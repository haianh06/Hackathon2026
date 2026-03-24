/**
 * V4L2 MMAP Camera Capture — Implementation
 *
 * DMA Pipeline:
 *   Camera Sensor → CSI-2 Receiver → DMA Engine → CMA Buffer (physical RAM)
 *                                                      ↓ mmap
 *                                                  Userspace pointer
 *
 * The CPU does NOT touch pixel data during capture — DMA handles the transfer.
 * mmap gives us a virtual address pointing to the same physical pages.
 */

#include "v4l2_capture.hpp"

#include <cerrno>
#include <cstdio>
#include <cstring>

#include <fcntl.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <linux/videodev2.h>

// Retry ioctl on EINTR
static int xioctl(int fd, unsigned long request, void* arg) {
    int r;
    do {
        r = ioctl(fd, request, arg);
    } while (r == -1 && errno == EINTR);
    return r;
}

V4L2Capture::V4L2Capture(const std::string& device, int width, int height, int num_bufs)
    : device_(device), req_w_(width), req_h_(height),
      num_bufs_(num_bufs > MAX_BUFFERS ? MAX_BUFFERS : (num_bufs < 2 ? 2 : num_bufs))
{
}

V4L2Capture::~V4L2Capture() {
    stop();
}

bool V4L2Capture::open() {
    // Open device
    fd_ = ::open(device_.c_str(), O_RDWR | O_NONBLOCK);
    if (fd_ < 0) {
        fprintf(stderr, "[V4L2] Cannot open %s: %s\n", device_.c_str(), strerror(errno));
        return false;
    }

    // Query capabilities
    struct v4l2_capability cap{};
    if (xioctl(fd_, VIDIOC_QUERYCAP, &cap) < 0) {
        fprintf(stderr, "[V4L2] QUERYCAP failed: %s\n", strerror(errno));
        close(fd_); fd_ = -1;
        return false;
    }

    if (!(cap.capabilities & V4L2_CAP_VIDEO_CAPTURE)) {
        fprintf(stderr, "[V4L2] %s is not a video capture device\n", device_.c_str());
        close(fd_); fd_ = -1;
        return false;
    }

    if (!(cap.capabilities & V4L2_CAP_STREAMING)) {
        fprintf(stderr, "[V4L2] %s does not support streaming I/O\n", device_.c_str());
        close(fd_); fd_ = -1;
        return false;
    }

    fprintf(stderr, "[V4L2] Device: %s (%s)\n", cap.card, cap.driver);

    // Try formats in preference order: BGR24 > RGB24 > YUYV > MJPEG
    static const uint32_t preferred_fmts[] = {
        V4L2_PIX_FMT_BGR24,
        V4L2_PIX_FMT_RGB24,
        V4L2_PIX_FMT_YUYV,
        V4L2_PIX_FMT_MJPEG,
    };

    bool fmt_set = false;
    for (uint32_t pf : preferred_fmts) {
        struct v4l2_format fmt{};
        fmt.type                = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        fmt.fmt.pix.width       = static_cast<uint32_t>(req_w_);
        fmt.fmt.pix.height      = static_cast<uint32_t>(req_h_);
        fmt.fmt.pix.pixelformat = pf;
        fmt.fmt.pix.field       = V4L2_FIELD_NONE;

        if (xioctl(fd_, VIDIOC_S_FMT, &fmt) == 0) {
            actual_w_  = static_cast<int>(fmt.fmt.pix.width);
            actual_h_  = static_cast<int>(fmt.fmt.pix.height);
            pixel_fmt_ = fmt.fmt.pix.pixelformat;
            fmt_set    = true;

            // Store fourcc string
            fmt_str_[0] = static_cast<char>(pixel_fmt_ & 0xFF);
            fmt_str_[1] = static_cast<char>((pixel_fmt_ >> 8) & 0xFF);
            fmt_str_[2] = static_cast<char>((pixel_fmt_ >> 16) & 0xFF);
            fmt_str_[3] = static_cast<char>((pixel_fmt_ >> 24) & 0xFF);
            fmt_str_[4] = '\0';

            fprintf(stderr, "[V4L2] Format: %s %dx%d\n", fmt_str_, actual_w_, actual_h_);
            break;
        }
    }

    if (!fmt_set) {
        fprintf(stderr, "[V4L2] No supported pixel format found\n");
        close(fd_); fd_ = -1;
        return false;
    }

    // Request MMAP buffers (DMA-capable, CMA region)
    struct v4l2_requestbuffers reqbuf{};
    reqbuf.count  = static_cast<uint32_t>(num_bufs_);
    reqbuf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    reqbuf.memory = V4L2_MEMORY_MMAP;

    if (xioctl(fd_, VIDIOC_REQBUFS, &reqbuf) < 0) {
        fprintf(stderr, "[V4L2] REQBUFS failed: %s\n", strerror(errno));
        close(fd_); fd_ = -1;
        return false;
    }

    allocated_bufs_ = static_cast<int>(reqbuf.count);
    fprintf(stderr, "[V4L2] Allocated %d MMAP buffers\n", allocated_bufs_);

    // Query and mmap each buffer
    for (int i = 0; i < allocated_bufs_; ++i) {
        struct v4l2_buffer buf{};
        buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index  = static_cast<uint32_t>(i);

        if (xioctl(fd_, VIDIOC_QUERYBUF, &buf) < 0) {
            fprintf(stderr, "[V4L2] QUERYBUF[%d] failed: %s\n", i, strerror(errno));
            stop();
            return false;
        }

        buffers_[i].length = buf.length;
        buffers_[i].start  = mmap(nullptr, buf.length,
                                   PROT_READ | PROT_WRITE, MAP_SHARED,
                                   fd_, buf.m.offset);

        if (buffers_[i].start == MAP_FAILED) {
            fprintf(stderr, "[V4L2] mmap[%d] failed: %s\n", i, strerror(errno));
            buffers_[i].start = nullptr;
            stop();
            return false;
        }
    }

    return true;
}

bool V4L2Capture::start() {
    if (fd_ < 0) return false;

    // Queue all buffers
    for (int i = 0; i < allocated_bufs_; ++i) {
        struct v4l2_buffer buf{};
        buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index  = static_cast<uint32_t>(i);

        if (xioctl(fd_, VIDIOC_QBUF, &buf) < 0) {
            fprintf(stderr, "[V4L2] QBUF[%d] failed: %s\n", i, strerror(errno));
            return false;
        }
    }

    // Start streaming
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd_, VIDIOC_STREAMON, &type) < 0) {
        fprintf(stderr, "[V4L2] STREAMON failed: %s\n", strerror(errno));
        return false;
    }

    streaming_ = true;
    fprintf(stderr, "[V4L2] Streaming started\n");
    return true;
}

void V4L2Capture::stop() {
    if (streaming_ && fd_ >= 0) {
        enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        xioctl(fd_, VIDIOC_STREAMOFF, &type);
        streaming_ = false;
    }

    // Unmap buffers
    for (int i = 0; i < allocated_bufs_; ++i) {
        if (buffers_[i].start && buffers_[i].start != MAP_FAILED) {
            munmap(buffers_[i].start, buffers_[i].length);
            buffers_[i].start = nullptr;
        }
    }
    allocated_bufs_ = 0;
    current_buf_ = -1;

    if (fd_ >= 0) {
        close(fd_);
        fd_ = -1;
    }
}

bool V4L2Capture::dequeue_frame(const uint8_t** data, size_t* size, int timeout_ms) {
    if (!streaming_) return false;

    // Wait for frame with poll()
    struct pollfd pfd{};
    pfd.fd     = fd_;
    pfd.events = POLLIN;

    int ret = poll(&pfd, 1, timeout_ms);
    if (ret <= 0) return false;  // timeout or error

    // Dequeue buffer (zero-copy: pointer directly into DMA-mapped memory)
    struct v4l2_buffer buf{};
    buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;

    if (xioctl(fd_, VIDIOC_DQBUF, &buf) < 0) {
        if (errno == EAGAIN) return false;
        fprintf(stderr, "[V4L2] DQBUF failed: %s\n", strerror(errno));
        return false;
    }

    current_buf_ = static_cast<int>(buf.index);
    *data = static_cast<const uint8_t*>(buffers_[current_buf_].start);
    *size = buf.bytesused;
    return true;
}

void V4L2Capture::return_frame() {
    if (current_buf_ < 0 || !streaming_) return;

    struct v4l2_buffer buf{};
    buf.type   = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    buf.memory = V4L2_MEMORY_MMAP;
    buf.index  = static_cast<uint32_t>(current_buf_);

    xioctl(fd_, VIDIOC_QBUF, &buf);
    current_buf_ = -1;
}

const char* V4L2Capture::format_str() const {
    return fmt_str_;
}
