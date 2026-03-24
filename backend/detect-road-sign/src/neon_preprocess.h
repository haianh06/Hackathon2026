/**
 * NEON SIMD Preprocessing — C header
 *
 * Declares NEON-accelerated BGR→CHW conversion functions.
 * Two variants:
 *   FP32: output float32 planes — standard, works with any model
 *   FP16: output float16 planes — requires FP16 ONNX model, 2× less bandwidth
 *
 * On aarch64: implemented in neon_preprocess.S (ARM64 assembly)
 * On x86_64:  falls back to scalar C in this header
 */

#pragma once

#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifdef __aarch64__

/**
 * BGR→CHW Float32 (standard precision)
 *
 * Input:  B G R B G R ... (num_pixels × 3 bytes)
 * Output: dst_r[N], dst_g[N], dst_b[N] (float32s ∈ [0, 1])
 */
void bgr_to_chw_neon(const unsigned char* bgr_src,
                     float* dst_r, float* dst_g, float* dst_b,
                     int num_pixels);

/**
 * BGR→CHW Float16 (half precision — ARMv8.2-A +fp16)
 *
 * Skips uint16→uint32 widening: 2.1× fewer instructions, 2× less memory writes.
 * Output is IEEE 754 binary16 stored as uint16_t — direct input to ONNX FP16 models.
 *
 * Input:  B G R B G R ... (num_pixels × 3 bytes)
 * Output: dst_r[N], dst_g[N], dst_b[N] (float16s as uint16_t ∈ [0, 1])
 */
void bgr_to_chw_neon_fp16(const unsigned char* bgr_src,
                           unsigned short* dst_r, unsigned short* dst_g,
                           unsigned short* dst_b, int num_pixels);

#else

/* ── Scalar fallbacks for non-ARM architectures ── */

static inline void bgr_to_chw_neon(const unsigned char* bgr_src,
                                   float* dst_r, float* dst_g, float* dst_b,
                                   int num_pixels) {
    const float inv255 = 1.0f / 255.0f;
    for (int i = 0; i < num_pixels; ++i) {
        dst_b[i] = bgr_src[i * 3 + 0] * inv255;
        dst_g[i] = bgr_src[i * 3 + 1] * inv255;
        dst_r[i] = bgr_src[i * 3 + 2] * inv255;
    }
}

/* Software float32→float16 bit conversion */
static inline unsigned short _f32_to_f16_bits(float v) {
    unsigned int f;
    memcpy(&f, &v, 4);
    unsigned short sign = (unsigned short)((f >> 16) & 0x8000u);
    int exp = (int)((f >> 23) & 0xFFu) - 127 + 15;
    unsigned short man = (unsigned short)((f >> 13) & 0x3FFu);
    if (exp <= 0) return sign;
    if (exp >= 31) return (unsigned short)(sign | 0x7C00u);
    return (unsigned short)(sign | ((unsigned short)exp << 10) | man);
}

static inline void bgr_to_chw_neon_fp16(const unsigned char* bgr_src,
                                         unsigned short* dst_r,
                                         unsigned short* dst_g,
                                         unsigned short* dst_b,
                                         int num_pixels) {
    const float inv255 = 1.0f / 255.0f;
    for (int i = 0; i < num_pixels; ++i) {
        dst_r[i] = _f32_to_f16_bits(bgr_src[i * 3 + 2] * inv255);
        dst_g[i] = _f32_to_f16_bits(bgr_src[i * 3 + 1] * inv255);
        dst_b[i] = _f32_to_f16_bits(bgr_src[i * 3 + 0] * inv255);
    }
}

#endif

#ifdef __cplusplus
}
#endif
