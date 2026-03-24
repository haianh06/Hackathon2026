/**
 * V4L2 MMAP Camera Capture — Zero-copy DMA pipeline for Raspberry Pi 5
 *
 * Uses kernel DMA buffers (CMA) mapped directly into userspace via mmap.
 * Eliminates HTTP/MJPEG overhead and JPEG decode latency.
 *
 * Buffer lifecycle:
 *   VIDIOC_REQBUFS  → Allocate DMA buffers in CMA region
 *   VIDIOC_QBUF     → Queue empty buffer for camera DMA write
 *   VIDIOC_DQBUF    → Dequeue filled buffer (zero-copy, already in our address space)
 *   mmap            → Kernel ↔ Userspace shared memory (no memcpy)
 */

#pragma once

#include <cstdint>
#include <string>

class V4L2Capture {
public:
    /**
     * @param device    V4L2 device path (e.g. /dev/video0)
     * @param width     Desired capture width
     * @param height    Desired capture height
     * @param num_bufs  Number of MMAP ring buffers (2-8, default 4)
     */
    V4L2Capture(const std::string& device, int width, int height, int num_bufs = 4);
    ~V4L2Capture();

    // Non-copyable
    V4L2Capture(const V4L2Capture&) = delete;
    V4L2Capture& operator=(const V4L2Capture&) = delete;

    /// Open device, negotiate format, allocate MMAP buffers
    bool open();

    /// Start streaming (VIDIOC_STREAMON)
    bool start();

    /// Stop streaming and release resources
    void stop();

    /// Dequeue a filled frame. Returns pointer to MMAP buffer (zero-copy).
    /// Buffer is valid until return_frame() is called.
    /// @param data     [out] Pointer to frame data
    /// @param size     [out] Frame data size in bytes
    /// @param timeout_ms  Timeout in milliseconds (-1 = block forever)
    /// @return true if frame available
    bool dequeue_frame(const uint8_t** data, size_t* size, int timeout_ms = 1000);

    /// Return the current frame buffer to the driver queue
    void return_frame();

    bool is_open() const { return fd_ >= 0; }
    int  width()  const { return actual_w_; }
    int  height() const { return actual_h_; }
    uint32_t pixel_format() const { return pixel_fmt_; }

    /// Human-readable fourcc string
    const char* format_str() const;

private:
    std::string device_;
    int req_w_, req_h_;
    int actual_w_ = 0, actual_h_ = 0;
    uint32_t pixel_fmt_ = 0;
    int fd_ = -1;
    int num_bufs_;

    static constexpr int MAX_BUFFERS = 8;
    struct Buffer {
        void*  start  = nullptr;
        size_t length = 0;
    };
    Buffer buffers_[MAX_BUFFERS];
    int    allocated_bufs_ = 0;
    int    current_buf_    = -1;  // index of dequeued buffer, or -1
    bool   streaming_      = false;

    char fmt_str_[5] = {};
};
