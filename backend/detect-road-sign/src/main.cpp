/**
 * Road Sign Detection C++ Service
 *
 * Self-contained service that:
 *  1. Reads camera frames via V4L2 MMAP (DMA, zero-copy) or MJPEG HTTP fallback
 *  2. Runs ONNX Runtime inference (YOLOv8) with NEON-accelerated preprocessing
 *  3. POSTs detection results directly to Node.js backend
 *  4. Exposes HTTP control API (start/stop/health/detect_once)
 *
 * Raspberry Pi 5 Optimisations:
 *  - V4L2 MMAP: DMA buffers in CMA region, mapped to userspace. Camera→RAM without CPU.
 *  - CPU Core Pinning: Core 0 = system, Core 1 = camera I/O, Core 2-3 = inference
 *  - NEON SIMD: LD3 hardware deinterleave + vectorised normalisation for preprocessing
 *
 * Architecture:
 *   Camera (V4L2 DMA / MJPEG) → C++ NEON preprocess → ONNX inference → HTTP POST → Node.js
 */

#include "detector.hpp"
#include "v4l2_capture.hpp"

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <sched.h>
#include <pthread.h>

#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <linux/videodev2.h>

static std::atomic<bool> g_running{true};
static std::atomic<bool> g_detecting{false};
static std::mutex        g_frame_mutex;
static std::vector<uint8_t> g_latest_frame;     // shared latest JPEG frame (MJPEG mode)
static std::atomic<bool>    g_frame_ready{false};

// V4L2 mode: raw BGR frame shared between camera and detection threads
static std::mutex           g_raw_mutex;
static std::vector<uint8_t> g_raw_frame;         // raw BGR24 pixels
static int                  g_raw_w = 0, g_raw_h = 0;
static std::atomic<bool>    g_raw_ready{false};
static std::atomic<bool>    g_use_v4l2{false};   // true = V4L2, false = MJPEG HTTP

// V4L2 auto-fallback: camera URL for fallback when V4L2 fails
static std::string          g_mjpeg_url;

static void signal_handler(int) { g_running = false; g_detecting = false; }

// ─────────────────── CPU Core Pinning (pthread_setaffinity_np) ───────────────────
//
// Linux CFS scheduler migrates threads between cores for load balancing,
// causing cold-cache penalties (L1/L2 flush) on each migration.
//
// We pin threads to dedicated cores:
//   Core 0 — System (IRQs, daemons, OS housekeeping)
//   Core 1 — Camera I/O pipeline (V4L2 DQBUF / MJPEG recv)
//   Core 2 — ONNX inference thread (+ Core 3 via OMP)
//   Core 3 — ONNX inference threadpool
//
// Benefits:
//   - L1 Instruction Cache stays warm (same code path runs on same core)
//   - L1 Data Cache stays warm (frame buffers are always in the same L1)
//   - Core 2↔3 share L2 cache: preprocessing output from Core 1 is visible
//     via MESI cache coherency protocol with minimal latency

static void pin_thread_to_core(int core_id, const char* name) {
#ifdef __linux__
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(core_id, &cpuset);
    int rc = pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset);
    if (rc == 0) {
        fprintf(stderr, "[RoadSign] Pinned '%s' thread to Core %d\n", name, core_id);
    } else {
        fprintf(stderr, "[RoadSign] Warning: failed to pin '%s' to Core %d (rc=%d)\n",
                name, core_id, rc);
    }
#else
    (void)core_id; (void)name;
#endif
}

// ─────────────────────── HTTP helpers (client) ───────────────────────

/// Resolve hostname and connect; returns fd or -1
static int http_connect(const char* host, int port) {
    struct addrinfo hints{}, *res;
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_STREAM;

    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host, port_str, &hints, &res) != 0) return -1;

    int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd < 0) { freeaddrinfo(res); return -1; }

    // Set connect timeout 3s
    struct timeval tv = {3, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    if (connect(fd, res->ai_addr, res->ai_addrlen) < 0) {
        close(fd);
        freeaddrinfo(res);
        return -1;
    }
    freeaddrinfo(res);
    return fd;
}

/// POST JSON to backend
static bool http_post_json(const char* host, int port,
                            const char* path, const std::string& json) {
    int fd = http_connect(host, port);
    if (fd < 0) return false;

    char header[512];
    int hlen = snprintf(header, sizeof(header),
        "POST %s HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "\r\n",
        path, host, port, json.size());

    send(fd, header, hlen, MSG_NOSIGNAL);
    send(fd, json.c_str(), json.size(), MSG_NOSIGNAL);

    // Read response status (we just need to know it succeeded)
    char resp[256];
    ssize_t n = recv(fd, resp, sizeof(resp) - 1, 0);
    close(fd);

    if (n > 0) {
        resp[n] = '\0';
        return strstr(resp, "200") != nullptr || strstr(resp, "201") != nullptr;
    }
    return false;
}

// ─────────────────────── HTTP helpers (server) ───────────────────────

static bool read_exact(int fd, void* buf, size_t n) {
    size_t total = 0;
    auto* p = static_cast<uint8_t*>(buf);
    while (total < n) {
        ssize_t r = recv(fd, p + total, n - total, 0);
        if (r <= 0) return false;
        total += static_cast<size_t>(r);
    }
    return true;
}

struct HttpRequest {
    std::string method, path;
    size_t content_length = 0;
    bool valid = false;
};

static HttpRequest parse_request(int fd) {
    HttpRequest req;
    std::string hdr;
    hdr.reserve(2048);
    char c;
    while (hdr.size() < 8192) {
        if (recv(fd, &c, 1, 0) <= 0) return req;
        hdr += c;
        if (hdr.size() >= 4 && hdr.substr(hdr.size() - 4) == "\r\n\r\n") break;
    }
    auto le = hdr.find("\r\n");
    if (le == std::string::npos) return req;
    auto sp1 = hdr.find(' ');
    auto sp2 = hdr.find(' ', sp1 + 1);
    if (sp1 == std::string::npos || sp2 == std::string::npos) return req;
    req.method = hdr.substr(0, sp1);
    req.path   = hdr.substr(sp1 + 1, sp2 - sp1 - 1);
    std::string lower = hdr;
    for (auto& ch : lower) ch = static_cast<char>(std::tolower(ch));
    auto cl = lower.find("content-length:");
    if (cl != std::string::npos)
        req.content_length = std::strtoul(lower.c_str() + cl + 15, nullptr, 10);
    req.valid = true;
    return req;
}

static void send_response(int fd, int status, const char* ct,
                           const void* body, size_t len) {
    const char* st = (status == 200) ? "OK" : (status == 503 ? "Service Unavailable" : "Bad Request");
    char hdr[512];
    int hl = snprintf(hdr, sizeof(hdr),
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: %s\r\n"
        "Content-Length: %zu\r\n"
        "Connection: close\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n", status, st, ct, len);
    send(fd, hdr, hl, MSG_NOSIGNAL);
    if (body && len > 0) send(fd, body, len, MSG_NOSIGNAL);
}

// ─────────────────────── JSON builder ───────────────────────

static std::string detections_to_json(const std::vector<roadsign::Detection>& dets) {
    std::string json = "{\"detections\":[";
    for (size_t i = 0; i < dets.size(); ++i) {
        const auto& d = dets[i];
        char buf[256];
        snprintf(buf, sizeof(buf),
            "%s{\"class\":\"%s\",\"confidence\":%.3f,"
            "\"bbox\":[%.0f,%.0f,%.0f,%.0f]}",
            (i > 0) ? "," : "",
            d.class_name(), d.confidence, d.x1, d.y1, d.x2, d.y2);
        json += buf;
    }
    char tail[32];
    snprintf(tail, sizeof(tail), "],\"count\":%zu}", dets.size());
    json += tail;
    return json;
}

// ─────────────────────── Camera MJPEG reader thread ───────────────────────
// Reads multipart/x-mixed-replace MJPEG stream via raw HTTP sockets.
// No cv::VideoCapture / FFmpeg dependency — works reliably in slim containers.

struct UrlParts { std::string host, path; int port; };

static UrlParts parse_url(const std::string& url) {
    UrlParts u{"hardware", "/stream", 8765};
    std::string s = url;
    if (s.rfind("http://", 0) == 0) s = s.substr(7);
    auto slash = s.find('/');
    if (slash != std::string::npos) {
        u.path = s.substr(slash);
        s = s.substr(0, slash);
    }
    auto colon = s.find(':');
    if (colon != std::string::npos) {
        u.host = s.substr(0, colon);
        u.port = std::atoi(s.substr(colon + 1).c_str());
    } else {
        u.host = s;
    }
    return u;
}

static void camera_reader_thread(const std::string& mjpeg_url) {
    pin_thread_to_core(1, "camera-mjpeg");

    auto url = parse_url(mjpeg_url);
    fprintf(stderr, "[RoadSign] Camera reader: %s:%d%s\n",
            url.host.c_str(), url.port, url.path.c_str());

    while (g_running) {
        int fd = http_connect(url.host.c_str(), url.port);
        if (fd < 0) {
            fprintf(stderr, "[RoadSign] Cannot connect to camera at %s:%d, retry in 3s\n",
                    url.host.c_str(), url.port);
            std::this_thread::sleep_for(std::chrono::seconds(3));
            continue;
        }

        // Longer recv timeout for streaming
        struct timeval tv = {10, 0};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        // Send GET request
        char req[512];
        int rlen = snprintf(req, sizeof(req),
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
            "Accept: */*\r\nConnection: keep-alive\r\n\r\n",
            url.path.c_str(), url.host.c_str(), url.port);
        if (send(fd, req, rlen, MSG_NOSIGNAL) <= 0) {
            close(fd);
            std::this_thread::sleep_for(std::chrono::seconds(3));
            continue;
        }

        // Read response headers until \r\n\r\n
        std::string hdr;
        hdr.reserve(4096);
        char c;
        bool got_headers = false;
        while (hdr.size() < 8192) {
            if (recv(fd, &c, 1, 0) <= 0) break;
            hdr += c;
            if (hdr.size() >= 4 &&
                hdr.compare(hdr.size() - 4, 4, "\r\n\r\n") == 0) {
                got_headers = true;
                break;
            }
        }

        if (!got_headers || hdr.find("200") == std::string::npos) {
            fprintf(stderr, "[RoadSign] Camera stream error (no 200), retry in 3s\n");
            close(fd);
            std::this_thread::sleep_for(std::chrono::seconds(3));
            continue;
        }

        fprintf(stderr, "[RoadSign] Camera MJPEG stream connected\n");

        // Lambda: read one line (up to \r\n) from fd
        auto read_line = [&](std::string& line) -> bool {
            line.clear();
            char ch;
            while (line.size() < 2048) {
                ssize_t n = recv(fd, &ch, 1, 0);
                if (n <= 0) return false;
                line += ch;
                if (line.size() >= 2 &&
                    line.compare(line.size() - 2, 2, "\r\n") == 0) {
                    line.resize(line.size() - 2);
                    return true;
                }
            }
            return false;
        };

        // Read multipart/x-mixed-replace stream
        // Each part: --frame\r\n headers \r\n <JPEG bytes> \r\n
        while (g_running) {
            std::string line;
            size_t content_length = 0;
            bool found_cl = false;

            // Read part headers until empty line
            while (true) {
                if (!read_line(line)) goto disconnect;
                if (line.empty()) {
                    if (found_cl) break;  // end of part headers
                    continue;
                }
                // Parse Content-Length
                if (line.size() > 15) {
                    std::string lower = line;
                    for (auto& ch : lower) ch = static_cast<char>(std::tolower(ch));
                    auto pos = lower.find("content-length:");
                    if (pos != std::string::npos) {
                        content_length = std::strtoul(
                            line.c_str() + pos + 15, nullptr, 10);
                        found_cl = (content_length > 0);
                    }
                }
            }

            if (content_length == 0 || content_length > 10 * 1024 * 1024)
                continue;

            // Read exactly content_length bytes of JPEG data
            std::vector<uint8_t> jpeg(content_length);
            size_t total = 0;
            while (total < content_length) {
                ssize_t n = recv(fd, jpeg.data() + total,
                                 content_length - total, 0);
                if (n <= 0) goto disconnect;
                total += static_cast<size_t>(n);
            }

            // Store JPEG frame directly (no decode/re-encode needed)
            {
                std::lock_guard<std::mutex> lk(g_frame_mutex);
                g_latest_frame = std::move(jpeg);
                g_frame_ready = true;
            }
        }

        disconnect:
        close(fd);
        if (g_running) {
            fprintf(stderr, "[RoadSign] Camera disconnected, retry in 2s\n");
            std::this_thread::sleep_for(std::chrono::seconds(2));
        }
    }
    fprintf(stderr, "[RoadSign] Camera reader stopped\n");
}

// ─────────────────── V4L2 MMAP Camera Reader Thread ───────────────────
//
// DMA Pipeline: Camera Sensor → CSI-2 → DMA Engine → CMA Buffer → mmap → this thread
// CPU does NOT copy pixel data during capture. DMA controller handles the transfer.
// We get a pointer to physical RAM where the frame already lives.

static void v4l2_reader_thread(const std::string& device, int cap_w, int cap_h) {
    pin_thread_to_core(1, "camera-v4l2");

    fprintf(stderr, "[RoadSign] V4L2 reader: %s %dx%d\n", device.c_str(), cap_w, cap_h);

    // Try V4L2 once — if STREAMON fails (e.g. rp1-cfe needs libcamera pipeline),
    // fall back to MJPEG HTTP mode automatically.
    {
        V4L2Capture cam(device, cap_w, cap_h, 4);
        if (!cam.open() || !cam.start()) {
            fprintf(stderr, "[RoadSign] V4L2 STREAMON failed — RPi5 rp1-cfe requires libcamera pipeline\n");
            fprintf(stderr, "[RoadSign] Auto-fallback → MJPEG HTTP mode\n");
            cam.stop();
            g_use_v4l2 = false;
            // Run MJPEG reader on this same thread
            camera_reader_thread(g_mjpeg_url);
            return;
        }

        fprintf(stderr, "[RoadSign] V4L2 streaming: %s %dx%d\n",
                cam.format_str(), cam.width(), cam.height());

        while (g_running) {
            const uint8_t* data = nullptr;
            size_t size = 0;

            if (!cam.dequeue_frame(&data, &size, 1000)) {
                continue;  // timeout, try again
            }

            uint32_t pf = cam.pixel_format();

            if (pf == V4L2_PIX_FMT_BGR24) {
                std::lock_guard<std::mutex> lk(g_raw_mutex);
                g_raw_frame.assign(data, data + size);
                g_raw_w = cam.width();
                g_raw_h = cam.height();
                g_raw_ready = true;
            } else if (pf == V4L2_PIX_FMT_RGB24) {
                int npix = cam.width() * cam.height();
                std::lock_guard<std::mutex> lk(g_raw_mutex);
                g_raw_frame.resize(npix * 3);
                for (int i = 0; i < npix * 3; i += 3) {
                    g_raw_frame[i + 0] = data[i + 2];
                    g_raw_frame[i + 1] = data[i + 1];
                    g_raw_frame[i + 2] = data[i + 0];
                }
                g_raw_w = cam.width();
                g_raw_h = cam.height();
                g_raw_ready = true;
            } else if (pf == V4L2_PIX_FMT_YUYV) {
                cv::Mat yuyv(cam.height(), cam.width(), CV_8UC2,
                             const_cast<uint8_t*>(data));
                cv::Mat bgr;
                cv::cvtColor(yuyv, bgr, cv::COLOR_YUV2BGR_YUYV);
                std::lock_guard<std::mutex> lk(g_raw_mutex);
                g_raw_frame.assign(bgr.data, bgr.data + bgr.total() * bgr.elemSize());
                g_raw_w = bgr.cols;
                g_raw_h = bgr.rows;
                g_raw_ready = true;
            } else if (pf == V4L2_PIX_FMT_MJPEG) {
                std::lock_guard<std::mutex> lk(g_frame_mutex);
                g_latest_frame.assign(data, data + size);
                g_frame_ready = true;
            }

            cam.return_frame();
        }

        cam.stop();
    }

    // Release frame buffers on exit
    {
        std::lock_guard<std::mutex> lk(g_raw_mutex);
        g_raw_frame.clear();
        g_raw_frame.shrink_to_fit();
        g_raw_ready = false;
    }

    fprintf(stderr, "[RoadSign] V4L2 reader stopped, resources released\n");
}

// ─────────────────────── Detection loop thread ───────────────────────

static void detection_loop_thread(roadsign::Detector& detector,
                                   const char* backend_host, int backend_port,
                                   int detect_interval_ms) {
    // Pin inference to Core 2 (Core 3 used by ONNX Runtime threadpool via OMP)
    pin_thread_to_core(2, "inference");

    fprintf(stderr, "[RoadSign] Detection loop ready (interval=%dms, mode=%s)\n",
            detect_interval_ms, g_use_v4l2.load() ? "V4L2" : "MJPEG");

    while (g_running) {
        if (!g_detecting) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            continue;
        }

        // Check for available frame based on mode
        bool use_raw = g_use_v4l2.load();
        bool has_raw_frame = use_raw && g_raw_ready;
        bool has_jpeg_frame = !use_raw && g_frame_ready;

        if (!has_raw_frame && !has_jpeg_frame) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }

        auto t0 = std::chrono::steady_clock::now();
        std::vector<roadsign::Detection> dets;

        if (has_raw_frame) {
            // ── V4L2 path: raw BGR frame → detect_raw() (no JPEG decode) ──
            std::vector<uint8_t> frame_copy;
            int fw, fh;
            {
                std::lock_guard<std::mutex> lk(g_raw_mutex);
                frame_copy = g_raw_frame;
                fw = g_raw_w;
                fh = g_raw_h;
            }
            if (!frame_copy.empty()) {
                dets = detector.detect_raw(frame_copy.data(), fw, fh);
            }
        } else {
            // ── MJPEG path: JPEG frame → detect() (with imdecode) ──
            std::vector<uint8_t> frame_copy;
            {
                std::lock_guard<std::mutex> lk(g_frame_mutex);
                frame_copy = g_latest_frame;
            }
            if (!frame_copy.empty()) {
                dets = detector.detect(frame_copy.data(), frame_copy.size());
            }
        }

        auto t1 = std::chrono::steady_clock::now();
        int ms = static_cast<int>(
            std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count());

        // Build JSON result
        std::string json = detections_to_json(dets);

        // Add timestamp and inference time
        auto now = std::chrono::system_clock::now();
        auto epoch = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()).count();

        char payload[4096];
        snprintf(payload, sizeof(payload),
            "{\"detections\":%s,\"count\":%zu,\"timestamp\":%.3f,\"inference_ms\":%d}",
            json.substr(json.find('['), json.rfind(']') - json.find('[') + 1).c_str(),
            dets.size(),
            static_cast<double>(epoch) / 1000.0,
            ms);

        // POST to Node.js backend
        bool sent = http_post_json(backend_host, backend_port,
                                    "/api/hardware/sign-detections", payload);

        if (dets.size() > 0) {
            fprintf(stderr, "[RoadSign] %zu detection(s) in %dms, sent=%s\n",
                    dets.size(), ms, sent ? "ok" : "fail");
        }

        // Rate limit: wait at least detect_interval_ms between inferences
        auto elapsed = std::chrono::steady_clock::now() - t0;
        auto remaining = std::chrono::milliseconds(detect_interval_ms) - elapsed;
        if (remaining.count() > 0) {
            std::this_thread::sleep_for(remaining);
        }
    }

    fprintf(stderr, "[RoadSign] Detection loop stopped\n");

    // Release frame buffers held by detection thread
    detector.release_buffers();
}

// ─────────────────────── Control HTTP server ───────────────────────

static void handle_connection(int client_fd, roadsign::Detector& detector,
                               const char* backend_host, int backend_port) {
    auto req = parse_request(client_fd);
    if (!req.valid) { close(client_fd); return; }

    // GET /health
    if (req.method == "GET" && req.path == "/health") {
        std::string body = detector.is_ready()
            ? "{\"status\":\"ok\",\"ready\":true,\"detecting\":"
              + std::string(g_detecting ? "true" : "false") + "}"
            : "{\"status\":\"error\",\"ready\":false}";
        send_response(client_fd, detector.is_ready() ? 200 : 503,
                      "application/json", body.c_str(), body.size());
        close(client_fd);
        return;
    }

    // POST /start — start continuous detection
    if (req.method == "POST" && req.path == "/start") {
        g_detecting = true;
        const char* body = "{\"status\":\"started\",\"detecting\":true}";
        send_response(client_fd, 200, "application/json", body, strlen(body));
        fprintf(stderr, "[RoadSign] Detection STARTED\n");
        close(client_fd);
        return;
    }

    // POST /stop — stop continuous detection
    if (req.method == "POST" && req.path == "/stop") {
        g_detecting = false;
        const char* body = "{\"status\":\"stopped\",\"detecting\":false}";
        send_response(client_fd, 200, "application/json", body, strlen(body));
        fprintf(stderr, "[RoadSign] Detection STOPPED\n");
        close(client_fd);
        return;
    }

    // POST /detect — single-frame detection (body = JPEG or empty to use latest)
    if (req.method == "POST" && req.path == "/detect") {
        std::vector<uint8_t> frame;
        if (req.content_length > 0 && req.content_length <= 10 * 1024 * 1024) {
            frame.resize(req.content_length);
            if (!read_exact(client_fd, frame.data(), req.content_length)) {
                close(client_fd);
                return;
            }
        } else {
            // Use latest camera frame
            std::lock_guard<std::mutex> lk(g_frame_mutex);
            frame = g_latest_frame;
        }

        if (frame.empty()) {
            const char* err = "{\"error\":\"no frame available\"}";
            send_response(client_fd, 400, "application/json", err, strlen(err));
            close(client_fd);
            return;
        }

        auto dets = detector.detect(frame.data(), frame.size());
        std::string json = detections_to_json(dets);
        send_response(client_fd, 200, "application/json", json.c_str(), json.size());
        close(client_fd);
        return;
    }

    // POST /detect_once — single detection, result POSTed to backend
    if (req.method == "POST" && req.path == "/detect_once") {
        std::vector<uint8_t> frame;
        {
            std::lock_guard<std::mutex> lk(g_frame_mutex);
            frame = g_latest_frame;
        }

        if (frame.empty()) {
            const char* err = "{\"error\":\"no frame available\"}";
            send_response(client_fd, 400, "application/json", err, strlen(err));
            close(client_fd);
            return;
        }

        auto dets = detector.detect(frame.data(), frame.size());
        std::string json = detections_to_json(dets);

        // POST to backend
        auto now = std::chrono::system_clock::now();
        auto epoch = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()).count();
        char payload[4096];
        snprintf(payload, sizeof(payload),
            "{\"detections\":%s,\"count\":%zu,\"timestamp\":%.3f,\"single\":true}",
            json.substr(json.find('['), json.rfind(']') - json.find('[') + 1).c_str(),
            dets.size(), static_cast<double>(epoch) / 1000.0);
        http_post_json(backend_host, backend_port,
                       "/api/hardware/sign-detect-result", payload);

        send_response(client_fd, 200, "application/json", json.c_str(), json.size());
        close(client_fd);
        return;
    }

    const char* err = "{\"error\":\"not found\"}";
    send_response(client_fd, 400, "application/json", err, strlen(err));
    close(client_fd);
}

// ─────────────────────── Main ───────────────────────

int main() {
    // Configuration from environment
    roadsign::Config cfg;
    if (const char* v = std::getenv("MODEL_PATH"))
        cfg.model_path = v;
    else
        cfg.model_path = "/app/detect-road-sign/best_yolo.onnx";

    if (const char* v = std::getenv("CONF_THRESHOLD"))
        cfg.conf_threshold = std::strtof(v, nullptr);
    if (const char* v = std::getenv("NMS_IOU_THRESHOLD"))
        cfg.nms_iou_threshold = std::strtof(v, nullptr);
    if (const char* v = std::getenv("NUM_THREADS"))
        cfg.num_threads = std::atoi(v);
    if (const char* v = std::getenv("INPUT_SIZE"))
        cfg.input_width = cfg.input_height = std::atoi(v);
    if (const char* v = std::getenv("USE_FP16"))
        cfg.use_fp16 = (std::string(v) == "true" || std::string(v) == "1");

    int port = 9001;
    if (const char* v = std::getenv("DETECT_PORT"))
        port = std::atoi(v);

    // Camera MJPEG stream URL (MJPEG mode + V4L2 fallback target)
    std::string camera_url = "http://hardware:8765/stream";
    if (const char* v = std::getenv("CAMERA_MJPEG_URL"))
        camera_url = v;
    g_mjpeg_url = camera_url;  // store for V4L2 auto-fallback

    // V4L2 mode configuration
    std::string camera_mode = "mjpeg";  // default
    if (const char* v = std::getenv("CAMERA_MODE"))
        camera_mode = v;

    std::string v4l2_device = "/dev/video0";
    if (const char* v = std::getenv("V4L2_DEVICE"))
        v4l2_device = v;

    int v4l2_width = 640, v4l2_height = 480;
    if (const char* v = std::getenv("V4L2_WIDTH"))
        v4l2_width = std::atoi(v);
    if (const char* v = std::getenv("V4L2_HEIGHT"))
        v4l2_height = std::atoi(v);

    g_use_v4l2 = (camera_mode == "v4l2");

    // Backend callback
    std::string backend_host = "backend";
    int backend_port = 5000;
    if (const char* v = std::getenv("BACKEND_HOST"))
        backend_host = v;
    if (const char* v = std::getenv("BACKEND_PORT"))
        backend_port = std::atoi(v);

    // Detection interval (ms between inferences)
    int detect_interval = 300;  // ~3 FPS detection
    if (const char* v = std::getenv("DETECT_INTERVAL_MS"))
        detect_interval = std::atoi(v);

    fprintf(stderr, "[RoadSign] ═══════════════════════════════════════════════\n");
    fprintf(stderr, "[RoadSign] C++ Road Sign Detection Service (Optimised)\n");
    fprintf(stderr, "[RoadSign] ───────────────────────────────────────────────\n");
    fprintf(stderr, "[RoadSign] Model:     %s\n", cfg.model_path.c_str());
    fprintf(stderr, "[RoadSign] Conf=%.2f NMS=%.2f Threads=%d Input=%dx%d\n",
            cfg.conf_threshold, cfg.nms_iou_threshold,
            cfg.num_threads, cfg.input_width, cfg.input_height);
    if (g_use_v4l2) {
        fprintf(stderr, "[RoadSign] Camera:    V4L2 MMAP → %s %dx%d\n",
                v4l2_device.c_str(), v4l2_width, v4l2_height);
        fprintf(stderr, "[RoadSign] Pipeline:  DMA→MMAP→NEON→ONNX (zero JPEG decode)\n");
    } else {
        fprintf(stderr, "[RoadSign] Camera:    MJPEG HTTP → %s\n", camera_url.c_str());
        fprintf(stderr, "[RoadSign] Pipeline:  HTTP→JPEG decode→NEON→ONNX\n");
    }
    fprintf(stderr, "[RoadSign] Backend:   %s:%d\n", backend_host.c_str(), backend_port);
    fprintf(stderr, "[RoadSign] Interval:  %dms\n", detect_interval);
    fprintf(stderr, "[RoadSign] Cores:     0=sys 1=camera 2-3=inference\n");
    fprintf(stderr, "[RoadSign] Precision: %s model + %s preprocessing\n",
            cfg.use_fp16 ? "FP16" : "FP32", cfg.use_fp16 ? "FP16 NEON" : "FP32 NEON");
    fprintf(stderr, "[RoadSign] SIMD:      NEON LD3 deinterleave + vectorised norm\n");
    fprintf(stderr, "[RoadSign] ═══════════════════════════════════════════════\n");

    // Load model
    roadsign::Detector detector(cfg);
    if (!detector.is_ready()) {
        fprintf(stderr, "[RoadSign] FATAL: Model failed to load!\n");
        return 1;
    }
    fprintf(stderr, "[RoadSign] Model loaded successfully\n");

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    // Start camera reader thread (V4L2 or MJPEG based on config)
    std::thread cam_thread;
    if (g_use_v4l2) {
        cam_thread = std::thread(v4l2_reader_thread, v4l2_device, v4l2_width, v4l2_height);
    } else {
        cam_thread = std::thread(camera_reader_thread, camera_url);
    }

    // Start detection loop thread
    std::thread det_thread(detection_loop_thread,
                            std::ref(detector),
                            backend_host.c_str(), backend_port,
                            detect_interval);

    // HTTP control server
    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) { perror("socket"); return 1; }
    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(static_cast<uint16_t>(port));

    if (bind(server_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("bind"); close(server_fd); return 1;
    }
    if (listen(server_fd, 8) < 0) {
        perror("listen"); close(server_fd); return 1;
    }

    fprintf(stderr, "[RoadSign] Control HTTP server on port %d\n", port);

    while (g_running) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(server_fd, &fds);
        struct timeval tv = {1, 0};
        if (select(server_fd + 1, &fds, nullptr, nullptr, &tv) <= 0) continue;

        struct sockaddr_in client_addr{};
        socklen_t client_len = sizeof(client_addr);
        int client_fd = accept(server_fd,
                               reinterpret_cast<sockaddr*>(&client_addr),
                               &client_len);
        if (client_fd < 0) continue;

        handle_connection(client_fd, detector, backend_host.c_str(), backend_port);
    }

    close(server_fd);

    g_running = false;
    g_detecting = false;
    cam_thread.join();
    det_thread.join();

    // Release all shared frame buffers
    {
        std::lock_guard<std::mutex> lk(g_frame_mutex);
        g_latest_frame.clear();
        g_latest_frame.shrink_to_fit();
    }
    {
        std::lock_guard<std::mutex> lk(g_raw_mutex);
        g_raw_frame.clear();
        g_raw_frame.shrink_to_fit();
    }

    fprintf(stderr, "[RoadSign] All resources released, service stopped\n");
    return 0;
}
