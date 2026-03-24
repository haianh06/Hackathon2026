#!/usr/bin/env python3
"""
Line-Following Module using the trained ResUNet model.

The physical map has white tape borders on a gray surface.
The car drives on the gray GAP between the two white borders.

Algorithm:
  1. Get binary mask of white pixels (via U-Net or HSV threshold)
  2. Apply morphological cleanup (open → close) to remove noise
  3. For each row in the ROI, find left and right white border clusters
  4. Where 2 borders exist: lane centre = midpoint of gap
  5. Where 1 border exists: infer the other using tracked lane width
  6. EMA-smooth corrections across frames to prevent oscillation

    correction  < 0  →  lane centre is LEFT of frame  → steer LEFT
    correction  > 0  →  lane centre is RIGHT of frame → steer RIGHT

Model: ResUNet (best_unet.pth) — trained externally, re-used here.
"""

import asyncio
import logging
import os
import numpy as np
from collections import deque

logger = logging.getLogger('line_follower')

# ── Try importing torch (may not be available in every environment) ──
try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("PyTorch not installed — line-following will use fallback mode")


# ═══════════════════════════════════════════════════════════
# U-Net model definition — MUST match the training architecture
# (copied from the user's line_following/test2.py)
# ═══════════════════════════════════════════════════════════

if TORCH_AVAILABLE:
    class ResidualBlock(nn.Module):
        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.conv = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 3, padding=1),
                nn.BatchNorm2d(out_ch),
                nn.ReLU(inplace=True),
                nn.Conv2d(out_ch, out_ch, 3, padding=1),
                nn.BatchNorm2d(out_ch),
            )
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 1),
                nn.BatchNorm2d(out_ch),
            ) if in_ch != out_ch else nn.Identity()
            self.relu = nn.ReLU(inplace=True)

        def forward(self, x):
            return self.relu(self.conv(x) + self.shortcut(x))

    class ResUNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.enc1 = ResidualBlock(3, 16)
            self.enc2 = ResidualBlock(16, 32)
            self.enc3 = ResidualBlock(32, 64)
            self.bottleneck = ResidualBlock(64, 128)

            self.up1 = nn.ConvTranspose2d(128, 64, 2, stride=2)
            self.dec1 = ResidualBlock(128, 64)

            self.up2 = nn.ConvTranspose2d(64, 32, 2, stride=2)
            self.dec2 = ResidualBlock(64, 32)

            self.up3 = nn.ConvTranspose2d(32, 16, 2, stride=2)
            self.dec3 = ResidualBlock(32, 16)

            self.out = nn.Conv2d(16, 1, 1)

        def forward(self, x):
            s1 = self.enc1(x)
            s2 = self.enc2(nn.MaxPool2d(2)(s1))
            s3 = self.enc3(nn.MaxPool2d(2)(s2))
            b = self.bottleneck(nn.MaxPool2d(2)(s3))
            d1 = self.dec1(torch.cat([self.up1(b), s3], dim=1))
            d2 = self.dec2(torch.cat([self.up2(d1), s2], dim=1))
            d3 = self.dec3(torch.cat([self.up3(d2), s1], dim=1))
            return self.out(d3)


# ═══════════════════════════════════════════════════════════
# LineFollower — wraps model + camera frame analysis
# ═══════════════════════════════════════════════════════════

# Path to the pre-trained weights (relative to hardware/)
_DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', 'line_following', 'best_unet.pth',
)

INPUT_SIZE = 256          # must match training size
MASK_THRESHOLD = 0.5      # sigmoid threshold for lane mask

# Only analyse the bottom 60 % of the frame (the road ahead)
ROI_TOP_FRAC = 0.40

# Dead-zone: corrections smaller than this are treated as "centred"
DEAD_ZONE = 0.03          # normalised (-1 … +1) — tighter for differential steering

# EMA smoothing factor (0 = full smoothing, 1 = no smoothing)
EMA_ALPHA = 0.35

# Morphological kernel size for mask cleanup
MORPH_KERNEL = 5

# Expected lane width range (fraction of image width) for single-border inference
EXPECTED_LANE_FRAC_MIN = 0.15   # lane is at least 15% of image width
EXPECTED_LANE_FRAC_MAX = 0.60   # lane is at most 60% of image width

# Minimum white pixels to consider the mask valid
MIN_WHITE_PIXELS = 50

# ── Canny + UNet fusion thresholds ──
# UNet offset tells how far the lane center is from the frame center.
# When offset exceeds warn threshold, we start blending UNet correction into
# canny steer to pull the car back toward the lane center.
UNET_WARN_THRESHOLD = 0.25       # start blending when 25% off-center
UNET_EMERGENCY_THRESHOLD = 0.55  # hard override when 55% off-center
UNET_BLEND_GAIN = 0.5            # max blending weight for UNet in warning zone
UNET_EMERGENCY_GAIN = 1.2        # multiplier for emergency override steer


class LineFollower:
    """
    Analyses a camera frame (JPEG bytes) and returns how far the detected
    lane centre is from the image centre.

    Return value of `analyse_frame(jpeg_bytes)`:
        float in [-1.0 … +1.0]
        negative = lane is to the LEFT  → steer left
        positive = lane is to the RIGHT → steer right
        0.0      = centred (or no lane detected)

    Features:
      - EMA smoothing across consecutive frames to avoid oscillation
      - Morphological cleanup of the binary mask
      - Tracks expected lane width from 2-border rows to infer missing borders
      - Confidence-weighted averaging (2-border rows weigh more)
      - Optional Canny-edge-detection fusion for improved lane tracking
    """

    def __init__(self, model_path=None):
        self.model = None
        self.device = 'cpu'
        self._ready = False
        self._cv2 = None

        # EMA state
        self._ema_correction = 0.0
        self._ema_lane_width = None   # tracked lane width in pixels
        self._morph_kernel = None

        # Canny detector (loaded lazily)
        self._canny_detector = None
        self._canny_available = False

        if not TORCH_AVAILABLE:
            logger.warning("LineFollower: PyTorch missing, using fallback OpenCV-only mode")
            self._init_cv2_fallback()
            return

        # cv2 is needed for JPEG decode + resize
        try:
            import cv2
            self._cv2 = cv2
            self._morph_kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (MORPH_KERNEL, MORPH_KERNEL))
        except ImportError:
            logger.error("OpenCV (cv2) not available — line-following disabled")
            return

        model_path = model_path or _DEFAULT_MODEL_PATH
        if not os.path.isfile(model_path):
            logger.warning(f"Model weights not found at {model_path} — fallback mode")
            self._init_cv2_fallback()
            return

        try:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
            self.model = ResUNet().to(self.device)
            self.model.load_state_dict(
                torch.load(model_path, map_location=self.device, weights_only=True)
            )
            self.model.eval()
            self._ready = True
            logger.info(f"✅ LineFollower model loaded ({model_path}) on {self.device}")
        except Exception as e:
            logger.error(f"Failed to load model: {e} — fallback mode")
            self._init_cv2_fallback()

        # Try loading canny detector for fusion
        self._init_canny_detector()

    def _init_canny_detector(self):
        """Lazily initialise the canny edge detector (AdaptiveTrackerV2).

        Canny is the PRIMARY steering source — it computes the Point of
        Orientation from edge detection and drives a PD controller.
        UNet is only used as a secondary lane-passability check.
        """
        try:
            import sys
            canny_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      '..', 'canny-edge-detection-main')
            if canny_path not in sys.path:
                sys.path.insert(0, canny_path)
            from test_1_improved import AdaptiveTrackerV2
            self._canny_detector = AdaptiveTrackerV2(width=640, height=480)
            self._canny_available = True
            logger.info("✅ Canny AdaptiveTrackerV2 loaded for PRIMARY lane steering")
        except Exception as e:
            self._canny_available = False
            logger.debug(f"Canny detector not available: {e}")

    # ── Fallback: simple colour thresholding (no model needed) ──
    def _init_cv2_fallback(self):
        try:
            import cv2
            self._cv2 = cv2
            self._morph_kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (MORPH_KERNEL, MORPH_KERNEL))
            self._ready = True
            self.model = None
            logger.info("LineFollower using OpenCV colour-threshold fallback")
        except ImportError:
            logger.error("OpenCV not available — line-following fully disabled")
            self._ready = False

    def reset(self):
        """Reset EMA state — call when starting a new navigation segment."""
        self._ema_correction = 0.0
        self._ema_lane_width = None
        # Reset canny PID integral term
        if self._canny_available and self._canny_detector is not None:
            self._canny_detector._integral = 0.0
            self._canny_detector.steer_ema.value = None
            # Unlock ROI for new segment
            self._canny_detector.unlock_roi()

    # ────────────────────────────────────────────────────────
    @property
    def is_ready(self):
        return self._ready

    def analyse_frame(self, jpeg_bytes):
        """
        Analyse a JPEG frame and return EMA-smoothed steering correction [-1 … +1].

        Decision hierarchy:
          1. CANNY (primary) — AdaptiveTrackerV2 computes the Point of
             Orientation from sliding-window edge detection + PD controller.
             The vehicle steers toward this point.
          2. UNET (secondary) — only used as a safety check: if the UNet
             mask shows a white border dangerously close to the lane centre
             it triggers an emergency avoidance override.
          3. If Canny is unavailable, UNet steers as a fallback.

        Returns 0.0 on any error.
        """
        if not self._ready or jpeg_bytes is None:
            return 0.0

        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is None:
                return 0.0

            # ── CANNY = PRIMARY: Point of Orientation steering ──
            canny_steer = None
            if self._canny_available and self._canny_detector is not None:
                try:
                    frame_resized = self._cv2.resize(frame, (640, 480))
                    steer_val, _ = self._canny_detector.process_frame(frame_resized)
                    canny_steer = float(steer_val)
                except Exception as e:
                    logger.debug(f"Canny detection error: {e}")

            # ── UNET = SECONDARY: lane passability check only ──
            unet_raw = 0.0
            if self.model is not None:
                mask = self._get_mask(frame)
                mask = self._clean_mask(mask)
                roi_top = int(mask.shape[0] * ROI_TOP_FRAC)
                roi = mask[roi_top:, :]
                unet_raw = self._lane_gap_offset(roi)

            # ── Decide final steering: canny (primary) + unet (centering) ──
            abs_unet = abs(unet_raw)

            if canny_steer is not None:
                # Canny = primary steer (already has PD + EMA internally)
                raw = canny_steer

                # UNet gradual centerline correction:
                # unet_raw > 0 → lane center is RIGHT → car drifting LEFT → steer RIGHT
                # unet_raw < 0 → lane center is LEFT  → car drifting RIGHT → steer LEFT
                # As offset grows, blend UNet correction progressively.
                if abs_unet > UNET_EMERGENCY_THRESHOLD:
                    # EMERGENCY: car about to cross white line → hard steer toward center
                    raw = float(np.clip(unet_raw * UNET_EMERGENCY_GAIN, -1.0, 1.0))
                    logger.warning(
                        f"UNET EMERGENCY: off-center {abs_unet:.0%}! "
                        f"canny={canny_steer:+.3f} unet={unet_raw:+.3f} → override={raw:+.3f}"
                    )
                elif abs_unet > UNET_WARN_THRESHOLD:
                    # WARNING ZONE: gradually blend UNet correction into canny steer
                    # blend ramps from 0 at warn threshold to UNET_BLEND_GAIN at emergency
                    blend = ((abs_unet - UNET_WARN_THRESHOLD) /
                             (UNET_EMERGENCY_THRESHOLD - UNET_WARN_THRESHOLD))
                    blend = min(blend, 1.0) * UNET_BLEND_GAIN
                    # Weighted mix: (1-blend)*canny + blend*unet_correction
                    raw = (1.0 - blend) * canny_steer + blend * unet_raw
                    raw = float(np.clip(raw, -1.0, 1.0))
                    logger.debug(
                        f"UNET BLEND: off-center {abs_unet:.0%}, blend={blend:.2f} "
                        f"canny={canny_steer:+.3f} unet={unet_raw:+.3f} → raw={raw:+.3f}"
                    )

                # No extra EMA — canny output is already smoothed
                self._ema_correction = raw
            else:
                # Canny unavailable → fallback to UNet with EMA smoothing
                raw = unet_raw
                self._ema_correction = EMA_ALPHA * raw + (1.0 - EMA_ALPHA) * self._ema_correction

            if abs(raw) > 0.01:
                logger.debug(
                    f"Lane: canny={'N/A' if canny_steer is None else f'{canny_steer:+.3f}'} "
                    f"unet={unet_raw:+.3f} raw={raw:+.3f} final={self._ema_correction:+.3f} "
                    f"primary={'canny' if canny_steer is not None else 'unet'}"
                )

            return self._ema_correction
        except Exception as e:
            logger.debug(f"analyse_frame error: {e}")
            return 0.0

    def analyse_frame_debug(self, jpeg_bytes):
        """
        Like analyse_frame but also returns debug info for the dev page:
        { correction, raw_correction, mask_jpeg, lane_cx, frame_cx, roi_top,
          left_edge, right_edge, gap_center, lane_width, confidence,
          ema_lane_width, borders_found }
        """
        result = {
            'correction': 0.0,
            'raw_correction': 0.0,
            'mask_jpeg': None,
            'lane_cx': None,
            'frame_cx': None,
            'roi_top': None,
            'left_edge': None,
            'right_edge': None,
            'gap_center': None,
            'lane_width': None,
            'confidence': 0.0,
            'ema_lane_width': self._ema_lane_width,
            'borders_found': 0,
        }
        if not self._ready or jpeg_bytes is None:
            return result

        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is None:
                return result

            mask = self._get_mask(frame)
            mask = self._clean_mask(mask)
            h, w = mask.shape
            roi_top = int(h * ROI_TOP_FRAC)
            roi = mask[roi_top:, :]

            raw, debug = self._lane_gap_offset(roi, return_debug=True)

            # EMA (do NOT update state here — debug calls should be side-effect-free)
            ema = EMA_ALPHA * raw + (1.0 - EMA_ALPHA) * self._ema_correction
            result['correction'] = ema
            result['raw_correction'] = raw
            result['roi_top'] = roi_top
            result['ema_lane_width'] = self._ema_lane_width

            if debug:
                result['left_edge'] = debug.get('left_edge')
                result['right_edge'] = debug.get('right_edge')
                result['gap_center'] = debug.get('gap_center')
                result['frame_cx'] = debug.get('frame_cx')
                result['lane_cx'] = debug.get('gap_center')
                result['lane_width'] = debug.get('lane_width')
                result['confidence'] = debug.get('confidence', 0.0)
                result['borders_found'] = debug.get('borders_found', 0)

            # Build an annotated debug image
            overlay = frame.copy()
            # Draw mask overlay (green = white-detected)
            mask_rgb = np.zeros_like(frame)
            mask_rgb[:, :, 1] = mask * 200
            overlay = self._cv2.addWeighted(overlay, 0.7, mask_rgb, 0.3, 0)

            # Draw ROI line
            self._cv2.line(overlay, (0, roi_top), (w, roi_top), (0, 255, 255), 2)

            # Draw gap center line and frame center line in ROI
            roi_h, roi_w = roi.shape
            fc = roi_w // 2
            self._cv2.line(overlay, (fc, roi_top), (fc, h), (255, 255, 0), 1)  # frame center

            if debug and debug.get('gap_center') is not None:
                gc = int(debug['gap_center'])
                self._cv2.line(overlay, (gc, roi_top), (gc, h), (0, 255, 0), 2)  # lane center
                # Draw left/right edges
                if debug.get('left_edge') is not None:
                    le = int(debug['left_edge'])
                    self._cv2.line(overlay, (le, roi_top), (le, h), (0, 0, 255), 2)
                if debug.get('right_edge') is not None:
                    re = int(debug['right_edge'])
                    self._cv2.line(overlay, (re, roi_top), (re, h), (255, 0, 0), 2)

            # Correction text
            txt = f"corr: {ema:+.3f} (raw:{raw:+.3f})"
            self._cv2.putText(overlay, txt, (10, 30),
                              self._cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            conf_txt = f"conf: {result['confidence']:.0%}  borders: {result['borders_found']}"
            self._cv2.putText(overlay, conf_txt, (10, 55),
                              self._cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 200), 1)

            _, jpeg_buf = self._cv2.imencode('.jpg', overlay, [self._cv2.IMWRITE_JPEG_QUALITY, 75])
            result['mask_jpeg'] = bytes(jpeg_buf)
            return result
        except Exception as e:
            logger.debug(f"analyse_frame_debug error: {e}")
            return result

    # ── Get binary mask from frame ──

    def _get_mask(self, frame):
        """Return binary mask (uint8 0/1) of white lane borders."""
        if self.model is not None:
            return self._mask_unet(frame)
        else:
            return self._mask_threshold(frame)

    def _clean_mask(self, mask):
        """Morphological open then close to remove noise."""
        if self._morph_kernel is None:
            return mask
        m = (mask * 255).astype(np.uint8)
        m = self._cv2.morphologyEx(m, self._cv2.MORPH_OPEN, self._morph_kernel)
        m = self._cv2.morphologyEx(m, self._cv2.MORPH_CLOSE, self._morph_kernel)
        return (m > 127).astype(np.uint8)

    def _mask_unet(self, frame):
        img = self._cv2.resize(frame, (INPUT_SIZE, INPUT_SIZE))
        img = img.astype(np.float32) / 255.0
        tensor = torch.from_numpy(img.transpose(2, 0, 1)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            out = torch.sigmoid(self.model(tensor))
        mask = out.squeeze().cpu().numpy()
        mask = (mask > MASK_THRESHOLD).astype(np.uint8)
        # Resize back to original frame size
        mask = self._cv2.resize(mask, (frame.shape[1], frame.shape[0]),
                                interpolation=self._cv2.INTER_NEAREST)
        return mask

    def _mask_threshold(self, frame):
        hsv = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2HSV)
        lower_white = np.array([0, 0, 180], dtype=np.uint8)
        upper_white = np.array([180, 60, 255], dtype=np.uint8)
        mask = self._cv2.inRange(hsv, lower_white, upper_white)
        return (mask > 0).astype(np.uint8)

    # ── Core: find lane centre as the GAP between left/right borders ──

    def _lane_gap_offset(self, binary_mask, return_debug=False):
        """
        Find the driveable gap between left and right white border clusters.

        For each row:
          1. Find all white-pixel runs (connected components on that row)
          2. If ≥ 2 clusters: gap centre = midpoint(right-edge of leftmost,
                                                     left-edge of rightmost)
             → also use this row to update expected lane width
          3. If 1 cluster:  use tracked lane width to infer the missing border
          4. If 0 clusters: skip row

        Rows with 2 borders get confidence=1.0; 1-border rows get 0.5.
        Final gap_center = weighted average by confidence, then dead-zone.

        Return: normalised offset in [-1, +1] from frame centre.
                negative = lane LEFT of frame → steer left
                positive = lane RIGHT of frame → steer right
        """
        debug = {}
        h, w = binary_mask.shape

        if binary_mask.sum() < MIN_WHITE_PIXELS:
            if return_debug:
                return 0.0, debug
            return 0.0

        gap_centers = []
        confidences = []
        left_edges = []
        right_edges = []
        lane_widths = []      # from 2-border rows only

        expected_lw = self._ema_lane_width  # may be None initially

        for row_idx in range(h):
            row = binary_mask[row_idx]
            # Find runs of white pixels
            diff = np.diff(np.concatenate(([0], row, [0])))
            starts = np.where(diff == 1)[0]
            ends = np.where(diff == -1)[0]
            if len(starts) == 0:
                continue

            if len(starts) >= 2:
                # ── Two or more clusters: high-confidence ──
                left_right_edge = ends[0] - 1        # rightmost px of left border
                right_left_edge = starts[-1]          # leftmost px of right border
                gc = (left_right_edge + right_left_edge) / 2.0
                lw = right_left_edge - left_right_edge  # gap width (lane width)

                # Sanity: gap must be reasonable fraction of image
                if lw > w * EXPECTED_LANE_FRAC_MIN and lw < w * EXPECTED_LANE_FRAC_MAX:
                    gap_centers.append(gc)
                    confidences.append(1.0)
                    left_edges.append(left_right_edge)
                    right_edges.append(right_left_edge)
                    lane_widths.append(lw)
                elif lw > 0:
                    # Gap too small or too large — still use but lower confidence
                    gap_centers.append(gc)
                    confidences.append(0.3)
                    left_edges.append(left_right_edge)
                    right_edges.append(right_left_edge)

            else:
                # ── One cluster: infer missing border from expected lane width ──
                cluster_start = starts[0]
                cluster_end = ends[0] - 1
                cluster_cx = (cluster_start + cluster_end) / 2.0

                if expected_lw is not None:
                    # Decide if this is the left or right border
                    if cluster_cx < w / 2.0:
                        # Cluster is on the left → it's the left border
                        # Gap center = left_right_edge + expected_lw / 2
                        gc = cluster_end + expected_lw / 2.0
                        left_edges.append(cluster_end)
                    else:
                        # Cluster is on the right → it's the right border
                        # Gap center = right_left_edge - expected_lw / 2
                        gc = cluster_start - expected_lw / 2.0
                        right_edges.append(cluster_start)
                    gap_centers.append(gc)
                    confidences.append(0.5)
                else:
                    # No expected lane width yet — simple heuristic
                    if cluster_cx < w / 2.0:
                        gc = (cluster_end + w) / 2.0
                    else:
                        gc = cluster_start / 2.0
                    gap_centers.append(gc)
                    confidences.append(0.2)

        if not gap_centers:
            if return_debug:
                return 0.0, debug
            return 0.0

        # ── Update expected lane width from 2-border rows ──
        if lane_widths:
            measured_lw = float(np.median(lane_widths))
            if self._ema_lane_width is None:
                self._ema_lane_width = measured_lw
            else:
                self._ema_lane_width = 0.3 * measured_lw + 0.7 * self._ema_lane_width

        # ── Confidence-weighted gap center ──
        gc_arr = np.array(gap_centers)
        conf_arr = np.array(confidences)
        lane_cx = float(np.average(gc_arr, weights=conf_arr))

        frame_cx = w / 2.0
        offset = (lane_cx - frame_cx) / frame_cx

        # Apply dead-zone
        if abs(offset) < DEAD_ZONE:
            offset = 0.0

        offset = float(np.clip(offset, -1.0, 1.0))

        if return_debug:
            total_conf = conf_arr.sum()
            max_conf = len(conf_arr)  # if all were 1.0
            debug['gap_center'] = lane_cx
            debug['frame_cx'] = frame_cx
            debug['left_edge'] = float(np.median(left_edges)) if left_edges else None
            debug['right_edge'] = float(np.median(right_edges)) if right_edges else None
            debug['lane_width'] = self._ema_lane_width
            debug['confidence'] = float(total_conf / max_conf) if max_conf > 0 else 0.0
            debug['borders_found'] = 2 if lane_widths else (1 if gap_centers else 0)
            return offset, debug

        return offset
