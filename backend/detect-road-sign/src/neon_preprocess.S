/*
 * NEON SIMD Preprocessing — ARM64 Assembly
 *
 * BGR interleaved (uint8) → CHW planar RGB (float32, normalized 0–1)
 *
 * Caller passes 3 separate destination plane pointers:
 *   dst_r, dst_g, dst_b — each points to num_pixels floats.
 *   This makes the function a leaf (no callee-saved registers, zero stack frame).
 *
 * Key ARM NEON instructions used:
 *   LD3   — Load-and-deinterleave: reads 48 bytes (16 BGR pixels),
 *            automatically splits into 3 registers {B[16], G[16], R[16]}.
 *            The Load/Store Unit does deinterleaving in hardware — 0 ALU cost.
 *
 *   PRFM  — Prefetch Memory: tells L1 cache controller to preload the next
 *            cache line while ALU processes current batch. Hides memory latency.
 *
 *   USHLL / USHLL2 — Unsigned Shift Left Long: widens uint8→uint16→uint32
 *            without data loss. Cortex-A76 dual NEON pipeline executes pairs
 *            in parallel.
 *
 *   UCVTF — Unsigned Convert to Float: uint32 → float32 in a single cycle
 *
 *   FMUL  — Floating-point Multiply (vector): normalizes by × (1/255)
 *
 *   ST1   — Store: writes 4 contiguous float32s (16 bytes) per instruction
 *
 * Performance:
 *   Processes 16 pixels per iteration (48 bytes in, 192 bytes out)
 *   640×640 image = 409,600 pixels = 25,600 iterations
 *   Leaf function — zero stack save/restore overhead
 *   ~30 instructions/iteration × ~1 cycle each ≈ ~0.03ms on Cortex-A76 @ 2.4GHz
 *
 * Register allocation (all caller-saved, no prologue/epilogue needed):
 *   x0  = bgr_src pointer (advances by 48 per iteration)
 *   x1  = R plane write pointer (advances by 64 per iteration)
 *   x2  = G plane write pointer (advances by 64 per iteration)
 *   x3  = B plane write pointer (advances by 64 per iteration)
 *   w4  = num_pixels (loop counter source)
 *   w5  = NEON loop counter (num_pixels / 16)
 *   w6  = remainder loop counter
 *   v0  = 16 Blue bytes  (from LD3)
 *   v1  = 16 Green bytes (from LD3)
 *   v2  = 16 Red bytes   (from LD3)
 *   v3–v8 = temporaries for widening/conversion
 *   v30 = broadcast constant {1/255, 1/255, 1/255, 1/255}
 */

    .text
    .align  4
    .global bgr_to_chw_neon
    .type   bgr_to_chw_neon, %function

// void bgr_to_chw_neon(const uint8_t* bgr_src,
//                      float* dst_r, float* dst_g, float* dst_b,
//                      int num_pixels)
// ABI: x0 = bgr_src, x1 = dst_r, x2 = dst_g, x3 = dst_b, w4 = num_pixels

bgr_to_chw_neon:
    // No prologue needed — leaf function, all registers are caller-saved

    // Guard: if num_pixels <= 0, return immediately
    cmp     w4, #0
    b.le    .Lreturn

    // ── Prepare normalization constant: 1.0 / 255.0 ──
    // Compute once, broadcast to all 4 lanes of v30
    fmov    s28, #1.0                   // s28 = 1.0
    mov     w5, #255
    ucvtf   s29, w5                     // s29 = 255.0
    fdiv    s28, s28, s29               // s28 = 0.003921569 ≈ 1/255
    dup     v30.4s, v28.s[0]            // v30 = {inv255, inv255, inv255, inv255}

    // ── Main NEON loop: 16 pixels per iteration ──
    lsr     w5, w4, #4                  // w5 = num_pixels / 16
    cbz     w5, .Lremainder             // skip if < 16 pixels

    .align  4
.Lloop16:
    // ╔══════════════════════════════════════════════════════════╗
    // ║  LOAD & DE-INTERLEAVE: 48 bytes → 3 × 16 bytes         ║
    // ║  LD3 instruction: hardware deinterleave, 0 ALU cycles   ║
    // ╚══════════════════════════════════════════════════════════╝
    ld3     {v0.16b, v1.16b, v2.16b}, [x0], #48
    //  v0 = B[0] B[1] ... B[15]   (16 Blue bytes)
    //  v1 = G[0] G[1] ... G[15]   (16 Green bytes)
    //  v2 = R[0] R[1] ... R[15]   (16 Red bytes)

    // Prefetch: tell L1 cache to preload data 4 iterations ahead (192 bytes)
    prfm    pldl1keep, [x0, #192]

    // ╔══════════════════════════════════════════════════════════╗
    // ║  RED CHANNEL (v2) → R plane at x1                       ║
    // ╚══════════════════════════════════════════════════════════╝
    // Stage 1: uint8 → uint16 (zero-extend)
    ushll   v3.8h, v2.8b, #0           // v3 = R[0..7] as uint16
    ushll2  v4.8h, v2.16b, #0          // v4 = R[8..15] as uint16

    // Stage 2: uint16 → uint32 (zero-extend)
    ushll   v5.4s, v3.4h, #0           // v5 = R[0..3] as uint32
    ushll2  v6.4s, v3.8h, #0           // v6 = R[4..7] as uint32
    ushll   v7.4s, v4.4h, #0           // v7 = R[8..11] as uint32
    ushll2  v8.4s, v4.8h, #0           // v8 = R[12..15] as uint32

    // Stage 3: uint32 → float32
    ucvtf   v5.4s, v5.4s
    ucvtf   v6.4s, v6.4s
    ucvtf   v7.4s, v7.4s
    ucvtf   v8.4s, v8.4s

    // Stage 4: normalize × (1/255)
    fmul    v5.4s, v5.4s, v30.4s
    fmul    v6.4s, v6.4s, v30.4s
    fmul    v7.4s, v7.4s, v30.4s
    fmul    v8.4s, v8.4s, v30.4s

    // Store 16 R floats (64 bytes) to R plane
    st1     {v5.4s, v6.4s, v7.4s, v8.4s}, [x1], #64

    // ╔══════════════════════════════════════════════════════════╗
    // ║  GREEN CHANNEL (v1) → G plane at x2                     ║
    // ╚══════════════════════════════════════════════════════════╝
    ushll   v3.8h, v1.8b, #0
    ushll2  v4.8h, v1.16b, #0

    ushll   v5.4s, v3.4h, #0
    ushll2  v6.4s, v3.8h, #0
    ushll   v7.4s, v4.4h, #0
    ushll2  v8.4s, v4.8h, #0

    ucvtf   v5.4s, v5.4s
    ucvtf   v6.4s, v6.4s
    ucvtf   v7.4s, v7.4s
    ucvtf   v8.4s, v8.4s

    fmul    v5.4s, v5.4s, v30.4s
    fmul    v6.4s, v6.4s, v30.4s
    fmul    v7.4s, v7.4s, v30.4s
    fmul    v8.4s, v8.4s, v30.4s

    st1     {v5.4s, v6.4s, v7.4s, v8.4s}, [x2], #64

    // ╔══════════════════════════════════════════════════════════╗
    // ║  BLUE CHANNEL (v0) → B plane at x3                      ║
    // ╚══════════════════════════════════════════════════════════╝
    ushll   v3.8h, v0.8b, #0
    ushll2  v4.8h, v0.16b, #0

    ushll   v5.4s, v3.4h, #0
    ushll2  v6.4s, v3.8h, #0
    ushll   v7.4s, v4.4h, #0
    ushll2  v8.4s, v4.8h, #0

    ucvtf   v5.4s, v5.4s
    ucvtf   v6.4s, v6.4s
    ucvtf   v7.4s, v7.4s
    ucvtf   v8.4s, v8.4s

    fmul    v5.4s, v5.4s, v30.4s
    fmul    v6.4s, v6.4s, v30.4s
    fmul    v7.4s, v7.4s, v30.4s
    fmul    v8.4s, v8.4s, v30.4s

    st1     {v5.4s, v6.4s, v7.4s, v8.4s}, [x3], #64

    // ── Loop control ──
    subs    w5, w5, #1
    b.gt    .Lloop16

    // ╔══════════════════════════════════════════════════════════╗
    // ║  REMAINDER: handle num_pixels % 16 (scalar path)        ║
    // ╚══════════════════════════════════════════════════════════╝
.Lremainder:
    and     w6, w4, #15                 // w6 = remaining pixels
    cbz     w6, .Lreturn                // none left → done

.Lscalar_loop:
    // Load one BGR pixel (3 bytes)
    ldrb    w7, [x0, #2]               // w7 = Red
    ucvtf   s17, w7                     // s17 = (float)Red
    fmul    s17, s17, s30               // s17 *= inv255
    str     s17, [x1], #4              // store to R plane

    ldrb    w7, [x0, #1]               // w7 = Green
    ucvtf   s17, w7
    fmul    s17, s17, s30
    str     s17, [x2], #4              // store to G plane

    ldrb    w7, [x0, #0]               // w7 = Blue
    ucvtf   s17, w7
    fmul    s17, s17, s30
    str     s17, [x3], #4              // store to B plane

    add     x0, x0, #3                  // advance src by 3 bytes
    subs    w6, w6, #1
    b.gt    .Lscalar_loop

    // No epilogue needed — no callee-saved registers used
.Lreturn:
    ret

    .size   bgr_to_chw_neon, .-bgr_to_chw_neon

/*
 * ═══════════════════════════════════════════════════════════════════════
 * FP16 Variant — Half-precision output (ARMv8.2-A +fp16)
 * ═══════════════════════════════════════════════════════════════════════
 *
 * Identical pipeline but skips the uint16→uint32 widening stage entirely.
 * UCVTF can convert uint16 → float16 directly with the .8h arrangement.
 *
 * Comparison per channel per 16 pixels:
 *   FP32: USHLL×2 → USHLL×4 → UCVTF×4 → FMUL×4 → ST1 = 15 ops, 64 bytes out
 *   FP16: USHLL×2 → UCVTF×2 → FMUL×2 → ST1        =  7 ops, 32 bytes out
 *
 * Total per iteration (3 channels):
 *   FP32: 45 instructions, 192 bytes written
 *   FP16: 21 instructions,  96 bytes written  ← 2.1× fewer ops, 2× less bandwidth
 *
 * Output: IEEE 754 binary16 (uint16_t), suitable for ONNX Runtime FP16 tensors.
 *
 * Register allocation (leaf function, all caller-saved):
 *   x0  = bgr_src pointer (advances by 48)
 *   x1  = R plane write pointer (advances by 32 — 16 × sizeof(float16))
 *   x2  = G plane write pointer (advances by 32)
 *   x3  = B plane write pointer (advances by 32)
 *   w4  = num_pixels
 *   w5  = NEON loop counter
 *   w6  = remainder counter
 *   v0–v2 = LD3 deinterleaved BGR bytes
 *   v3–v4 = temporaries (uint16 / float16)
 *   v30  = broadcast constant {inv255} in float16 ×8 lanes
 */

    .align  4
    .global bgr_to_chw_neon_fp16
    .type   bgr_to_chw_neon_fp16, %function

// void bgr_to_chw_neon_fp16(const uint8_t* bgr_src,
//                           uint16_t* dst_r, uint16_t* dst_g, uint16_t* dst_b,
//                           int num_pixels)
// ABI: x0 = bgr_src, x1 = dst_r, x2 = dst_g, x3 = dst_b, w4 = num_pixels

bgr_to_chw_neon_fp16:
    cmp     w4, #0
    b.le    .Lfp16_return

    // ── Prepare normalization constant in FP16 ──
    // 1.0 / 255.0 computed in FP32 then narrowed to FP16
    fmov    s28, #1.0
    mov     w5, #255
    ucvtf   s29, w5
    fdiv    s28, s28, s29               // s28 = inv255 (FP32)
    fcvt    h28, s28                    // h28 = inv255 (FP16)
    dup     v30.8h, v28.h[0]            // v30.8h = {inv255 × 8} in FP16

    // ── Main loop: 16 pixels per iteration ──
    lsr     w5, w4, #4
    cbz     w5, .Lfp16_remainder

    .align  4
.Lfp16_loop16:
    // Load & de-interleave 48 bytes → B[16], G[16], R[16]
    ld3     {v0.16b, v1.16b, v2.16b}, [x0], #48
    prfm    pldl1keep, [x0, #192]

    // ── RED (v2) → FP16 R plane ──
    ushll   v3.8h, v2.8b, #0           // R[0..7]  uint8 → uint16
    ushll2  v4.8h, v2.16b, #0          // R[8..15] uint8 → uint16
    ucvtf   v3.8h, v3.8h               // uint16 → float16 (DIRECT, no uint32 step!)
    ucvtf   v4.8h, v4.8h
    fmul    v3.8h, v3.8h, v30.8h       // × inv255 in FP16
    fmul    v4.8h, v4.8h, v30.8h
    st1     {v3.8h, v4.8h}, [x1], #32  // store 16 × float16 = 32 bytes

    // ── GREEN (v1) → FP16 G plane ──
    ushll   v3.8h, v1.8b, #0
    ushll2  v4.8h, v1.16b, #0
    ucvtf   v3.8h, v3.8h
    ucvtf   v4.8h, v4.8h
    fmul    v3.8h, v3.8h, v30.8h
    fmul    v4.8h, v4.8h, v30.8h
    st1     {v3.8h, v4.8h}, [x2], #32

    // ── BLUE (v0) → FP16 B plane ──
    ushll   v3.8h, v0.8b, #0
    ushll2  v4.8h, v0.16b, #0
    ucvtf   v3.8h, v3.8h
    ucvtf   v4.8h, v4.8h
    fmul    v3.8h, v3.8h, v30.8h
    fmul    v4.8h, v4.8h, v30.8h
    st1     {v3.8h, v4.8h}, [x3], #32

    subs    w5, w5, #1
    b.gt    .Lfp16_loop16

    // ── Remainder: scalar FP16 path ──
.Lfp16_remainder:
    and     w6, w4, #15
    cbz     w6, .Lfp16_return

.Lfp16_scalar:
    ldrb    w7, [x0, #2]               // Red
    ucvtf   h17, w7                     // int → float16
    fmul    h17, h17, h30               // × inv255
    str     h17, [x1], #2              // store 2 bytes

    ldrb    w7, [x0, #1]               // Green
    ucvtf   h17, w7
    fmul    h17, h17, h30
    str     h17, [x2], #2

    ldrb    w7, [x0, #0]               // Blue
    ucvtf   h17, w7
    fmul    h17, h17, h30
    str     h17, [x3], #2

    add     x0, x0, #3
    subs    w6, w6, #1
    b.gt    .Lfp16_scalar

.Lfp16_return:
    ret

    .size   bgr_to_chw_neon_fp16, .-bgr_to_chw_neon_fp16
