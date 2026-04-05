#!/usr/bin/env python3
"""
Sliding Window Lane Follower — BEV + Polynomial Regression + Kalman Filter

Pipeline:
  1. Camera frame -> grayscale -> binary mask (white line detection)
  2. Perspective transform -> Bird's Eye View (BEV)
  3. Histogram on bottom half -> find right white line base
  4. Sliding windows from bottom to top -> collect centroids
  5. Polynomial fit: x = a*y^2 + b*y + c  (right line curve)
  6. Lane center = right_curve_x - lane_width / 2
  7. CTE = (lane_center - car_center) / norm -> PID -> steering
  8. Kalman filter for smoothing + hold-over prediction when line lost
  9. Targeted search: previous polynomial +/- margin for next frame
 10. Intersection detection via horizontal line scan

Sign convention:
    correction > 0 -> car drifted LEFT  -> steer RIGHT
    correction < 0 -> car drifted RIGHT -> steer LEFT

All parameters are read from LF_* environment variables (lane.env).
"""

import logging
import os
import numpy as np

logger = logging.getLogger('line_follower')


def _env_int(key, default):
    return int(os.environ.get(key, default))


def _env_float(key, default):
    return float(os.environ.get(key, default))


# ═════════════════════════════════════════════════════════════
#  Kalman Filter  (state = [cte, d_cte/dt])
# ═════════════════════════════════════════════════════════════
class LaneKalmanFilter:
    """1-D Kalman filter tracking Cross-Track Error.

    When vision measurement is available it fuses prediction + observation.
    When the line is lost, pure prediction keeps the car on course for a
    short while (dynamic model: CTE propagates with its rate).
    """

    def __init__(self, q=0.005, r_normal=0.05, r_degraded=0.20):
        self.x = np.array([0.0, 0.0])          # [cte, cte_rate]
        self.P = np.eye(2) * 0.5
        self.Q = np.diag([q, q * 5])
        self.R_normal = r_normal
        self.R_degraded = r_degraded
        self.F = np.array([[1.0, 1.0],
                           [0.0, 1.0]])
        self.H = np.array([[1.0, 0.0]])

    def predict(self):
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return float(self.x[0])

    def update(self, z_cte, degraded=False):
        R = self.R_degraded if degraded else self.R_normal
        innovation = z_cte - float((self.H @ self.x).item())
        S = float((self.H @ self.P @ self.H.T).item()) + R
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * innovation
        self.P = (np.eye(2) - K @ self.H) @ self.P
        return float(self.x[0])

    @property
    def cte(self):
        return float(self.x[0])

    @property
    def innovation_gate(self):
        return float(np.sqrt(max(float((self.H @ self.P @ self.H.T).item()) + self.R_normal, 0.001)))

    def reset(self, cte=0.0):
        self.x = np.array([cte, 0.0])
        self.P = np.eye(2) * 0.5


# ═════════════════════════════════════════════════════════════
#  EMA Filter
# ═════════════════════════════════════════════════════════════
class EMAFilter:
    def __init__(self, alpha=0.4):
        self.alpha = alpha
        self.value = None

    def update(self, new_value):
        if new_value is None:
            return self.value if self.value is not None else 0.0
        if self.value is None:
            self.value = new_value
        else:
            self.value = self.alpha * new_value + (1.0 - self.alpha) * self.value
        return float(self.value)

    def reset(self):
        self.value = None


# ═════════════════════════════════════════════════════════════
#  LineFollower — main class
# ═════════════════════════════════════════════════════════════
class LineFollower:
    """
    Sliding-window lane follower on Bird's Eye View imagery.

    PRIMARY target: inner edge of the RIGHT white line.
    Lane center  = right_line_x - lane_width_bev / 2.
    CTE          = (lane_center - car_x_bev) / half_bev_w.

    Frame-to-frame continuity:
      - Targeted search: +/-margin around previous polynomial (no full scan)
      - Kalman filter: predicts CTE when line is temporarily invisible
      - Line lock: once the right line is acquired, stay locked
    """

    def __init__(self):
        self._ready = False
        self._cv2 = None

        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            logger.error("OpenCV (cv2) not available — line-following disabled")
            return

        # ── Frame dimensions ──
        self.frame_w = _env_int('LF_FRAME_WIDTH', 640)
        self.frame_h = _env_int('LF_FRAME_HEIGHT', 480)

        # ── BEV dimensions ──
        self.bev_w = _env_int('LF_BEV_WIDTH', 300)
        self.bev_h = _env_int('LF_BEV_HEIGHT', 300)

        # ── BEV perspective transform ──
        fw, fh = float(self.frame_w), float(self.frame_h)
        bw, bh = float(self.bev_w), float(self.bev_h)

        src = np.float32([
            [fw * _env_float('LF_BEV_SRC_TL_X', 0.30),
             fh * _env_float('LF_BEV_SRC_TL_Y', 0.50)],
            [fw * _env_float('LF_BEV_SRC_TR_X', 0.70),
             fh * _env_float('LF_BEV_SRC_TR_Y', 0.50)],
            [fw * _env_float('LF_BEV_SRC_BR_X', 1.00),
             fh * _env_float('LF_BEV_SRC_BR_Y', 0.95)],
            [fw * _env_float('LF_BEV_SRC_BL_X', 0.00),
             fh * _env_float('LF_BEV_SRC_BL_Y', 0.95)],
        ])
        dst = np.float32([
            [bw * _env_float('LF_BEV_DST_TL_X', 0.10),
             bh * _env_float('LF_BEV_DST_TL_Y', 0.0)],
            [bw * _env_float('LF_BEV_DST_TR_X', 0.90),
             bh * _env_float('LF_BEV_DST_TR_Y', 0.0)],
            [bw * _env_float('LF_BEV_DST_BR_X', 0.90),
             bh * _env_float('LF_BEV_DST_BR_Y', 1.0)],
            [bw * _env_float('LF_BEV_DST_BL_X', 0.10),
             bh * _env_float('LF_BEV_DST_BL_Y', 1.0)],
        ])
        self._bev_matrix = cv2.getPerspectiveTransform(src, dst)
        self._bev_matrix_inv = cv2.getPerspectiveTransform(dst, src)

        # ── Lane geometry (in BEV pixel space) ──
        self.lane_width_bev = _env_int('LF_LANE_WIDTH_BEV', 140)
        self._target_right_x_bev = _env_int('LF_TARGET_RIGHT_X_BEV', 210)

        # ── Binary mask parameters ──
        self._binary_threshold = _env_int('LF_BINARY_THRESHOLD', 180)
        self._blur_kernel = _env_int('LF_BLUR_KERNEL', 5)
        self._morph_kernel_size = _env_int('LF_MORPH_KERNEL', 3)
        self._morph_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (self._morph_kernel_size, self._morph_kernel_size))

        # ── Sliding window parameters ──
        self._n_windows = _env_int('LF_N_WINDOWS', 9)
        self._window_margin = _env_int('LF_WINDOW_MARGIN', 40)
        self._min_pix_recenter = _env_int('LF_MIN_PIX_RECENTER', 30)

        # ── Polynomial / targeted search ──
        self._poly_order = 2
        self._prev_right_poly = None
        self._poly_locked = False
        self._search_margin = _env_int('LF_SEARCH_MARGIN', 50)
        self._lock_lost_frames = 0
        self._lock_lost_max = _env_int('LF_LOCK_LOST_MAX', 15)

        # ── PID controller ──
        self.Kp = _env_float('LF_PID_KP', 0.85)
        self.Ki = _env_float('LF_PID_KI', 0.05)
        self.Kd = _env_float('LF_PID_KD', 0.60)
        self._integral = 0.0
        self._integral_max = _env_float('LF_PID_INTEGRAL_MAX', 2.0)
        self._prev_cte = 0.0
        self._steer_ema = EMAFilter(alpha=_env_float('LF_STEER_EMA_ALPHA', 0.3))

        # ── Kalman filter ──
        self._kalman = LaneKalmanFilter(
            q=_env_float('LF_KALMAN_Q', 0.005),
            r_normal=_env_float('LF_KALMAN_R_NORMAL', 0.05),
            r_degraded=_env_float('LF_KALMAN_R_DEGRADED', 0.20),
        )
        self._kalman_gate = _env_float('LF_KALMAN_GATE', 3.5)

        # ── Intersection detection ──
        self._horiz_threshold = _env_float('LF_HORIZ_LINE_THRESH', 0.40)
        self._intersection_detected = False

        # ── Recovery / line-loss state ──
        self._frames_no_line = 0
        self._max_predict_frames = _env_int('LF_MAX_PREDICT_FRAMES', 20)

        # ── Adaptive lane width EMA ──
        self._lane_width_ema = float(self.lane_width_bev)

        # ── Evaluation Y (where we measure CTE in BEV, near bottom = near car) ──
        self._eval_y = int(self.bev_h * _env_float('LF_EVAL_Y_FRAC', 0.85))
        # Car X in BEV coordinate (camera centre maps here)
        self._car_x_bev = self.bev_w / 2.0

        self._ready = True
        logger.info(
            "LineFollower ready (Sliding Window BEV %dx%d | lane_w=%dpx "
            "| Kp=%.2f Ki=%.2f Kd=%.2f)",
            self.bev_w, self.bev_h, self.lane_width_bev,
            self.Kp, self.Ki, self.Kd
        )

    # ─────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────
    @property
    def is_ready(self):
        return self._ready

    def reset(self):
        """Reset all tracking state — call when starting a new segment."""
        self._integral = 0.0
        self._prev_cte = 0.0
        self._steer_ema.reset()
        self._kalman.reset()
        self._prev_right_poly = None
        self._poly_locked = False
        self._lock_lost_frames = 0
        self._frames_no_line = 0
        self._intersection_detected = False
        self._lane_width_ema = float(self.lane_width_bev)

    # ─────────────────────────────────────────────────────────
    # Image pre-processing
    # ─────────────────────────────────────────────────────────
    def _get_binary_mask(self, bgr):
        cv2 = self._cv2
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        k = self._blur_kernel
        blur = cv2.GaussianBlur(gray, (k, k), 0)
        _, mask = cv2.threshold(blur, self._binary_threshold, 255, cv2.THRESH_BINARY)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._morph_kernel)

    def _to_bev(self, mask):
        return self._cv2.warpPerspective(mask, self._bev_matrix,
                                         (self.bev_w, self.bev_h))

    # ─────────────────────────────────────────────────────────
    # Histogram peak (initial line base detection)
    # ─────────────────────────────────────────────────────────
    def _histogram_right_base(self, bev_mask):
        """Histogram of bottom half -> peak X in right half = line base."""
        bottom_half = bev_mask[self.bev_h // 2:, :]
        histogram = np.sum(bottom_half > 0, axis=0)

        midpoint = self.bev_w // 2
        right_hist = histogram[midpoint:]

        if np.max(right_hist) < 10:
            return None

        return midpoint + int(np.argmax(right_hist))

    # ─────────────────────────────────────────────────────────
    # Sliding-window search (full scan — used when no lock)
    # ─────────────────────────────────────────────────────────
    def _sliding_window_search(self, bev_mask, base_x):
        """From base_x at the bottom, slide windows upward collecting centroids."""
        win_h = self.bev_h // self._n_windows
        margin = self._window_margin
        centroids = []
        cur_x = base_x
        window_rects = []

        for i in range(self._n_windows):
            y_bot = self.bev_h - i * win_h
            y_top = self.bev_h - (i + 1) * win_h
            x_left = max(0, cur_x - margin)
            x_right = min(self.bev_w, cur_x + margin)

            window_rects.append((x_left, y_top, x_right, y_bot))

            roi = bev_mask[y_top:y_bot, x_left:x_right]
            nonzero_x = np.nonzero(roi)[1]

            cy = (y_top + y_bot) // 2
            if len(nonzero_x) >= self._min_pix_recenter:
                cx = int(np.mean(nonzero_x)) + x_left
                centroids.append((cx, cy))
                cur_x = cx

        return centroids, window_rects

    # ─────────────────────────────────────────────────────────
    # Targeted search (use previous polynomial +/- margin)
    # ─────────────────────────────────────────────────────────
    def _targeted_search(self, bev_mask, prev_poly):
        """Search only within +/-margin of previous polynomial curve."""
        margin = self._search_margin
        win_h = self.bev_h // self._n_windows
        centroids = []
        window_rects = []

        for i in range(self._n_windows):
            y_bot = self.bev_h - i * win_h
            y_top = self.bev_h - (i + 1) * win_h
            cy = (y_top + y_bot) // 2

            expected_x = int(np.polyval(prev_poly, cy))
            x_left = max(0, expected_x - margin)
            x_right = min(self.bev_w, expected_x + margin)

            window_rects.append((x_left, y_top, x_right, y_bot))

            if x_left >= x_right:
                continue

            roi = bev_mask[y_top:y_bot, x_left:x_right]
            nonzero_x = np.nonzero(roi)[1]

            if len(nonzero_x) >= self._min_pix_recenter:
                cx = int(np.mean(nonzero_x)) + x_left
                centroids.append((cx, cy))

        return centroids, window_rects

    # ─────────────────────────────────────────────────────────
    # Polynomial fitting
    # ─────────────────────────────────────────────────────────
    def _fit_polynomial(self, centroids):
        """Fit  x = a*y^2 + b*y + c  through centroids."""
        if len(centroids) < 3:
            return None
        xs = np.array([c[0] for c in centroids], dtype=np.float64)
        ys = np.array([c[1] for c in centroids], dtype=np.float64)
        try:
            return np.polyfit(ys, xs, self._poly_order)
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────
    # Intersection detection (horizontal white line)
    # ─────────────────────────────────────────────────────────
    def _detect_horizontal_line(self, bev_mask):
        """True if a horizontal white line spans > threshold of BEV width."""
        mid_start = self.bev_h // 3
        mid_end = 2 * self.bev_h // 3
        for y in range(mid_start, mid_end, 3):
            white_ratio = np.sum(bev_mask[y, :] > 0) / float(self.bev_w)
            if white_ratio > self._horiz_threshold:
                return True
        return False

    # ─────────────────────────────────────────────────────────
    # Core processing pipeline
    # ─────────────────────────────────────────────────────────
    def _process_core(self, frame):
        """Core lane-following pipeline. Returns dict with all results."""
        cv2 = self._cv2

        h, w = frame.shape[:2]
        if w != self.frame_w or h != self.frame_h:
            frame = cv2.resize(frame, (self.frame_w, self.frame_h))
        mask = self._get_binary_mask(frame)
        bev_mask = self._to_bev(mask)

        # ── Intersection check (skip every other frame when locked) ──
        if not self._poly_locked or (self._frames_no_line % 2 == 0):
            intersection = self._detect_horizontal_line(bev_mask)
        else:
            intersection = self._intersection_detected
        if intersection and not self._intersection_detected:
            self._intersection_detected = True
            self._poly_locked = False
            self._prev_right_poly = None
            self._lock_lost_frames = 0
            logger.info("Intersection detected — polynomial lock released")
        elif not intersection:
            self._intersection_detected = False

        # ── Step 1: Detect right white line ──
        poly_coeffs = None
        centroids = []
        window_rects = []

        # Strategy A: Targeted search around previous polynomial
        if self._poly_locked and self._prev_right_poly is not None:
            centroids, window_rects = self._targeted_search(bev_mask, self._prev_right_poly)
            poly_coeffs = self._fit_polynomial(centroids)

            if poly_coeffs is None:
                self._lock_lost_frames += 1
                if self._lock_lost_frames > self._lock_lost_max:
                    self._poly_locked = False
                    self._prev_right_poly = None
                    self._lock_lost_frames = 0
                    logger.debug("Polynomial lock lost — falling back to histogram scan")

        # Strategy B: Full histogram + sliding window
        if poly_coeffs is None and not self._poly_locked:
            base_x = self._histogram_right_base(bev_mask)
            if base_x is not None:
                centroids, window_rects = self._sliding_window_search(bev_mask, base_x)
                poly_coeffs = self._fit_polynomial(centroids)

        # ── Step 2: Compute CTE ──
        predicted_cte = self._kalman.predict()
        raw_cte = None
        filtered_cte = predicted_cte
        confidence = 0.0
        status = 'SEARCH'
        lane_center_x = None
        right_x = None

        if poly_coeffs is not None:
            self._prev_right_poly = poly_coeffs
            self._poly_locked = True
            self._lock_lost_frames = 0
            self._frames_no_line = 0

            right_x = float(np.polyval(poly_coeffs, self._eval_y))
            lane_center_x = right_x - self._lane_width_ema / 2.0

            raw_cte = (lane_center_x - self._car_x_bev) / (self.bev_w / 2.0)
            raw_cte = float(np.clip(raw_cte, -1.5, 1.5))

            innovation = abs(raw_cte - predicted_cte)
            gate = self._kalman_gate * self._kalman.innovation_gate

            if innovation < gate:
                filtered_cte = self._kalman.update(raw_cte)
                confidence = 1.0
            else:
                filtered_cte = predicted_cte
                confidence = 0.3

            status = 'LOCKED'
        else:
            self._frames_no_line += 1
            filtered_cte = predicted_cte

            if self._frames_no_line > self._max_predict_frames:
                self._kalman.reset()
                filtered_cte = 0.0
                self._integral = 0.0
                self._prev_right_poly = None
                self._poly_locked = False

            status = 'PREDICT(%d)' % self._frames_no_line

        # ── Step 3: PID on filtered CTE ──
        if poly_coeffs is not None:
            self._integral += filtered_cte
            self._integral = float(np.clip(self._integral,
                                           -self._integral_max, self._integral_max))
        else:
            self._integral *= 0.85

        d_cte = filtered_cte - self._prev_cte
        self._prev_cte = filtered_cte

        steering = self.Kp * filtered_cte + self.Ki * self._integral + self.Kd * d_cte
        steering = float(np.clip(steering, -1.0, 1.0))
        steering = self._steer_ema.update(steering)

        return {
            'steering': steering,
            'filtered_cte': filtered_cte,
            'raw_cte': raw_cte,
            'confidence': confidence,
            'poly_coeffs': poly_coeffs,
            'centroids': centroids,
            'window_rects': window_rects,
            'bev_mask': bev_mask,
            'status': status,
            'intersection': intersection,
            'lane_center_x': lane_center_x,
            'right_x': right_x,
        }

    # ─────────────────────────────────────────────────────────
    # Public: analyse_frame  (used by AutoNavigator)
    # ─────────────────────────────────────────────────────────
    def analyse_frame(self, jpeg_bytes):
        """Analyse JPEG frame -> steering correction [-1, +1]."""
        if not self._ready or jpeg_bytes is None:
            return 0.0
        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is None:
                return 0.0
            result = self._process_core(frame)
            return result['steering']
        except Exception as e:
            logger.debug("analyse_frame error: %s", e)
            return 0.0

    # ─────────────────────────────────────────────────────────
    # Public: analyse_frame_full  (used by odometry sensor fusion)
    # ─────────────────────────────────────────────────────────
    def analyse_frame_full(self, jpeg_bytes):
        """Analyse frame and return steering + vision CTE + confidence.
        Returns dict: {steering, vision_cte, confidence, status, intersection}
        vision_cte is None when the line is not visible."""
        empty = {'steering': 0.0, 'vision_cte': None, 'confidence': 0.0,
                 'status': 'N/A', 'intersection': False}
        if not self._ready or jpeg_bytes is None:
            return empty
        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is None:
                return empty
            result = self._process_core(frame)
            return {
                'steering': result['steering'],
                'vision_cte': result['raw_cte'],
                'confidence': result['confidence'],
                'status': result['status'],
                'intersection': result['intersection'],
            }
        except Exception as e:
            logger.debug("analyse_frame_full error: %s", e)
            return empty

    # ─────────────────────────────────────────────────────────
    # Public: analyse_frame_debug  (used by debug streams)
    # ─────────────────────────────────────────────────────────
    def analyse_frame_debug(self, jpeg_bytes):
        """Analyse + return debug visualization + metadata."""
        empty = {
            'correction': 0.0,
            'raw_correction': 0.0,
            'mask_jpeg': None,
            'confidence': 0.0,
            'status': 'N/A',
            'intersection': False,
        }
        if not self._ready or jpeg_bytes is None:
            return empty

        try:
            cv2 = self._cv2
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return empty

            result = self._process_core(frame)
            viz = cv2.resize(frame, (self.frame_w, self.frame_h)).copy()
            bev_mask = result['bev_mask']
            centroids = result['centroids']
            ey = self._eval_y

            # ── Helper: BEV coords → camera-frame coords ──
            def bev2frame(pts):
                a = np.float32(pts).reshape(-1, 1, 2)
                fp = cv2.perspectiveTransform(a, self._bev_matrix_inv)
                return fp.reshape(-1, 2)

            # ═══════════════════════════════════════════════
            #  MAIN FRAME — full lane detection overlay
            # ═══════════════════════════════════════════════
            if centroids and len(centroids) >= 2:
                # Project right-line centroids → frame
                right_fp = bev2frame(centroids).astype(int)

                # Offset centroids → lane-centre line → frame
                hw = int(self._lane_width_ema / 2)
                center_bev = [(cx - hw, cy) for cx, cy in centroids]
                center_fp = bev2frame(center_bev).astype(int)

                # Semi-transparent green lane fill
                overlay = viz.copy()
                fill_pts = np.concatenate(
                    [right_fp, center_fp[::-1]], axis=0)
                cv2.fillPoly(overlay, [fill_pts], (0, 180, 0))
                cv2.addWeighted(overlay, 0.25, viz, 0.75, 0, viz)

                # Right line — straight segments (red) + centroid dots (yellow)
                for i in range(len(right_fp) - 1):
                    p1 = (int(right_fp[i][0]), int(right_fp[i][1]))
                    p2 = (int(right_fp[i + 1][0]), int(right_fp[i + 1][1]))
                    cv2.line(viz, p1, p2, (0, 0, 255), 2)
                for pt in right_fp:
                    cv2.circle(viz, (int(pt[0]), int(pt[1])), 4,
                               (0, 255, 255), -1)

                # Lane-centre line — straight segments (cyan)
                for i in range(len(center_fp) - 1):
                    p1 = (int(center_fp[i][0]), int(center_fp[i][1]))
                    p2 = (int(center_fp[i + 1][0]), int(center_fp[i + 1][1]))
                    cv2.line(viz, p1, p2, (255, 255, 0), 2)

            # Target dots at eval_y → projected to frame
            if result['right_x'] is not None:
                rx_f = bev2frame([(result['right_x'], ey)])[0]
                cv2.circle(viz, (int(rx_f[0]), int(rx_f[1])), 8,
                           (0, 0, 255), -1)
                cv2.putText(viz, "R",
                            (int(rx_f[0]) + 10, int(rx_f[1]) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            if result['lane_center_x'] is not None:
                lc_f = bev2frame([(result['lane_center_x'], ey)])[0]
                cv2.circle(viz, (int(lc_f[0]), int(lc_f[1])), 8,
                           (255, 255, 0), -1)
                cv2.putText(viz, "C",
                            (int(lc_f[0]) + 10, int(lc_f[1]) + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)

            car_f = bev2frame([(self._car_x_bev, ey)])[0]
            cv2.circle(viz, (int(car_f[0]), int(car_f[1])), 6,
                       (255, 0, 0), -1)

            # HUD text
            st = result['status']
            st_color = (0, 255, 0) if 'LOCKED' in st else (0, 165, 255)
            cv2.putText(viz, "STEER: %+.3f" % result['steering'],
                        (self.frame_w - 300, 30),
                        cv2.FONT_HERSHEY_DUPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(viz, "CTE: %+.3f  %s" % (result['filtered_cte'], st),
                        (self.frame_w - 300, 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, st_color, 2)

            if result['intersection']:
                cv2.putText(viz, "INTERSECTION",
                            (self.frame_w // 2 - 80, self.frame_h - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            # ═══════════════════════════════════════════════
            #  BEV OVERLAY — mask + 2 target dots only
            # ═══════════════════════════════════════════════
            bev_color = cv2.cvtColor(bev_mask, cv2.COLOR_GRAY2BGR)

            if result['lane_center_x'] is not None:
                lcx = int(result['lane_center_x'])
                cv2.circle(bev_color, (lcx, ey), 6, (255, 255, 0), -1)
                cv2.putText(bev_color, "C", (lcx + 8, ey + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 0), 1)
            if result['right_x'] is not None:
                rx = int(result['right_x'])
                cv2.circle(bev_color, (rx, ey), 6, (0, 0, 255), -1)
                cv2.putText(bev_color, "R", (rx + 8, ey + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

            cv2.rectangle(bev_color, (0, 0),
                          (self.bev_w - 1, self.bev_h - 1),
                          (128, 128, 128), 1)

            bev_h_disp = min(160, self.frame_h - 10)
            bev_w_disp = min(160, self.frame_w - 10)
            bev_small = cv2.resize(bev_color, (bev_w_disp, bev_h_disp))
            viz[5:5 + bev_h_disp, 5:5 + bev_w_disp] = bev_small

            kalman_txt = "Kalman=%+.3f  W=%.0f" % (
                self._kalman.cte, self._lane_width_ema)
            cv2.putText(viz, kalman_txt, (10, self.frame_h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

            _, jpeg_buf = cv2.imencode('.jpg', viz,
                                       [cv2.IMWRITE_JPEG_QUALITY, 85])

            return {
                'correction': result['steering'],
                'raw_correction': result['raw_cte'] if result['raw_cte'] is not None else 0.0,
                'mask_jpeg': bytes(jpeg_buf),
                'confidence': result['confidence'],
                'status': result['status'],
                'intersection': result['intersection'],
                'lane_center_x': result['lane_center_x'],
                'right_x': result['right_x'],
            }

        except Exception as e:
            logger.debug("analyse_frame_debug error: %s", e)
            return empty
