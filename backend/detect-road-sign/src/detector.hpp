/**
 * Road Sign Detector — High-performance ONNX Runtime C++ inference
 *
 * Model: YOLOv8 (Ultralytics export) — ONNX format
 * Input:  [1, 3, 640, 640] float32 (BGR → RGB, normalized 0-1)
 * Output: [1, 8, 8400]  → 8 channels × 8400 anchors (transposed)
 *         Channels: [cx, cy, w, h, score_c0..score_c3] pixel-scale
 *
 * Classes: {0: go_straight_sign, 1: park_sign,
 *           2: turn_left_sign,   3: turn_right_sign}
 *
 * Optimizations for Raspberry Pi 5 CPU:
 *  - ONNX Runtime with intra_op=4 threads (Cortex-A76 quad-core)
 *  - Letterbox pre-processing avoids distortion
 *  - NMS with IoU threshold to deduplicate
 *  - Zero-copy where possible
 */

#pragma once

#include <onnxruntime_cxx_api.h>
#include <opencv2/opencv.hpp>

#include <array>
#include <cmath>
#include <string>
#include <vector>

namespace roadsign {

/// Single detection result
struct Detection {
    int   class_id;          // 0-3
    float confidence;        // 0.0 – 1.0
    float x1, y1, x2, y2;   // pixel coords in original image
    const char* class_name() const {
        static const char* names[] = {
            "go_straight_sign", "park_sign",
            "turn_left_sign",   "turn_right_sign"
        };
        return (class_id >= 0 && class_id < 4) ? names[class_id] : "unknown";
    }
};

/// Configuration for the detector
struct Config {
    std::string model_path  = "best_yolo.onnx";
    float conf_threshold    = 0.5f;
    float nms_iou_threshold = 0.45f;
    int   input_width       = 640;
    int   input_height      = 640;
    int   num_threads       = 4;      // match Pi 5 quad-core
    bool  use_fp16          = false;   // FP16 model + NEON FP16 preprocessing
};

class Detector {
public:
    explicit Detector(const Config& cfg = {});

    /// Returns true if model loaded successfully
    bool is_ready() const { return ready_; }

    /// Release pre-allocated buffers to free memory when idle
    void release_buffers();

    /**
     * Run inference on a raw JPEG buffer.
     * @param jpeg_data   pointer to JPEG bytes
     * @param jpeg_len    length of JPEG data
     * @return vector of detections above confidence threshold
     */
    std::vector<Detection> detect(const uint8_t* jpeg_data, size_t jpeg_len);

    /**
     * Run inference on a raw BGR frame (zero JPEG overhead).
     * Used with V4L2 MMAP capture for minimum latency.
     * @param bgr_data   pointer to BGR24 pixel data
     * @param width      frame width
     * @param height     frame height
     * @return vector of detections above confidence threshold
     */
    std::vector<Detection> detect_raw(const uint8_t* bgr_data, int width, int height);

    /**
     * Detect + draw bounding boxes. Returns annotated JPEG.
     * @param jpeg_data  input JPEG
     * @param jpeg_len   input length
     * @param out_jpeg   output annotated JPEG buffer
     * @param detections output detection list
     */
    void detect_annotated(const uint8_t* jpeg_data, size_t jpeg_len,
                          std::vector<uint8_t>& out_jpeg,
                          std::vector<Detection>& detections);

private:
    // Pre-process: decode JPEG, letterbox resize, HWC→CHW, normalise
    void preprocess(const uint8_t* jpeg_data, size_t jpeg_len,
                    float& scale, int& pad_x, int& pad_y,
                    cv::Mat& original);

    // Pre-process from raw BGR Mat (skip JPEG decode), uses NEON SIMD
    void preprocess_raw(const cv::Mat& bgr_frame,
                        float& scale, int& pad_x, int& pad_y);

    // Pre-process from raw BGR Mat → FP16 output (ARMv8.2-A +fp16)
    void preprocess_raw_fp16(const cv::Mat& bgr_frame,
                              float& scale, int& pad_x, int& pad_y);

    // Letterbox resize (shared between FP32 and FP16 paths)
    cv::Mat letterbox(const cv::Mat& input, float& scale, int& pad_x, int& pad_y);

    // Run inference from prepared blob (FP32 or FP16), return detections
    std::vector<Detection> run_inference(float scale, int pad_x, int pad_y,
                                          int orig_w, int orig_h);

    // Read output tensor as FP32 (auto-converts if model outputs FP16)
    const float* read_output_fp32(const Ort::Value& tensor, size_t total_elements);

    // Post-process: parse transposed YOLOv8 output, apply NMS, scale back
    std::vector<Detection> postprocess(const float* output, int num_anchors,
                                       float scale, int pad_x, int pad_y,
                                       int orig_w, int orig_h);

    // IoU for NMS
    static float iou(const Detection& a, const Detection& b);

    // NMS
    static std::vector<Detection> nms(std::vector<Detection>& dets, float iou_thr);

    // Draw detections onto frame
    static void draw(cv::Mat& frame, const std::vector<Detection>& dets);

    Config                        cfg_;
    bool                          ready_ = false;
    Ort::Env                      env_;
    Ort::SessionOptions           session_opts_;
    std::unique_ptr<Ort::Session> session_;

    // Pre-allocated input/output names (stored as string for lifetime)
    std::string input_name_  = "images";
    std::string output_name_ = "output0";

    // Pre-allocated blobs (avoid per-frame allocation)
    std::vector<float>    blob_fp32_;
    std::vector<uint16_t> blob_fp16_;
    std::vector<float>    fp32_output_buf_;  // for FP16→FP32 output conversion
};

} // namespace roadsign
