import cv2
import numpy as np
from collections import deque
import time
from typing import Optional, Tuple, List, Dict

# ═════════════════════════════════════════════════════════════
# Utility Classes
# ═════════════════════════════════════════════════════════════

class EMA:
    """Exponential Moving Average filter."""
    def __init__(self, alpha: float = 0.35, init: Optional[float] = None):
        self.alpha = float(np.clip(alpha, 0.0, 1.0))
        self.v = init

    def update(self, x: Optional[float]) -> Optional[float]:
        if x is None:
            return self.v
        if self.v is None:
            self.v = x
        else:
            self.v = self.alpha * x + (1.0 - self.alpha) * self.v
        return self.v

    def reset(self) -> None:
        self.v = None


class SimpleKalmanFilter1D:
    """
    1-D Kalman filter for smooth lane-center tracking with velocity.
    State: [position, velocity]
    """
    def __init__(self, process_noise: float = 0.5, measurement_noise: float = 2.0):
        self.x = np.array([0.0, 0.0])       # [position, velocity]
        self.P = np.eye(2) * 100.0           # covariance (high = uncertain)
        self.Q = np.eye(2) * process_noise   # process noise
        self.R = np.array([[measurement_noise]])  # measurement noise
        self.H = np.array([[1.0, 0.0]])      # observation: we measure position
        self.initialised = False

    def predict(self, dt: float = 1.0) -> float:
        F = np.array([[1.0, dt], [0.0, 1.0]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + self.Q
        return float(self.x[0])

    def update(self, z: float) -> float:
        if not self.initialised:
            self.x[0] = z
            self.x[1] = 0.0
            self.P = np.eye(2) * 10.0
            self.initialised = True
            return z
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + (K @ y).flatten()
        self.P = (np.eye(2) - K @ self.H) @ self.P
        return float(self.x[0])

    def reset(self):
        self.x = np.array([0.0, 0.0])
        self.P = np.eye(2) * 100.0
        self.initialised = False


# ═════════════════════════════════════════════════════════════
# Preprocessing: Shadow Removal, CLAHE, Multi-Colorspace Fusion
# ═════════════════════════════════════════════════════════════

class AdaptivePreprocessor:
    """
    Robust preprocessing pipeline:
      1. Shadow removal via LAB L-channel CLAHE normalisation
      2. Multi-colorspace lane extraction (HSV white+yellow, HLS L-channel, LAB L)
      3. Bilateral filter for edge-preserving smoothing
      4. Morphological cleanup to bridge small gaps and remove noise
    """
    def __init__(self):
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._morph_kernel_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    def remove_shadows(self, bgr: np.ndarray) -> np.ndarray:
        """Remove shadows using LAB color space — normalise L channel with CLAHE."""
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l_chan = lab[:, :, 0]
        l_enhanced = self.clahe.apply(l_chan)
        lab[:, :, 0] = l_enhanced
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def extract_lane_mask(self, bgr: np.ndarray) -> np.ndarray:
        """
        Multi-colorspace fusion for robust white lane border detection.
        Combines HSV, HLS, and LAB detections to handle varied lighting and shadows.
        """
        corrected = self.remove_shadows(bgr)

        # HSV white detection (primary)
        hsv = cv2.cvtColor(corrected, cv2.COLOR_BGR2HSV)
        white_hsv = cv2.inRange(hsv, (0, 0, 160), (180, 80, 255))
        # HSV yellow detection (secondary)
        yellow_hsv = cv2.inRange(hsv, (15, 80, 100), (40, 255, 255))
        # HLS L-channel: high lightness = white tape
        hls = cv2.cvtColor(corrected, cv2.COLOR_BGR2HLS)
        white_hls = cv2.inRange(hls[:, :, 1], 170, 255)
        # LAB L-channel for brightness-based detection
        lab = cv2.cvtColor(corrected, cv2.COLOR_BGR2LAB)
        white_lab = cv2.inRange(lab[:, :, 0], 180, 255)

        # Fuse all masks
        combined = cv2.bitwise_or(white_hsv, yellow_hsv)
        combined = cv2.bitwise_or(combined, white_hls)
        combined = cv2.bitwise_or(combined, white_lab)

        # Close small gaps then remove noise
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, self._morph_kernel_large)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, self._morph_kernel)
        return combined

    def get_edges(self, bgr: np.ndarray, roi_mask: np.ndarray) -> np.ndarray:
        """Canny edges with adaptive thresholds + bilateral noise filtering."""
        lane_mask = self.extract_lane_mask(bgr)
        masked = cv2.bitwise_and(lane_mask, roi_mask)
        smoothed = cv2.bilateralFilter(masked, 9, 75, 75)
        v = np.median(smoothed[smoothed > 0]) if np.any(smoothed > 0) else 127
        low = int(max(25, 0.5 * v))
        high = int(min(200, 1.2 * v))
        return cv2.Canny(smoothed, low, high)


# ═════════════════════════════════════════════════════════════
# Bird's-Eye View (BEV) Perspective Transform
# ═════════════════════════════════════════════════════════════

class BirdsEyeView:
    """
    Warp camera frame to top-down view.
    Parallel lane lines become truly parallel; 90° turns are geometric right-angles.
    """
    def __init__(self, frame_w: int = 640, frame_h: int = 480):
        self.frame_w = frame_w
        self.frame_h = frame_h
        self._bev_size = (frame_w, frame_h)
        w, h = frame_w, frame_h
        src = np.float32([
            [int(0.10 * w), h],
            [int(0.42 * w), int(0.55 * h)],
            [int(0.58 * w), int(0.55 * h)],
            [int(0.90 * w), h],
        ])
        margin = int(0.20 * w)
        dst = np.float32([
            [margin, h], [margin, 0],
            [w - margin, 0], [w - margin, h],
        ])
        self._M = cv2.getPerspectiveTransform(src, dst)
        self._M_inv = cv2.getPerspectiveTransform(dst, src)

    def warp(self, img: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(img, self._M, self._bev_size, flags=cv2.INTER_LINEAR)

    def unwarp(self, img: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(img, self._M_inv, self._bev_size, flags=cv2.INTER_LINEAR)

    def warp_point(self, x: float, y: float) -> Tuple[float, float]:
        pt = np.array([[[x, y]]], dtype=np.float32)
        w = cv2.perspectiveTransform(pt, self._M)
        return float(w[0, 0, 0]), float(w[0, 0, 1])

    def unwarp_point(self, x: float, y: float) -> Tuple[float, float]:
        pt = np.array([[[x, y]]], dtype=np.float32)
        w = cv2.perspectiveTransform(pt, self._M_inv)
        return float(w[0, 0, 0]), float(w[0, 0, 1])


# ═════════════════════════════════════════════════════════════
# Sliding Window Lane Finder (Histogram-based)
# ═════════════════════════════════════════════════════════════

class SlidingWindowLaneFinder:
    """
    Histogram-based sliding window lane detection on BEV binary image.
    Handles 90° turns and intersections via:
      - Bottom-histogram base peaks
      - Adaptive widening when pixels are sparse
      - Gap bridging using previous polynomial predictions
    """
    def __init__(self, n_windows: int = 12, margin: int = 60,
                 min_pix: int = 30, max_gap_windows: int = 4):
        self.n_windows = n_windows
        self.margin = margin
        self.min_pix = min_pix
        self.max_gap_windows = max_gap_windows

    def find_lanes(self, binary_bev: np.ndarray,
                   prev_left_fit: Optional[np.ndarray] = None,
                   prev_right_fit: Optional[np.ndarray] = None
                   ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray],
                              List[Tuple], List[Tuple], np.ndarray]:
        h, w = binary_bev.shape[:2]
        debug_img = np.zeros((h, w, 3), dtype=np.uint8)

        # Histogram of bottom half to find lane base positions
        bottom_half = binary_bev[h // 2:, :]
        histogram = np.sum(bottom_half, axis=0)

        midpoint = w // 2
        left_base = int(np.argmax(histogram[:midpoint])) if np.any(histogram[:midpoint] > 0) else None
        right_base = int(np.argmax(histogram[midpoint:]) + midpoint) if np.any(histogram[midpoint:] > 0) else None

        if left_base is None or histogram[left_base] < self.min_pix:
            left_base = int(np.polyval(prev_left_fit, h - 1)) if prev_left_fit is not None else w // 4
        if right_base is None or histogram[right_base] < self.min_pix:
            right_base = int(np.polyval(prev_right_fit, h - 1)) if prev_right_fit is not None else 3 * w // 4

        left_current, right_current = left_base, right_base
        window_height = h // self.n_windows
        nonzero = binary_bev.nonzero()
        nonzero_y, nonzero_x = np.array(nonzero[0]), np.array(nonzero[1])

        left_lane_inds, right_lane_inds = [], []
        left_gap_count, right_gap_count = 0, 0

        for win_idx in range(self.n_windows):
            y_low = h - (win_idx + 1) * window_height
            y_high = h - win_idx * window_height

            # Adaptive margin: widen when losing pixels
            lm = self.margin + left_gap_count * 20
            rm = self.margin + right_gap_count * 20

            xl_low, xl_high = max(0, left_current - lm), min(w, left_current + lm)
            xr_low, xr_high = max(0, right_current - rm), min(w, right_current + rm)

            good_left = ((nonzero_y >= y_low) & (nonzero_y < y_high) &
                         (nonzero_x >= xl_low) & (nonzero_x < xl_high)).nonzero()[0]
            good_right = ((nonzero_y >= y_low) & (nonzero_y < y_high) &
                          (nonzero_x >= xr_low) & (nonzero_x < xr_high)).nonzero()[0]

            left_lane_inds.append(good_left)
            right_lane_inds.append(good_right)
            cv2.rectangle(debug_img, (xl_low, y_low), (xl_high, y_high), (0, 255, 0), 2)
            cv2.rectangle(debug_img, (xr_low, y_low), (xr_high, y_high), (0, 0, 255), 2)

            if len(good_left) >= self.min_pix:
                left_current = int(np.mean(nonzero_x[good_left]))
                left_gap_count = 0
            else:
                left_gap_count += 1
                if prev_left_fit is not None and left_gap_count <= self.max_gap_windows:
                    y_mid = (y_low + y_high) / 2
                    left_current = int(np.clip(np.polyval(prev_left_fit, y_mid), 0, w - 1))

            if len(good_right) >= self.min_pix:
                right_current = int(np.mean(nonzero_x[good_right]))
                right_gap_count = 0
            else:
                right_gap_count += 1
                if prev_right_fit is not None and right_gap_count <= self.max_gap_windows:
                    y_mid = (y_low + y_high) / 2
                    right_current = int(np.clip(np.polyval(prev_right_fit, y_mid), 0, w - 1))

        left_lane_inds = np.concatenate(left_lane_inds) if left_lane_inds else np.array([])
        right_lane_inds = np.concatenate(right_lane_inds) if right_lane_inds else np.array([])

        left_points, right_points = [], []
        left_fit, right_fit = None, None

        if len(left_lane_inds) > 0:
            lx, ly = nonzero_x[left_lane_inds], nonzero_y[left_lane_inds]
            left_points = list(zip(lx.tolist(), ly.tolist()))
            if len(lx) >= 3:
                try:
                    left_fit = np.polyfit(ly, lx, 2)
                except (np.linalg.LinAlgError, ValueError):
                    pass

        if len(right_lane_inds) > 0:
            rx, ry = nonzero_x[right_lane_inds], nonzero_y[right_lane_inds]
            right_points = list(zip(rx.tolist(), ry.tolist()))
            if len(rx) >= 3:
                try:
                    right_fit = np.polyfit(ry, rx, 2)
                except (np.linalg.LinAlgError, ValueError):
                    pass

        return left_fit, right_fit, left_points, right_points, debug_img


# ═════════════════════════════════════════════════════════════
# Core Lane Detector v2
# ═════════════════════════════════════════════════════════════

class PointsOfOrientationLane:
    """
    Advanced lane detector combining:
      - AdaptivePreprocessor (shadow/lighting robustness via CLAHE + multi-colorspace)
      - Bird's-Eye View (BEV) perspective transform
      - Sliding-window histogram lane finding (handles 90° turns)
      - Kalman filter tracking with velocity prediction
      - Persistent reference ROI for recovery after lane loss
      - Virtual marker interpolation for single-edge scenarios
      - Junction detection and gap bridging across intersections
    """
    def __init__(
        self,
        heights_frac: Tuple[float, float] = (0.92, 0.60),
        n_heights: int = 12,
        roi_top_frac: float = 0.50,
        canny1: int = 50,
        canny2: int = 150,
        search_margin_px: int = 220,
        min_lane_width_px: int = 30,
        ema_center_alpha: float = 0.30,
        max_frames_lost: int = 45,
        use_virtual_markers: bool = True,
        use_bev: bool = True,
        use_sliding_window: bool = True,
        frame_w: int = 640,
        frame_h: int = 480,
    ):
        self.heights_frac = heights_frac
        self.n_heights = n_heights
        self.roi_top_frac = roi_top_frac
        self.canny1, self.canny2 = canny1, canny2
        self.search_margin_px = search_margin_px
        self.min_lane_width_px = min_lane_width_px
        self.max_frames_lost = max_frames_lost
        self.use_virtual_markers = use_virtual_markers
        self.use_bev = use_bev
        self.use_sliding_window = use_sliding_window

        # Core components
        self.preprocessor = AdaptivePreprocessor()
        self.bev = BirdsEyeView(frame_w, frame_h) if use_bev else None
        self.slider = SlidingWindowLaneFinder(
            n_windows=12, margin=60, min_pix=25, max_gap_windows=5
        ) if use_sliding_window else None

        # Tracking state
        self.center_ema = EMA(alpha=ema_center_alpha)
        self.left_kalman = SimpleKalmanFilter1D(process_noise=1.0, measurement_noise=3.0)
        self.right_kalman = SimpleKalmanFilter1D(process_noise=1.0, measurement_noise=3.0)
        self.center_kalman = SimpleKalmanFilter1D(process_noise=0.5, measurement_noise=2.0)

        self.last_good_center = None
        self.frames_lost = 0
        self.frame_count = 0

        self.last_left_poly = None
        self.last_right_poly = None
        self.lane_width_history = deque(maxlen=30)

        # Persistent Reference ROI — first good detection stored as anchor
        self.reference_left_poly = None
        self.reference_right_poly = None
        self.reference_lane_width = None
        self.reference_set = False
        self._high_confidence_count = 0

        # Junction / intersection state
        self.junction_detected = False
        self.junction_cooldown = 0

        # 90° turn state
        self.turn_detected = False
        self.turn_direction = 0  # -1=left, 0=none, 1=right
        self.pre_turn_poly_left = None
        self.pre_turn_poly_right = None

        # Frame dimensions
        self._frame_w = frame_w
        self._frame_h = frame_h
        self._bev_initialised = False

    def _ensure_bev(self, w: int, h: int):
        if self.use_bev and (w != self._frame_w or h != self._frame_h or not self._bev_initialised):
            self._frame_w, self._frame_h = w, h
            self.bev = BirdsEyeView(w, h)
            self._bev_initialised = True

    def _roi_mask(self, h: int, w: int) -> np.ndarray:
        roi = np.zeros((h, w), dtype=np.uint8)
        top = int(self.roi_top_frac * h)
        poly = np.array([
            [int(0.02 * w), h], [int(0.35 * w), top],
            [int(0.65 * w), top], [int(0.98 * w), h]
        ], dtype=np.int32)
        cv2.fillPoly(roi, [poly], 255)
        return roi

    def _detect_junction(self, binary: np.ndarray, h: int, w: int) -> bool:
        """Detect intersections via unusual white density in mid-frame band."""
        if self.junction_cooldown > 0:
            self.junction_cooldown -= 1
            return self.junction_detected
        mid_strip = binary[int(0.55 * h):int(0.75 * h), :]
        density = cv2.countNonZero(mid_strip) / max(mid_strip.size, 1)
        if density > 0.22:
            self.junction_detected = True
            self.junction_cooldown = 8
            return True
        self.junction_detected = False
        return False

    def _detect_90_turn(self, left_fit, right_fit, h: int, w: int) -> int:
        """Detect approaching 90° turn: -1=left, 0=straight, 1=right."""
        if left_fit is None and right_fit is not None:
            return -1
        if right_fit is None and left_fit is not None:
            return 1
        if left_fit is not None and right_fit is not None:
            y_top = int(0.3 * h)
            xl_top, xr_top = np.polyval(left_fit, y_top), np.polyval(right_fit, y_top)
            xl_bot, xr_bot = np.polyval(left_fit, h - 1), np.polyval(right_fit, h - 1)
            width_bot = xr_bot - xl_bot
            if width_bot > 0:
                ratio = (xr_top - xl_top) / max(width_bot, 1)
                if ratio < 0.3:
                    mid_top = (xl_top + xr_top) / 2
                    mid_bot = (xl_bot + xr_bot) / 2
                    if mid_top < mid_bot - 30:
                        return -1
                    elif mid_top > mid_bot + 30:
                        return 1
        return 0

    def _update_reference(self, left_fit, right_fit, quality: float):
        """Store first high-confidence detection as persistent reference."""
        if quality > 0.6:
            self._high_confidence_count += 1
        else:
            self._high_confidence_count = max(0, self._high_confidence_count - 1)
        if not self.reference_set and self._high_confidence_count >= 5:
            if left_fit is not None and right_fit is not None:
                self.reference_left_poly = left_fit.copy()
                self.reference_right_poly = right_fit.copy()
                if len(self.lane_width_history) > 0:
                    self.reference_lane_width = float(np.mean(self.lane_width_history))
                self.reference_set = True

    def _recover_from_reference(self, left_fit, right_fit, h: int):
        """Use reference + Kalman prediction to reconstruct missing lanes."""
        if not self.reference_set:
            return left_fit, right_fit
        ref_width = self.reference_lane_width or 100
        if left_fit is None and right_fit is not None:
            left_fit = np.array([right_fit[0], right_fit[1], right_fit[2] - ref_width])
        elif right_fit is None and left_fit is not None:
            right_fit = np.array([left_fit[0], left_fit[1], left_fit[2] + ref_width])
        elif left_fit is None and right_fit is None:
            if self.center_kalman.initialised:
                predicted = self.center_kalman.predict()
                left_fit = np.array([0.0, 0.0, predicted - ref_width / 2])
                right_fit = np.array([0.0, 0.0, predicted + ref_width / 2])
            elif self.reference_left_poly is not None:
                left_fit = self.reference_left_poly.copy()
                right_fit = self.reference_right_poly.copy()
        return left_fit, right_fit

    def detect(self, frame_bgr: np.ndarray) -> Tuple[List, List, List, Dict]:
        """
        Full detection pipeline returning smooth lane points for steering.
        Dual-path: BEV sliding-window (primary) + scanline (fallback).
        """
        self.frame_count += 1
        h, w = frame_bgr.shape[:2]
        self._ensure_bev(w, h)

        # Step 1: Adaptive preprocessing
        roi_mask = self._roi_mask(h, w)
        edges = self.preprocessor.get_edges(frame_bgr, roi_mask)
        lane_mask = self.preprocessor.extract_lane_mask(frame_bgr)
        lane_mask_roi = cv2.bitwise_and(lane_mask, roi_mask)

        # Step 2: Junction detection
        is_junction = self._detect_junction(lane_mask_roi, h, w)

        # Step 3: Dual-path lane finding
        left_fit_bev, right_fit_bev = None, None
        bev_quality, scan_quality = 0.0, 0.0
        sliding_debug = None

        # Path A: BEV + Sliding Window
        if self.use_bev and self.use_sliding_window and self.bev is not None:
            bev_binary = self.bev.warp(lane_mask_roi)
            left_fit_bev, right_fit_bev, lp, rp, sliding_debug = self.slider.find_lanes(
                bev_binary, self.last_left_poly, self.last_right_poly)
            bev_quality = min(1.0, (len(lp) + len(rp)) / 200.0)

        # Path B: Classic scanline
        left_fit_scan, right_fit_scan, scan_quality = self._scanline_detect(
            edges, lane_mask_roi, h, w)

        # Step 4: Fuse results
        if bev_quality > 0.3 and bev_quality >= scan_quality:
            left_fit, right_fit = left_fit_bev, right_fit_bev
            combined_quality = bev_quality
            detection_method = "bev_sliding"
        elif scan_quality > 0.2:
            left_fit, right_fit = left_fit_scan, right_fit_scan
            combined_quality = scan_quality
            detection_method = "scanline"
        else:
            if bev_quality > scan_quality:
                left_fit, right_fit = left_fit_bev, right_fit_bev
                combined_quality = bev_quality
            else:
                left_fit, right_fit = left_fit_scan, right_fit_scan
                combined_quality = scan_quality
            detection_method = "fallback"

        # Step 5: 90° turn detection
        turn_dir = self._detect_90_turn(left_fit, right_fit, h, w)
        if turn_dir != 0 and not self.turn_detected:
            self.turn_detected = True
            self.turn_direction = turn_dir
            self.pre_turn_poly_left = self.last_left_poly
            self.pre_turn_poly_right = self.last_right_poly
        elif turn_dir == 0 and self.turn_detected:
            self.turn_detected = False
            self.turn_direction = 0

        # Step 6: Virtual markers + reference recovery
        virtual_info = {"used_virtual_left": False, "used_virtual_right": False,
                        "used_reference": False}

        if self.use_virtual_markers and len(self.lane_width_history) > 0:
            avg_width = np.mean(self.lane_width_history)
            if left_fit is None and right_fit is not None:
                left_fit = np.array([right_fit[0], right_fit[1], right_fit[2] - avg_width])
                virtual_info["used_virtual_left"] = True
            elif right_fit is None and left_fit is not None:
                right_fit = np.array([left_fit[0], left_fit[1], left_fit[2] + avg_width])
                virtual_info["used_virtual_right"] = True

        if left_fit is None or right_fit is None:
            left_fit, right_fit = self._recover_from_reference(left_fit, right_fit, h)
            if left_fit is not None and right_fit is not None:
                virtual_info["used_reference"] = True

        # Step 7: Lane width update (skip during junctions)
        if not is_junction and left_fit is not None and right_fit is not None:
            y_bottom = int(0.85 * h)
            width = np.polyval(right_fit, y_bottom) - np.polyval(left_fit, y_bottom)
            if self.min_lane_width_px < width < w * 0.8:
                self.lane_width_history.append(width)

        self.last_left_poly = left_fit
        self.last_right_poly = right_fit
        if combined_quality > 0.5:
            self._update_reference(left_fit, right_fit, combined_quality)

        # Step 8: Generate smooth output points with Kalman smoothing
        ys = np.linspace(int(self.heights_frac[0] * h),
                         int(self.heights_frac[1] * h),
                         self.n_heights).astype(int)
        smooth_lefts, smooth_rights, smooth_centers = [], [], []
        if left_fit is not None and right_fit is not None:
            for y in ys:
                xl = np.polyval(left_fit, y)
                xr = np.polyval(right_fit, y)
                xl_s = self.left_kalman.update(xl)
                xr_s = self.right_kalman.update(xr)
                cx_s = self.center_kalman.update((xl_s + xr_s) / 2)
                smooth_lefts.append((xl_s, float(y)))
                smooth_rights.append((xr_s, float(y)))
                smooth_centers.append((cx_s, float(y)))

        # Step 9: Tracking management
        if len(smooth_centers) > 0:
            self.last_good_center = self.center_ema.update(smooth_centers[0][0])
            self.frames_lost = 0
        else:
            self.center_ema.update(None)
            self.frames_lost += 1
            if self.center_kalman.initialised:
                self.last_good_center = self.center_kalman.predict()
            if self.frames_lost > self.max_frames_lost:
                self.last_good_center = None
                self.last_left_poly = None
                self.last_right_poly = None
                self.center_ema.reset()
                self.left_kalman.reset()
                self.right_kalman.reset()
                self.center_kalman.reset()

        debug = {
            "edges": edges, "lane_mask": lane_mask_roi,
            "quality": combined_quality, "frames_lost": self.frames_lost,
            "virtual_markers": virtual_info, "detection_method": detection_method,
            "is_junction": is_junction,
            "turn_detected": self.turn_detected, "turn_direction": self.turn_direction,
            "reference_set": self.reference_set,
            "bev_quality": bev_quality, "scan_quality": scan_quality,
        }
        if sliding_debug is not None:
            debug["sliding_windows"] = sliding_debug
        return smooth_centers, smooth_lefts, smooth_rights, debug

    def _scanline_detect(self, edges, lane_mask, h, w):
        """Classic scanline detection using both edges and lane mask."""
        ys = np.linspace(int(self.heights_frac[0] * h),
                         int(self.heights_frac[1] * h),
                         self.n_heights).astype(int)
        x_center_ref = self.last_good_center if self.last_good_center is not None else w / 2
        raw_lefts, raw_rights = [], []

        for y in ys:
            y_idx = int(np.clip(y, 0, h - 1))
            combined_row = cv2.bitwise_or(edges[y_idx, :], lane_mask[y_idx, :])
            margin = self.search_margin_px
            if self.frames_lost > 3:
                margin = min(w // 2, margin + self.frames_lost * 10)
            l_bound = max(0, int(x_center_ref - margin))
            r_bound = min(w - 1, int(x_center_ref + margin))

            xL, xR = None, None
            for x in range(int(x_center_ref), l_bound - 1, -1):
                if combined_row[x] != 0:
                    xL = x; break
            for x in range(int(x_center_ref), r_bound + 1, 1):
                if combined_row[x] != 0:
                    xR = x; break

            if xL is not None and xR is not None and (xR - xL) >= self.min_lane_width_px:
                raw_lefts.append((xL, y))
                raw_rights.append((xR, y))
                x_center_ref = 0.5 * (xL + xR)
                self.lane_width_history.append(xR - xL)

        quality = min(1.0, len(raw_lefts) / self.n_heights) if self.n_heights > 0 else 0
        left_fit = self._fit_poly(raw_lefts) or self.last_left_poly
        right_fit = self._fit_poly(raw_rights) or self.last_right_poly
        return left_fit, right_fit, quality

    @staticmethod
    def _fit_poly(points):
        if len(points) < 3:
            return None
        xs = np.array([p[0] for p in points], dtype=np.float32)
        ys = np.array([p[1] for p in points], dtype=np.float32)
        try:
            return np.polyfit(ys, xs, 2)
        except (np.linalg.LinAlgError, ValueError):
            return None

    def compute_steering(self, centers: List[Tuple[float, float]],
                         frame_w: int, k_gain: float = 1.15
                         ) -> Tuple[Optional[float], Optional[Tuple[float, float]]]:
        """Pure Pursuit steering with curvature-adaptive gain and Kalman fallback."""
        if len(centers) < 3:
            if self.center_kalman.initialised:
                predicted = self.center_kalman.predict()
                err = np.clip((predicted - frame_w / 2.0) / (frame_w / 2.0), -1.0, 1.0)
                return float(np.clip(k_gain * err, -1.0, 1.0)), (predicted, float(self._frame_h * 0.7))
            return None, None

        xs = np.array([p[0] for p in centers], dtype=np.float32)
        ys = np.array([p[1] for p in centers], dtype=np.float32)
        try:
            poly = np.polyfit(ys, xs, 2)
        except np.linalg.LinAlgError:
            return None, None

        y_look = 0.3 * ys.min() + 0.7 * ys.max()
        x_look = np.polyval(poly, y_look)
        # Curvature-adaptive gain: reduce in sharp curves to prevent oscillation
        curvature = abs(poly[0])
        adaptive_gain = max(0.5, min(k_gain, k_gain / (1.0 + 5.0 * curvature)))
        err = np.clip((x_look - frame_w / 2.0) / (frame_w / 2.0), -1.0, 1.0)
        return float(np.clip(adaptive_gain * err, -1.0, 1.0)), (float(x_look), float(y_look))

    def reset(self):
        """Full reset of all tracking state."""
        self.center_ema.reset()
        self.left_kalman.reset()
        self.right_kalman.reset()
        self.center_kalman.reset()
        self.last_good_center = None
        self.last_left_poly = None
        self.last_right_poly = None
        self.frames_lost = 0
        self.lane_width_history.clear()
        self.reference_set = False
        self.reference_left_poly = None
        self.reference_right_poly = None
        self.reference_lane_width = None
        self._high_confidence_count = 0
        self.junction_detected = False
        self.junction_cooldown = 0
        self.turn_detected = False
        self.turn_direction = 0


# ═════════════════════════════════════════════════════════════
# Visualisation
# ═════════════════════════════════════════════════════════════

def draw_debug(frame: np.ndarray, centers: List, lefts: List, rights: List,
               target_point: Optional[Tuple], debug: Dict) -> np.ndarray:
    out = frame.copy()
    vi = debug.get("virtual_markers", {})

    # Lane polygon fill
    if len(lefts) > 1 and len(rights) > 1:
        overlay = frame.copy()
        cv2.fillPoly(overlay, [np.array(lefts + list(reversed(rights)), dtype=np.int32)],
                     (200, 255, 200))
        out = cv2.addWeighted(frame, 0.4, overlay, 0.6, 0)

    # Lane boundary lines (orange if virtual, else red/blue)
    if len(lefts) > 1:
        color = (0, 165, 255) if vi.get("used_virtual_left") else (0, 0, 255)
        cv2.polylines(out, [np.array(lefts, dtype=np.int32).reshape((-1, 1, 2))],
                      False, color, 4, cv2.LINE_AA)
    if len(rights) > 1:
        color = (0, 165, 255) if vi.get("used_virtual_right") else (255, 0, 0)
        cv2.polylines(out, [np.array(rights, dtype=np.int32).reshape((-1, 1, 2))],
                      False, color, 4, cv2.LINE_AA)
    if len(centers) > 1:
        cv2.polylines(out, [np.array(centers, dtype=np.int32).reshape((-1, 1, 2))],
                      False, (0, 255, 0), 2, cv2.LINE_AA)

    y_off = 60
    if vi.get("used_virtual_left"):
        cv2.putText(out, "[VIRTUAL LEFT]", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2); y_off += 25
    if vi.get("used_virtual_right"):
        cv2.putText(out, "[VIRTUAL RIGHT]", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2); y_off += 25
    if vi.get("used_reference"):
        cv2.putText(out, "[REFERENCE RECOVERY]", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2); y_off += 25
    if debug.get("is_junction"):
        cv2.putText(out, "[JUNCTION]", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2); y_off += 25
    if debug.get("turn_detected"):
        label = "LEFT" if debug["turn_direction"] < 0 else "RIGHT"
        cv2.putText(out, f"[90-DEG TURN {label}]", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2); y_off += 25

    method = debug.get("detection_method", "?")
    cv2.putText(out, f"Method: {method}", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1); y_off += 20
    bev_q, scan_q = debug.get("bev_quality", 0), debug.get("scan_quality", 0)
    cv2.putText(out, f"BEV:{bev_q:.0%} Scan:{scan_q:.0%}", (15, y_off), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    if target_point:
        cv2.circle(out, (int(target_point[0]), int(target_point[1])), 8, (0, 255, 255), -1)
    if debug["frames_lost"] > 5:
        cv2.putText(out, f"LANE LOST ({debug['frames_lost']}f)", (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    return out


# ═════════════════════════════════════════════════════════════
# Main Execution Loop
# ═════════════════════════════════════════════════════════════

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Cannot open camera/video.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    detector = PointsOfOrientationLane(use_bev=True, use_sliding_window=True,
                                       frame_w=640, frame_h=480)
    fps_counter = deque(maxlen=30)
    last_time = time.time()
    show_bev = False

    print("[*] Lane Detector v2: CLAHE + Shadow Removal + BEV + Sliding Window + Kalman + Reference ROI")
    print("[*] Press Q/ESC=quit, R=reset, B=toggle BEV view")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        centers, lefts, rights, debug = detector.detect(frame)
        steer, target = detector.compute_steering(centers, frame.shape[1])

        now = time.time()
        dt = now - last_time
        if dt > 0:
            fps_counter.append(1.0 / dt)
        last_time = now

        vis = draw_debug(frame, centers, lefts, rights, target, debug)
        steer_str = f"{steer:+.3f}" if steer is not None else "LOST"
        fps_str = f"FPS: {np.mean(fps_counter):.1f}" if fps_counter else "FPS: 0"
        ref_str = "REF:OK" if detector.reference_set else "REF:--"
        cv2.putText(vis, f"{fps_str} | Steer: {steer_str} | {ref_str}",
                    (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        cv2.imshow("Lane Detection v2", vis)
        cv2.imshow("Edges + Lane Mask", debug["edges"])
        if show_bev and "sliding_windows" in debug:
            cv2.imshow("BEV Sliding Windows", debug["sliding_windows"])

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break
        elif key == ord('r'):
            detector.reset()
            print("[*] Tracking reset")
        elif key == ord('b'):
            show_bev = not show_bev
            if not show_bev:
                cv2.destroyWindow("BEV Sliding Windows")
            print(f"[*] BEV view: {'ON' if show_bev else 'OFF'}")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()