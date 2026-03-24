/**
 * Road Sign Detector — ONNX Runtime C++ implementation
 *
 * YOLOv8 model — optimised for Raspberry Pi 5 (Cortex-A76, 4 cores):
 *  - Letterbox preserves aspect ratio → no distortion
 *  - NEON SIMD for BGR→CHW conversion (LD3 deinterleave + vectorised normalisation)
 *  - Single allocation for CHW blob
 *  - ONNX Runtime session graph optimisations (ORT_ENABLE_ALL)
 *  - NMS with greedy IoU suppression
 *  - Zero-copy raw frame path for V4L2 MMAP capture
 */

#include "detector.hpp"
#include "neon_preprocess.h"

#include <algorithm>
#include <cstring>
#include <numeric>

namespace roadsign {

// ─────────────────────── Constructor ───────────────────────

Detector::Detector(const Config& cfg)
    : cfg_(cfg),
      env_(ORT_LOGGING_LEVEL_WARNING, "roadsign")
{
    // Session options — optimised for CPU
    session_opts_.SetIntraOpNumThreads(cfg_.num_threads);
    session_opts_.SetInterOpNumThreads(1);
    session_opts_.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);

    // Disable memory pattern for dynamic shapes
    session_opts_.DisableMemPattern();

    // Execution mode: sequential is better for single-image inference
    session_opts_.SetExecutionMode(ExecutionMode::ORT_SEQUENTIAL);

    try {
        session_ = std::make_unique<Ort::Session>(
            env_, cfg_.model_path.c_str(), session_opts_);
        ready_ = true;
    } catch (const Ort::Exception& e) {
        fprintf(stderr, "[RoadSign] Failed to load model: %s\n", e.what());
        ready_ = false;
    }

    // Pre-allocate blob buffers (avoid per-frame heap allocation)
    const int img_size = cfg_.input_width * cfg_.input_height;
    if (cfg_.use_fp16) {
        blob_fp16_.resize(3 * img_size);
    } else {
        blob_fp32_.resize(3 * img_size);
    }
}

void Detector::release_buffers() {
    blob_fp32_.clear();
    blob_fp32_.shrink_to_fit();
    blob_fp16_.clear();
    blob_fp16_.shrink_to_fit();
    fp32_output_buf_.clear();
    fp32_output_buf_.shrink_to_fit();
}

// ─────────────────────── Pre-processing ───────────────────────

void Detector::preprocess(const uint8_t* jpeg_data, size_t jpeg_len,
                          float& scale, int& pad_x, int& pad_y,
                          cv::Mat& original)
{
    // Decode JPEG
    cv::Mat raw(1, static_cast<int>(jpeg_len), CV_8UC1,
                const_cast<uint8_t*>(jpeg_data));
    original = cv::imdecode(raw, cv::IMREAD_COLOR);

    // Delegate to raw preprocessor (FP16 or FP32 path)
    if (cfg_.use_fp16)
        preprocess_raw_fp16(original, scale, pad_x, pad_y);
    else
        preprocess_raw(original, scale, pad_x, pad_y);
}

// ─────────────────────── Letterbox (shared) ───────────────────────

cv::Mat Detector::letterbox(const cv::Mat& input, float& scale,
                            int& pad_x, int& pad_y)
{
    const int orig_h = input.rows;
    const int orig_w = input.cols;
    const int tgt_w  = cfg_.input_width;
    const int tgt_h  = cfg_.input_height;

    scale = std::min(static_cast<float>(tgt_w) / orig_w,
                     static_cast<float>(tgt_h) / orig_h);
    int new_w = static_cast<int>(orig_w * scale);
    int new_h = static_cast<int>(orig_h * scale);
    pad_x = (tgt_w - new_w) / 2;
    pad_y = (tgt_h - new_h) / 2;

    cv::Mat resized;
    cv::resize(input, resized, cv::Size(new_w, new_h), 0, 0, cv::INTER_LINEAR);

    cv::Mat padded(tgt_h, tgt_w, CV_8UC3, cv::Scalar(114, 114, 114));
    resized.copyTo(padded(cv::Rect(pad_x, pad_y, new_w, new_h)));
    return padded;
}

void Detector::preprocess_raw(const cv::Mat& bgr_frame,
                               float& scale, int& pad_x, int& pad_y)
{
    cv::Mat padded = letterbox(bgr_frame, scale, pad_x, pad_y);

    const int img_size = cfg_.input_height * cfg_.input_width;
    blob_fp32_.resize(3 * img_size);

    float* dst_r = blob_fp32_.data();
    float* dst_g = blob_fp32_.data() + img_size;
    float* dst_b = blob_fp32_.data() + 2 * img_size;
    bgr_to_chw_neon(padded.data, dst_r, dst_g, dst_b, img_size);
}

void Detector::preprocess_raw_fp16(const cv::Mat& bgr_frame,
                                    float& scale, int& pad_x, int& pad_y)
{
    cv::Mat padded = letterbox(bgr_frame, scale, pad_x, pad_y);

    const int img_size = cfg_.input_height * cfg_.input_width;
    blob_fp16_.resize(3 * img_size);

    uint16_t* dst_r = blob_fp16_.data();
    uint16_t* dst_g = blob_fp16_.data() + img_size;
    uint16_t* dst_b = blob_fp16_.data() + 2 * img_size;
    bgr_to_chw_neon_fp16(padded.data, dst_r, dst_g, dst_b, img_size);
}

// ─────────────────────── Post-processing ───────────────────────

std::vector<Detection> Detector::postprocess(
    const float* output, int num_anchors,
    float scale, int pad_x, int pad_y,
    int orig_w, int orig_h)
{
    // YOLOv8 transposed output: [batch, 8, num_anchors]
    // Row 0=cx, 1=cy, 2=w, 3=h, 4..7=class scores
    // Coordinates are pixel-scale (0–input_size)
    constexpr int NUM_CLASSES = 4;

    std::vector<Detection> candidates;
    candidates.reserve(64);

    for (int j = 0; j < num_anchors; ++j) {
        // Find best class
        int   best_cls   = 0;
        float best_score = output[4 * num_anchors + j];
        for (int c = 1; c < NUM_CLASSES; ++c) {
            float s = output[(4 + c) * num_anchors + j];
            if (s > best_score) {
                best_score = s;
                best_cls   = c;
            }
        }

        if (best_score < cfg_.conf_threshold) continue;

        // Decode bbox — YOLOv8 outputs pixel-scale cxcywh
        float cx = output[0 * num_anchors + j];
        float cy = output[1 * num_anchors + j];
        float w  = output[2 * num_anchors + j];
        float h  = output[3 * num_anchors + j];

        // Remove padding, un-scale to original image
        float x1 = (cx - w * 0.5f - pad_x) / scale;
        float y1 = (cy - h * 0.5f - pad_y) / scale;
        float x2 = (cx + w * 0.5f - pad_x) / scale;
        float y2 = (cy + h * 0.5f - pad_y) / scale;

        // Clamp to image bounds
        x1 = std::max(0.0f, std::min(x1, static_cast<float>(orig_w)));
        y1 = std::max(0.0f, std::min(y1, static_cast<float>(orig_h)));
        x2 = std::max(0.0f, std::min(x2, static_cast<float>(orig_w)));
        y2 = std::max(0.0f, std::min(y2, static_cast<float>(orig_h)));

        if (x2 - x1 < 2.0f || y2 - y1 < 2.0f) continue;

        candidates.push_back({best_cls, best_score, x1, y1, x2, y2});
    }

    return nms(candidates, cfg_.nms_iou_threshold);
}

// ─────────────────────── NMS ───────────────────────

float Detector::iou(const Detection& a, const Detection& b) {
    float ix1 = std::max(a.x1, b.x1);
    float iy1 = std::max(a.y1, b.y1);
    float ix2 = std::min(a.x2, b.x2);
    float iy2 = std::min(a.y2, b.y2);

    float inter = std::max(0.0f, ix2 - ix1) * std::max(0.0f, iy2 - iy1);
    float area_a = (a.x2 - a.x1) * (a.y2 - a.y1);
    float area_b = (b.x2 - b.x1) * (b.y2 - b.y1);
    float union_area = area_a + area_b - inter;

    return (union_area > 0.0f) ? (inter / union_area) : 0.0f;
}

std::vector<Detection> Detector::nms(std::vector<Detection>& dets, float iou_thr) {
    // Sort descending by confidence
    std::sort(dets.begin(), dets.end(),
              [](const Detection& a, const Detection& b) {
                  return a.confidence > b.confidence;
              });

    std::vector<bool> suppressed(dets.size(), false);
    std::vector<Detection> result;
    result.reserve(dets.size());

    for (size_t i = 0; i < dets.size(); ++i) {
        if (suppressed[i]) continue;
        result.push_back(dets[i]);
        for (size_t j = i + 1; j < dets.size(); ++j) {
            if (!suppressed[j] && dets[i].class_id == dets[j].class_id
                && iou(dets[i], dets[j]) > iou_thr) {
                suppressed[j] = true;
            }
        }
    }
    return result;
}

// ─────────────────────── Inference (shared FP32/FP16) ───────────────────────

std::vector<Detection> Detector::run_inference(float scale, int pad_x, int pad_y,
                                                int orig_w, int orig_h)
{
    std::array<int64_t, 4> shape = {1, 3, cfg_.input_height, cfg_.input_width};
    auto memory_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);

    Ort::Value input_tensor{nullptr};
    if (cfg_.use_fp16) {
        input_tensor = Ort::Value::CreateTensor(
            memory_info, blob_fp16_.data(),
            blob_fp16_.size() * sizeof(uint16_t),
            shape.data(), shape.size(),
            ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT16);
    } else {
        input_tensor = Ort::Value::CreateTensor<float>(
            memory_info, blob_fp32_.data(), blob_fp32_.size(),
            shape.data(), shape.size());
    }

    const char* input_names[]  = { input_name_.c_str() };
    const char* output_names[] = { output_name_.c_str() };

    auto output_tensors = session_->Run(
        Ort::RunOptions{nullptr},
        input_names, &input_tensor, 1,
        output_names, 1);

    auto& out_tensor = output_tensors[0];
    auto  out_shape  = out_tensor.GetTensorTypeAndShapeInfo().GetShape();
    int num_anchors = static_cast<int>(out_shape[2]);

    const float* out_data = read_output_fp32(
        out_tensor, static_cast<size_t>(out_shape[1]) * num_anchors);

    return postprocess(out_data, num_anchors, scale, pad_x, pad_y, orig_w, orig_h);
}

const float* Detector::read_output_fp32(const Ort::Value& tensor,
                                         size_t total_elements)
{
    auto elem_type = tensor.GetTensorTypeAndShapeInfo().GetElementType();

    if (elem_type == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT)
        return tensor.GetTensorData<float>();

    // FP16 model output → convert to FP32 for postprocessing
    const uint16_t* fp16_data = tensor.GetTensorData<uint16_t>();
    fp32_output_buf_.resize(total_elements);
#ifdef __aarch64__
    const __fp16* src = reinterpret_cast<const __fp16*>(fp16_data);
    for (size_t i = 0; i < total_elements; ++i)
        fp32_output_buf_[i] = static_cast<float>(src[i]);
#else
    for (size_t i = 0; i < total_elements; ++i) {
        uint16_t h = fp16_data[i];
        uint32_t sign = (h & 0x8000u) << 16;
        uint32_t exp  = (h >> 10) & 0x1Fu;
        uint32_t man  = h & 0x3FFu;
        uint32_t f;
        if (exp == 0) f = sign;
        else if (exp == 31) f = sign | 0x7F800000u | (man << 13);
        else f = sign | ((exp + 112) << 23) | (man << 13);
        float val;
        memcpy(&val, &f, 4);
        fp32_output_buf_[i] = val;
    }
#endif
    return fp32_output_buf_.data();
}

// ─────────────────────── Detection ───────────────────────

std::vector<Detection> Detector::detect(const uint8_t* jpeg_data, size_t jpeg_len) {
    if (!ready_ || !jpeg_data || jpeg_len == 0) return {};

    float scale;
    int pad_x, pad_y;
    cv::Mat original;

    preprocess(jpeg_data, jpeg_len, scale, pad_x, pad_y, original);
    return run_inference(scale, pad_x, pad_y, original.cols, original.rows);
}

// ─────────────────────── Detect from raw BGR frame ───────────────────────

std::vector<Detection> Detector::detect_raw(const uint8_t* bgr_data, int width, int height) {
    if (!ready_ || !bgr_data || width <= 0 || height <= 0) return {};

    cv::Mat frame(height, width, CV_8UC3, const_cast<uint8_t*>(bgr_data));

    float scale;
    int pad_x, pad_y;

    if (cfg_.use_fp16)
        preprocess_raw_fp16(frame, scale, pad_x, pad_y);
    else
        preprocess_raw(frame, scale, pad_x, pad_y);

    return run_inference(scale, pad_x, pad_y, width, height);
}

// ─────────────────────── Draw ───────────────────────

void Detector::draw(cv::Mat& frame, const std::vector<Detection>& dets) {
    static const cv::Scalar colors[] = {
        {0, 200, 0},     // go_straight = green
        {200, 200, 0},   // park = cyan
        {200, 0, 0},     // turn_left = blue
        {0, 0, 200},     // turn_right = red
    };

    for (const auto& d : dets) {
        cv::Scalar color = (d.class_id >= 0 && d.class_id < 4)
                               ? colors[d.class_id]
                               : cv::Scalar(128, 128, 128);

        cv::rectangle(frame,
                      cv::Point(static_cast<int>(d.x1), static_cast<int>(d.y1)),
                      cv::Point(static_cast<int>(d.x2), static_cast<int>(d.y2)),
                      color, 2);

        char label[64];
        snprintf(label, sizeof(label), "%s %.0f%%", d.class_name(), d.confidence * 100);

        int baseline = 0;
        cv::Size ts = cv::getTextSize(label, cv::FONT_HERSHEY_SIMPLEX, 0.5, 1, &baseline);

        cv::rectangle(frame,
                      cv::Point(static_cast<int>(d.x1), static_cast<int>(d.y1) - ts.height - 6),
                      cv::Point(static_cast<int>(d.x1) + ts.width + 4, static_cast<int>(d.y1)),
                      color, -1);

        cv::putText(frame, label,
                    cv::Point(static_cast<int>(d.x1) + 2, static_cast<int>(d.y1) - 4),
                    cv::FONT_HERSHEY_SIMPLEX, 0.5, cv::Scalar(0, 0, 0), 1);
    }
}

// ─────────────────────── Detect + Annotate ───────────────────────

void Detector::detect_annotated(const uint8_t* jpeg_data, size_t jpeg_len,
                                std::vector<uint8_t>& out_jpeg,
                                std::vector<Detection>& detections)
{
    if (!ready_ || !jpeg_data || jpeg_len == 0) {
        detections.clear();
        out_jpeg.clear();
        return;
    }

    float scale;
    int pad_x, pad_y;
    cv::Mat original;

    preprocess(jpeg_data, jpeg_len, scale, pad_x, pad_y, original);
    detections = run_inference(scale, pad_x, pad_y, original.cols, original.rows);

    draw(original, detections);

    std::vector<int> params = {cv::IMWRITE_JPEG_QUALITY, 80};
    std::vector<uint8_t> buf;
    cv::imencode(".jpg", original, buf, params);
    out_jpeg = std::move(buf);
}

} // namespace roadsign
