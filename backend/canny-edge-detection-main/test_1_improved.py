import cv2
import numpy as np
import os
from typing import Tuple, Optional


# ═══════════════════════════════════════════════════════════════
# Load all parameters from environment variables (canny.env)
# ═══════════════════════════════════════════════════════════════

def _env_int(key, default):
    return int(os.environ.get(key, default))

def _env_float(key, default):
    return float(os.environ.get(key, default))


class EMAFilter:
    def __init__(self, alpha: float = 0.4):
        self.alpha = alpha
        self.value = None

    def update(self, new_value: Optional[float]) -> float:
        if new_value is None:
            return self.value if self.value is not None else 0.0
        if self.value is None:
            self.value = new_value
        else:
            self.value = (self.alpha * new_value) + ((1.0 - self.alpha) * self.value)
        return float(self.value)

    def reset(self):
        self.value = None


class LaneKalmanFilter:
    """1D Kalman filter tracking CTE — state = [cte, d_cte/dt].

    Combines model prediction with noisy vision measurements.
    When measurement jumps (e.g. BEV picks up wrong line), the filter
    trusts its prediction more, effectively rejecting the outlier.
    """

    def __init__(self, q: float = 0.005, r_both: float = 0.05, r_single: float = 0.20):
        self.x = np.array([0.0, 0.0])       # state: [cte, cte_rate]
        self.P = np.eye(2) * 0.5             # covariance
        self.Q = np.diag([q, q * 5])         # process noise
        self.R_both = r_both                  # measurement noise (2 lines)
        self.R_single = r_single              # measurement noise (1 line)
        self.F = np.array([[1.0, 1.0],
                           [0.0, 1.0]])       # state transition
        self.H = np.array([[1.0, 0.0]])       # observation

    def predict(self) -> float:
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return float(self.x[0])

    def update(self, z_cte: float, single_line: bool = False) -> float:
        R = self.R_single if single_line else self.R_both
        innovation = z_cte - float(self.H @ self.x)
        S = float(self.H @ self.P @ self.H.T) + R
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * innovation
        self.P = (np.eye(2) - K @ self.H) @ self.P
        return float(self.x[0])

    @property
    def cte(self) -> float:
        return float(self.x[0])

    @property
    def cte_rate(self) -> float:
        return float(self.x[1])

    @property
    def innovation_gate(self) -> float:
        """Sqrt of S = HPH'+R — scale for Mahalanobis gating."""
        return float(np.sqrt(max(float(self.H @ self.P @ self.H.T) + self.R_both, 0.001)))

    def reset(self, cte: float = 0.0):
        self.x = np.array([cte, 0.0])
        self.P = np.eye(2) * 0.5


# Lane state constants
LANE_BOTH = 2
LANE_LEFT_ONLY = 1
LANE_RIGHT_ONLY = -1
LANE_NONE = 0
STATE_LABELS = {LANE_BOTH: '2L', LANE_LEFT_ONLY: 'L', LANE_RIGHT_ONLY: 'R', LANE_NONE: '--'}


class AdaptiveTrackerV2:
    """
    Lane-following tracker dựa trên Cross-Track Error (CTE) + PID controller.

    Pipeline:
      1. Perception: Camera → binary mask (vạch trắng)
      2. Ego-centric: Xe làm gốc tọa độ, frame pixel = hệ tọa độ
      3. Target fix cứng: Vạch trái & phải phải nằm tại target_left_x, target_right_x
      4. CTE = (actual_center - target_center) / half_width
      5. PID controller: u(t) = Kp*e + Ki*∫e + Kd*de/dt
      6. Pure Pursuit: Khi vào cua, dùng lookahead point thay vì target fix cứng
      7. Bird's Eye View: BEV overlay ở góc trên trái camera stream

    Tất cả tham số đọc từ env (canny.env).
    """

    def __init__(self, width: int = None, height: int = None):
        # ── Frame dimensions ──
        self.w = width or _env_int('CANNY_FRAME_WIDTH', 640)
        self.h = height or _env_int('CANNY_FRAME_HEIGHT', 480)

        # ── Camera offset ──
        self.camera_x_offset = _env_int('CANNY_CAMERA_X_OFFSET', 0)

        # ── ROI ──
        self.y_bottom = int(self.h * _env_float('CANNY_ROI_BOTTOM_FRAC', 0.85))
        self.y_top    = int(self.h * _env_float('CANNY_ROI_TOP_FRAC', 0.60))
        self.num_scans = _env_int('CANNY_NUM_SCANS', 12)
        self.standard_lane_width = int(self.w * _env_float('CANNY_STANDARD_LANE_WIDTH_FRAC', 0.6))

        # ROI khởi tạo
        self.roi_x = np.array([
            self.w * _env_float('CANNY_ROI_INIT_LEFT_BOT', 0.15),
            self.w * _env_float('CANNY_ROI_INIT_RIGHT_BOT', 0.85),
            self.w * _env_float('CANNY_ROI_INIT_LEFT_TOP', 0.30),
            self.w * _env_float('CANNY_ROI_INIT_RIGHT_TOP', 0.70),
        ], dtype=np.float32)
        self.roi_ema_alpha = _env_float('CANNY_ROI_EMA_ALPHA', 0.2)

        # ── Target (chỉ cần target_right + lane_width → suy ra left, center) ──
        self.target_right_x = _env_int('CANNY_TARGET_RIGHT_X', 500)
        self.lane_width = _env_int('CANNY_LANE_WIDTH', 360)
        self.target_left_x = self.target_right_x - self.lane_width
        self.target_center_x = self.target_right_x - self.lane_width // 2
        self._lane_width_ema = float(self.lane_width)  # adaptive EMA
        # Dòng quét target X: đẩy trọng tâm lên cao hơn baseline
        # để xe bị lệch vẫn thấy được vạch 2 bên (% chiều cao frame)
        self.target_x_scan_y = int(self.h * _env_float('CANNY_TARGET_X_SCAN_Y_FRAC', 0.70))

        # ── PID Controller (System A: Lane Position) ──
        self.Kp = _env_float('CANNY_PID_KP', 0.85)
        self.Ki = _env_float('CANNY_PID_KI', 0.05)
        self.Kd = _env_float('CANNY_PID_KD', 0.60)
        self._integral = 0.0
        self._integral_max = _env_float('CANNY_PID_INTEGRAL_MAX', 2.0)
        self._prev_cte = 0.0
        self.steer_ema = EMAFilter(alpha=_env_float('CANNY_PID_STEER_EMA_ALPHA', 0.3))

        # ── Line Loss Recovery ──
        self._last_valid_cte = 0.0
        self._frames_line_lost = 0
        self._last_left_detected = True
        self._last_right_detected = True
        self._recovery_steer_base = _env_float('CANNY_RECOVERY_STEER_BASE', 0.25)
        self._recovery_max_frames = _env_int('CANNY_RECOVERY_MAX_FRAMES', 12)

        # ── Heading Controller (System B: ROI Heading — independent) ──
        self._heading_prev = 0.0
        self.heading_Kp = _env_float('CANNY_HEADING_KP', 0.40)
        self.heading_Kd = _env_float('CANNY_HEADING_KD', 0.30)

        # ── Dual-system fusion weights ──
        self.position_weight = _env_float('CANNY_POSITION_WEIGHT', 0.65)
        self.heading_weight = _env_float('CANNY_HEADING_WEIGHT', 0.35)

        # ── Target X centering PID (separate from process_frame PID) ──
        self._tx_integral = 0.0
        self._tx_prev_cte = 0.0
        self._tx_steer_ema = EMAFilter(alpha=_env_float('CANNY_PID_STEER_EMA_ALPHA', 0.3))

        # ── Kalman Filter ──
        self.kalman = LaneKalmanFilter(
            q=_env_float('CANNY_KALMAN_Q', 0.005),
            r_both=_env_float('CANNY_KALMAN_R_BOTH', 0.05),
            r_single=_env_float('CANNY_KALMAN_R_SINGLE', 0.20),
        )
        self._kalman_outlier_gate = _env_float('CANNY_KALMAN_OUTLIER_GATE', 3.5)

        # ── Lane State Machine ──
        self._lane_state = LANE_NONE
        self._state_confirm_count = 0
        self._state_confirm_threshold = _env_int('CANNY_STATE_CONFIRM_FRAMES', 2)

        # ── BEV Histogram ──
        self._hist_peak_threshold_frac = _env_float('CANNY_HIST_PEAK_THRESHOLD', 0.10)

        # ── Pure Pursuit ──
        self.pursuit_lookahead_min = _env_int('CANNY_PURSUIT_LOOKAHEAD_MIN', 60)
        self.pursuit_lookahead_max = _env_int('CANNY_PURSUIT_LOOKAHEAD_MAX', 180)
        self.pursuit_gain = _env_float('CANNY_PURSUIT_GAIN', 1.2)

        # ── Bird's Eye View ──
        self.bev_w = _env_int('CANNY_BEV_WIDTH', 200)
        self.bev_h = _env_int('CANNY_BEV_HEIGHT', 200)
        self._bev_matrix = self._compute_bev_matrix()

        # ── Baseline ──
        self.baseline_y = self.y_bottom
        self.baseline_balance = 0.0



        # ── Binary mask params ──
        self._binary_threshold = _env_int('CANNY_BINARY_THRESHOLD', 180)
        self._blur_kernel = _env_int('CANNY_BLUR_KERNEL', 5)
        self._morph_kernel_size = _env_int('CANNY_MORPH_KERNEL', 3)

    def _compute_bev_matrix(self) -> np.ndarray:
        """Tính ma trận perspective transform cho Bird's Eye View."""
        fw, fh = float(self.w), float(self.h)
        bw, bh = float(self.bev_w), float(self.bev_h)

        src = np.float32([
            [fw * _env_float('CANNY_BEV_SRC_TL_X', 0.30), fh * _env_float('CANNY_BEV_SRC_TL_Y', 0.50)],
            [fw * _env_float('CANNY_BEV_SRC_TR_X', 0.70), fh * _env_float('CANNY_BEV_SRC_TR_Y', 0.50)],
            [fw * _env_float('CANNY_BEV_SRC_BR_X', 1.00), fh * _env_float('CANNY_BEV_SRC_BR_Y', 0.95)],
            [fw * _env_float('CANNY_BEV_SRC_BL_X', 0.00), fh * _env_float('CANNY_BEV_SRC_BL_Y', 0.95)],
        ])
        dst = np.float32([
            [bw * _env_float('CANNY_BEV_DST_TL_X', 0.10), bh * _env_float('CANNY_BEV_DST_TL_Y', 0.0)],
            [bw * _env_float('CANNY_BEV_DST_TR_X', 0.90), bh * _env_float('CANNY_BEV_DST_TR_Y', 0.0)],
            [bw * _env_float('CANNY_BEV_DST_BR_X', 0.90), bh * _env_float('CANNY_BEV_DST_BR_Y', 1.0)],
            [bw * _env_float('CANNY_BEV_DST_BL_X', 0.10), bh * _env_float('CANNY_BEV_DST_BL_Y', 1.0)],
        ])
        return cv2.getPerspectiveTransform(src, dst)

    # ═══════════════════════════════════════════════════════════
    # Image Processing
    # ═══════════════════════════════════════════════════════════

    def _get_clean_mask(self, bgr_img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        k = self._blur_kernel
        blur = cv2.GaussianBlur(gray, (k, k), 0)
        _, mask = cv2.threshold(blur, self._binary_threshold, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT,
                                           (self._morph_kernel_size, self._morph_kernel_size))
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    def _get_bird_eye_view(self, bgr_img: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Tạo Bird's Eye View đen trắng từ binary mask (góc nhìn từ trên xuống)."""
        bev_mask = cv2.warpPerspective(mask, self._bev_matrix,
                                        (self.bev_w, self.bev_h))
        # Ảnh đen trắng: vạch trắng trên nền đen
        bev_bw = np.zeros((self.bev_h, self.bev_w, 3), dtype=np.uint8)
        bev_bw[bev_mask > 0] = (255, 255, 255)

        # Vẽ target lines trên BEV (chỉ TR + TC, không vẽ TL)
        target_pts_src = np.float32([
            [[self.target_right_x, self.y_bottom]],
            [[self.target_center_x, self.y_bottom]],
        ])
        target_pts_bev = cv2.perspectiveTransform(target_pts_src, self._bev_matrix)

        labels = ["TR", "TC"]
        colors = [(255, 0, 0), (0, 255, 255)]
        for i in range(2):
            pt = target_pts_bev[i][0]
            x, y = int(pt[0]), int(pt[1])
            if 0 <= x < self.bev_w and 0 <= y < self.bev_h:
                cv2.circle(bev_bw, (x, y), 4, colors[i], -1)
                cv2.putText(bev_bw, labels[i], (x + 5, y - 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.3, colors[i], 1)

        cv2.rectangle(bev_bw, (0, 0), (self.bev_w - 1, self.bev_h - 1), (128, 128, 128), 1)
        cv2.putText(bev_bw, "BEV", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        return bev_bw

    def _bev_histogram_peaks(self, mask: np.ndarray):
        """BEV histogram peak detection.

        Warp binary mask to bird's eye view, project white pixels
        onto X axis.  Left/right peaks above threshold → line exists.

        Returns: (left_detected, right_detected,
                  left_peak_x_bev, right_peak_x_bev, histogram)
        """
        bev_mask = cv2.warpPerspective(mask, self._bev_matrix,
                                        (self.bev_w, self.bev_h))
        # Bottom 65% of BEV (closer to car = more reliable)
        roi_top = int(self.bev_h * 0.35)
        roi = bev_mask[roi_top:, :]
        histogram = np.sum(roi > 0, axis=0).astype(float)

        midpoint = self.bev_w // 2
        threshold = roi.shape[0] * self._hist_peak_threshold_frac

        left_hist = histogram[:midpoint]
        right_hist = histogram[midpoint:]

        left_max = float(np.max(left_hist)) if len(left_hist) > 0 else 0.0
        right_max = float(np.max(right_hist)) if len(right_hist) > 0 else 0.0

        left_detected = left_max > threshold
        right_detected = right_max > threshold

        left_peak_x = int(np.argmax(left_hist)) if left_detected else None
        right_peak_x = midpoint + int(np.argmax(right_hist)) if right_detected else None

        return left_detected, right_detected, left_peak_x, right_peak_x, histogram

    def _get_line_edges(self, mask_1d: np.ndarray, expected_x: int,
                        window: int = 80, x_min: int = None,
                        x_max: int = None) -> Optional[Tuple[int, int]]:
        """Tìm rìa trong và rìa ngoài của 1 vạch kẻ trắng.
        x_min/x_max: hard boundary — chỉ tìm vạch trong vùng này.
        Ngăn detect nhầm vạch trắng bên ngoài lane khi có 3+ vạch."""
        start = max(0, expected_x - window)
        end = min(self.w, expected_x + window)
        # Apply hard boundary clamp if provided
        if x_min is not None:
            start = max(start, x_min)
        if x_max is not None:
            end = min(end, x_max)
        if start >= end:
            return None
        local_slice = mask_1d[start:end]
        white_indices = np.where(local_slice > 0)[0]

        if len(white_indices) < 3:
            return None

        # When multiple white segments exist, pick the one nearest to expected_x
        # This prevents grabbing the outer line in 3-line scenarios
        padded = np.concatenate(([0], (local_slice > 0).astype(np.uint8), [0]))
        diff = np.diff(padded)
        seg_starts = np.where(diff == 1)[0]
        seg_ends = np.where(diff == -1)[0]

        best_seg = None
        best_dist = float('inf')
        exp_local = expected_x - start  # expected_x in local coordinates
        for ss, se in zip(seg_starts, seg_ends):
            seg_w = se - ss
            if seg_w < 3 or seg_w > (end - start) * 0.6:
                continue
            seg_mid = (ss + se) / 2.0
            dist = abs(seg_mid - exp_local)
            if dist < best_dist:
                best_dist = dist
                best_seg = (ss, se)

        if best_seg is None:
            return None

        l_edge_local, r_edge_local = best_seg
        return start + l_edge_local, start + r_edge_local - 1



    def reset_target_x(self):
        """Reset Target X PID + Kalman state — gọi khi bắt đầu segment mới."""
        self._tx_integral = 0.0
        self._tx_prev_cte = 0.0
        self._tx_steer_ema.reset()
        self.kalman.reset()
        self._lane_state = LANE_NONE
        self._state_confirm_count = 0
        self._frames_line_lost = 0

    # ═══════════════════════════════════════════════════════════
    # Dual-Target Lane Centering (PRIMARY steering source)
    # ═══════════════════════════════════════════════════════════

    def get_target_x_steering(self, frame: np.ndarray):
        """
        State-machine + Kalman lane centering.

        Pipeline:
          1. Edge detection → find left/right white lines
          2. BEV histogram → validate line existence (peak > threshold)
          3. State machine → BOTH / LEFT_ONLY / RIGHT_ONLY / NONE
          4. Lane center = midpoint (2 lines) or line ± W/2 (1 line)
          5. Raw CTE = (lane_center − target_center) / half_w
          6. Kalman filter → smooth CTE, reject outlier jumps
          7. PID → steering command (with anti integral windup)

        Quy ước dấu:
          CTE > 0 → lane center dịch phải so với mốc → xe lệch TRÁI → lái PHẢI
          CTE < 0 → lane center dịch trái so với mốc → xe lệch PHẢI → lái TRÁI

        Returns: (steering, confidence, left_inner, right_inner,
                  raw_cte, filtered_cte, lane_state)
        """
        mask = self._get_clean_mask(frame)
        half_w = self.w / 2.0

        # ── 1. Edge detection ──────────────────────────────
        scan_y = self.target_x_scan_y
        band = 4
        scan_rows = [scan_y, (scan_y + self.y_bottom) // 2, self.y_bottom]
        row = np.zeros(self.w, dtype=np.uint8)
        for sy in scan_rows:
            sy = max(band, min(self.h - band, sy))
            row_slice = np.max(mask[sy - band:sy + band, :], axis=0)
            row = np.maximum(row, row_slice)

        # Find all white segments
        padded = np.concatenate(([0], (row > 0).astype(np.uint8), [0]))
        diff = np.diff(padded)
        starts = np.where(diff == 1)[0]
        ends = np.where(diff == -1)[0]

        left_inner = None
        right_inner = None
        min_line_w = 3
        max_line_w = int(self.w * 0.15)
        max_search_dist = self.lane_width

        # Find left line: right edge (inner) closest to target_left_x
        best_left_dist = float('inf')
        left_seg = None
        for s, e in zip(starts, ends):
            w = e - s
            if w < min_line_w or w > max_line_w:
                continue
            inner_edge = e
            dist = abs(inner_edge - self.target_left_x)
            if dist > max_search_dist or inner_edge > self.target_right_x:
                continue
            if dist < best_left_dist:
                best_left_dist = dist
                left_inner = inner_edge
                left_seg = (s, e)

        # Find right line: left edge (inner) closest to target_right_x
        best_right_dist = float('inf')
        for s, e in zip(starts, ends):
            w = e - s
            if w < min_line_w or w > max_line_w:
                continue
            if left_seg is not None and s == left_seg[0] and e == left_seg[1]:
                continue
            inner_edge = s
            dist = abs(inner_edge - self.target_right_x)
            if dist > max_search_dist or inner_edge < self.target_left_x:
                continue
            if dist < best_right_dist:
                best_right_dist = dist
                right_inner = inner_edge

        # ── 2. BEV histogram validation ───────────────────
        hist_left, hist_right, _, _, _ = self._bev_histogram_peaks(mask)

        # ── 3. State machine ──────────────────────────────
        has_left = left_inner is not None
        has_right = right_inner is not None

        if has_left and has_right:
            raw_state = LANE_BOTH
        elif has_left:
            raw_state = LANE_LEFT_ONLY
        elif has_right:
            raw_state = LANE_RIGHT_ONLY
        else:
            raw_state = LANE_NONE

        # Hysteresis: require N consecutive frames to confirm transition
        if raw_state != self._lane_state:
            self._state_confirm_count += 1
            if self._state_confirm_count >= self._state_confirm_threshold:
                old_state = self._lane_state
                self._lane_state = raw_state
                self._state_confirm_count = 0
                # Anti integral windup on state transition
                if old_state == LANE_BOTH and raw_state in (LANE_LEFT_ONLY, LANE_RIGHT_ONLY):
                    self._tx_integral *= 0.5
                elif raw_state == LANE_NONE:
                    self._tx_integral *= 0.3
        else:
            self._state_confirm_count = 0

        # ── 4. Lane center estimation (single-line tracking) ──
        lane_center = None
        single_line = False
        confidence = 0.0

        if has_left and has_right:
            lane_center = (left_inner + right_inner) / 2.0
            confidence = 1.0
            # Adaptive lane width (EMA)
            measured_w = right_inner - left_inner
            if 0.5 * self.lane_width < measured_w < 1.5 * self.lane_width:
                self._lane_width_ema = 0.95 * self._lane_width_ema + 0.05 * measured_w
        elif has_left:
            lane_center = left_inner + self._lane_width_ema / 2.0
            single_line = True
            confidence = 0.6 if hist_left else 0.4
        elif has_right:
            lane_center = right_inner - self._lane_width_ema / 2.0
            single_line = True
            confidence = 0.6 if hist_right else 0.4

        # ── 5. Kalman predict + update ────────────────────
        predicted_cte = self.kalman.predict()

        if lane_center is not None:
            raw_cte = (lane_center - self.target_center_x) / half_w

            # Outlier gating (Mahalanobis-like)
            innovation = abs(raw_cte - predicted_cte)
            gate = self._kalman_outlier_gate * self.kalman.innovation_gate

            if innovation < gate:
                filtered_cte = self.kalman.update(raw_cte, single_line=single_line)
            else:
                # Outlier — trust prediction
                filtered_cte = predicted_cte
                confidence *= 0.2

            self._frames_line_lost = 0
        else:
            # No measurement — pure prediction
            raw_cte = predicted_cte
            filtered_cte = predicted_cte
            self._frames_line_lost += 1

            if self._frames_line_lost > self._recovery_max_frames:
                self.kalman.reset()
                filtered_cte = 0.0
                self._tx_integral = 0.0

        # ── 6. PID on filtered CTE (anti-windup) ─────────
        if self._lane_state == LANE_NONE:
            self._tx_integral *= 0.8   # decay when no lines
        else:
            self._tx_integral += filtered_cte
            self._tx_integral = np.clip(self._tx_integral,
                                        -self._integral_max, self._integral_max)

        d_cte = filtered_cte - self._tx_prev_cte
        self._tx_prev_cte = filtered_cte

        steering = self.Kp * filtered_cte + self.Ki * self._tx_integral + self.Kd * d_cte
        steering = float(np.clip(steering, -1.0, 1.0))
        steering = self._tx_steer_ema.update(steering)

        return steering, confidence, left_inner, right_inner, raw_cte, filtered_cte, self._lane_state

    # ═══════════════════════════════════════════════════════════
    # Pure Pursuit — Lookahead point trên center path
    # ═══════════════════════════════════════════════════════════

    def _pure_pursuit_steer(self, center_path_pts: list,
                             car_x: int, car_y: int) -> Optional[float]:
        """
        Pure Pursuit: tìm lookahead point trên center_path và tính góc lái.

        center_path_pts: list of (x, y) sorted từ bottom → top.
        car_x, car_y: vị trí xe (tâm frame, đáy ROI).

        Returns: steer in [-1, +1] or None.
        """
        if len(center_path_pts) < 3:
            return None

        la_min = self.pursuit_lookahead_min
        la_max = self.pursuit_lookahead_max

        # Ước lượng độ cong
        dx_path = abs(center_path_pts[-1][0] - center_path_pts[0][0])
        curvature = min(dx_path / (self.w * 0.5 + 1e-5), 1.0)
        lookahead_dist = la_max - curvature * (la_max - la_min)

        # Tìm điểm gần nhất với lookahead distance
        best_pt = None
        best_dist_diff = float('inf')
        for (px, py) in center_path_pts:
            d = np.sqrt((px - car_x) ** 2 + (py - car_y) ** 2)
            diff = abs(d - lookahead_dist)
            if diff < best_dist_diff:
                best_dist_diff = diff
                best_pt = (px, py)

        if best_pt is None:
            return None

        lateral_offset = best_pt[0] - car_x
        ld = np.sqrt((best_pt[0] - car_x) ** 2 + (best_pt[1] - car_y) ** 2)
        if ld < 1.0:
            return 0.0

        steer = self.pursuit_gain * (2.0 * lateral_offset) / (ld * ld) * ld
        return float(np.clip(steer, -1.0, 1.0))

    # ═══════════════════════════════════════════════════════════
    # Main Processing Pipeline
    # ═══════════════════════════════════════════════════════════

    def process_frame(self, frame: np.ndarray):
        mask = self._get_clean_mask(frame)
        viz_frame = frame.copy()
        car_center_x = (self.w // 2) + self.camera_x_offset
        half_w = self.w / 2.0

        # ── Bird's Eye View ──
        bev_img = self._get_bird_eye_view(frame, mask)

        active_y_top = self.y_top
        active_roi_x = self.roi_x.copy()

        l_bot_exp, r_bot_exp, l_top_exp, r_top_exp = active_roi_x
        y_steps = np.linspace(self.y_bottom, active_y_top, self.num_scans, dtype=int)

        valid_l_pts, valid_r_pts = [], []
        center_path_pts = []
        row_cte_errors = []   # (y, cte, n_lines) lane-center CTE per scan row

        COLOR_OUTER = (150, 0, 0)
        COLOR_INNER = (255, 255, 0)
        COLOR_MID = (0, 255, 255)
        COLOR_CENTER = (0, 0, 255)
        COLOR_TARGET = (0, 255, 0)

        # ══════════════════════════════════════════════════
        # BƯỚC 1: Quét tìm cạnh lane, tính center path
        # ══════════════════════════════════════════════════
        baseline_l_mid = None
        baseline_r_mid = None

        for y in y_steps:
            ratio = (self.y_bottom - y) / (self.y_bottom - active_y_top + 1e-5)
            exp_l = int(l_bot_exp + ratio * (l_top_exp - l_bot_exp))
            exp_r = int(r_bot_exp + ratio * (r_top_exp - r_bot_exp))

            row_slice = np.max(mask[max(0, y - 2):min(self.h, y + 2), :], axis=0)

            # Clamp search: left line within [0, target_right],
            #               right line within [target_left, w]
            # Dùng target_left/right thay vì center — tránh mất line khi drift
            l_edges = self._get_line_edges(row_slice, exp_l, window=60,
                                           x_min=0, x_max=self.target_right_x)
            r_edges = self._get_line_edges(row_slice, exp_r, window=60,
                                           x_min=self.target_left_x, x_max=self.w)

            l_mid_val, r_mid_val = None, None

            if l_edges:
                l_out, l_in = l_edges
                l_mid_val = (l_out + l_in) // 2
                valid_l_pts.append((y, l_mid_val))
                cv2.line(viz_frame, (l_out, y), (l_in, y), (0, 255, 0), 2)
                cv2.circle(viz_frame, (l_out, y), 3, COLOR_OUTER, -1)
                cv2.circle(viz_frame, (l_in, y), 3, COLOR_INNER, -1)
                cv2.circle(viz_frame, (l_mid_val, y), 3, COLOR_MID, -1)

            if r_edges:
                r_in, r_out = r_edges
                r_mid_val = (r_in + r_out) // 2
                valid_r_pts.append((y, r_mid_val))
                cv2.line(viz_frame, (r_in, y), (r_out, y), (0, 255, 0), 2)
                cv2.circle(viz_frame, (r_in, y), 3, COLOR_INNER, -1)
                cv2.circle(viz_frame, (r_out, y), 3, COLOR_OUTER, -1)
                cv2.circle(viz_frame, (r_mid_val, y), 3, COLOR_MID, -1)

            if y == y_steps[0]:
                baseline_l_mid = l_mid_val
                baseline_r_mid = r_mid_val

            # ── Lane-center CTE per row (state machine approach) ──
            l_in_val = l_edges[1] if l_edges else None
            r_in_val = r_edges[0] if r_edges else None

            if l_in_val is not None and r_in_val is not None:
                lane_ctr = (l_in_val + r_in_val) / 2.0
                row_cte_errors.append((y, (lane_ctr - self.target_center_x) / half_w, 2))
            elif l_in_val is not None:
                lane_ctr = l_in_val + self._lane_width_ema / 2.0
                row_cte_errors.append((y, (lane_ctr - self.target_center_x) / half_w, 1))
            elif r_in_val is not None:
                lane_ctr = r_in_val - self._lane_width_ema / 2.0
                row_cte_errors.append((y, (lane_ctr - self.target_center_x) / half_w, 1))

            # Center point for Pure Pursuit (2 lines or estimated from 1)
            if l_mid_val is not None and r_mid_val is not None:
                cv2.line(viz_frame, (l_mid_val, y), (r_mid_val, y), (200, 100, 200), 1)
                center_x = (l_mid_val + r_mid_val) // 2
                center_path_pts.append((center_x, y))
                cv2.circle(viz_frame, (center_x, y), 4, COLOR_CENTER, -1)

        # ══════════════════════════════════════════════════
        # VẼ HỆ TRỤC TỌA ĐỘ (Ego-centric Coordinate System)
        # ══════════════════════════════════════════════════
        cv2.arrowedLine(viz_frame, (car_center_x, self.h - 10),
                        (car_center_x, active_y_top - 20),
                        (200, 200, 200), 2, tipLength=0.03)
        cv2.putText(viz_frame, "Y", (car_center_x + 5, active_y_top - 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        cv2.arrowedLine(viz_frame, (20, self.y_bottom),
                        (self.w - 20, self.y_bottom),
                        (200, 200, 200), 2, tipLength=0.02)
        cv2.putText(viz_frame, "X", (self.w - 15, self.y_bottom - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        cv2.circle(viz_frame, (car_center_x, self.y_bottom), 6, (255, 255, 255), 2)
        cv2.putText(viz_frame, "O", (car_center_x - 15, self.y_bottom + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        # ══════════════════════════════════════════════════
        # VẼ TARGET FIX CỨNG — cố định theo pixel khung hình (full height)
        # ══════════════════════════════════════════════════
        cv2.line(viz_frame, (self.target_right_x, self.h - 1),
                 (self.target_right_x, 0), (255, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(viz_frame, f"TR:{self.target_right_x}",
                    (self.target_right_x - 30, self.h - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 0, 0), 1)
        # Đường tâm nét đứt — cố định theo frame
        for yy in range(0, self.h, 8):
            cv2.line(viz_frame, (self.target_center_x, yy),
                     (self.target_center_x, min(yy + 4, self.h)),
                     COLOR_TARGET, 1)
        # Dòng quét target X (ngang) — từ center đến target right
        cv2.line(viz_frame, (self.target_center_x, self.target_x_scan_y),
                 (self.target_right_x, self.target_x_scan_y),
                 (0, 200, 200), 1, cv2.LINE_AA)

        # ══════════════════════════════════════════════════
        # BASELINE
        # ══════════════════════════════════════════════════
        cv2.line(viz_frame, (0, self.y_bottom), (self.w, self.y_bottom), (255, 100, 255), 2)

        if baseline_l_mid is not None and baseline_r_mid is not None:
            dist_l = car_center_x - baseline_l_mid
            dist_r = baseline_r_mid - car_center_x
            total = dist_l + dist_r if (dist_l + dist_r) > 0 else 1
            self.baseline_balance = (dist_r - dist_l) / total
            bal_color = (0, 255, 0) if abs(self.baseline_balance) < 0.1 else (0, 165, 255)
            cv2.putText(viz_frame, f"BAL: {self.baseline_balance:+.2f}",
                        (10, self.y_bottom - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, bal_color, 2)
            cv2.circle(viz_frame, (car_center_x, self.y_bottom), 5, (255, 100, 255), -1)
            if baseline_l_mid:
                cv2.circle(viz_frame, (baseline_l_mid, self.y_bottom), 5, (0, 0, 255), -1)
            if baseline_r_mid:
                cv2.circle(viz_frame, (baseline_r_mid, self.y_bottom), 5, (255, 0, 0), -1)
        else:
            self.baseline_balance = 0.0
            cv2.putText(viz_frame, "BAL: N/A",
                        (10, self.y_bottom - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 2)

        # ══════════════════════════════════════════════════
        # BƯỚC 2: Hồi quy xác định ROI hình thang
        # ══════════════════════════════════════════════════
        poly_l, poly_r = None, None

        if len(valid_l_pts) >= 3:
            y_coords, x_coords = zip(*valid_l_pts)
            poly_l = np.poly1d(np.polyfit(y_coords, x_coords, 1))

        if len(valid_r_pts) >= 3:
            y_coords, x_coords = zip(*valid_r_pts)
            poly_r = np.poly1d(np.polyfit(y_coords, x_coords, 1))

        is_tracking = False
        if poly_l is not None and poly_r is not None:
            is_tracking = True
            target_l_bot = poly_l(self.y_bottom)
            target_l_top = poly_l(active_y_top)
            target_r_bot = poly_r(self.y_bottom)
            target_r_top = poly_r(active_y_top)

            new_target = np.array([target_l_bot, target_r_bot, target_l_top, target_r_top],
                                  dtype=np.float32)
            self.roi_x = (self.roi_ema_alpha * new_target) + \
                         ((1 - self.roi_ema_alpha) * self.roi_x)
            active_roi_x = self.roi_x.copy()

        # ── Clamp ROI top edges: never go outside target lines ──
        # ROI top must stay ON or INSIDE the target left/right boundaries
        active_roi_x[2] = max(active_roi_x[2], self.target_left_x)   # left_top >= target_left
        active_roi_x[3] = min(active_roi_x[3], self.target_right_x)  # right_top <= target_right
        # Also clamp bottom edges
        active_roi_x[0] = max(active_roi_x[0], self.target_left_x - 40)   # small margin at bottom
        active_roi_x[1] = min(active_roi_x[1], self.target_right_x + 40)

        curr_l_bot, curr_r_bot, curr_l_top, curr_r_top = map(int, active_roi_x)

        # ══════════════════════════════════════════════════
        # BƯỚC 3: Vẽ khung ROI + đường định hướng
        # ══════════════════════════════════════════════════
        roi_color = (0, 0, 255)
        trap_pts = np.array([
            [curr_l_bot, self.y_bottom], [curr_l_top, active_y_top],
            [curr_r_top, active_y_top], [curr_r_bot, self.y_bottom]
        ], np.int32)
        cv2.polylines(viz_frame, [trap_pts], True, roi_color, 3)

        if len(center_path_pts) > 1:
            for i in range(len(center_path_pts) - 1):
                cv2.line(viz_frame, center_path_pts[i], center_path_pts[i + 1], COLOR_MID, 2)

        mid_bot_x = (curr_l_bot + curr_r_bot) // 2
        mid_top_x = (curr_l_top + curr_r_top) // 2

        cv2.line(viz_frame, (mid_top_x, active_y_top), (car_center_x, self.h), (0, 255, 255), 2)
        cv2.circle(viz_frame, (mid_top_x, active_y_top), 6, COLOR_CENTER, -1)

        # ══════════════════════════════════════════════════
        # BƯỚC 4: STATE-MACHINE STEERING
        #
        #   System A — LANE POSITION: lane-center CTE (state machine)
        #     Uses known lane width for single-line tracking
        #     CTE = (lane_center − target_center) / half_w
        #
        #   System B — ROI HEADING: hướng lane từ hình học ROI
        #     Luôn có (ROI ổn định nhờ EMA) → hướng xe đang đi
        # ══════════════════════════════════════════════════

        # ── System A: Lane Position (lane-center CTE) ──
        lane_cte = None
        n_lines_max = 0
        if len(row_cte_errors) >= 2:
            rc_ys = np.array([e[0] for e in row_cte_errors])
            rc_errs = np.array([e[1] for e in row_cte_errors])
            rc_n = np.array([e[2] for e in row_cte_errors])
            n_lines_max = int(rc_n.max())
            rc_w = (rc_ys - rc_ys.min() + 1.0) * rc_n
            lane_cte = float(np.average(rc_errs, weights=rc_w))

        # ── System B: ROI Heading (luôn có, EMA-stabilised) ──
        heading_err = (mid_top_x - mid_bot_x) / half_w
        d_heading = heading_err - self._heading_prev
        self._heading_prev = heading_err
        head_steer = self.heading_Kp * heading_err + self.heading_Kd * d_heading

        # ── Compute steering ──
        if lane_cte is not None:
            # --- Cả 2 hệ thống → kết hợp tương quan ---
            self._integral += lane_cte
            self._integral = np.clip(self._integral, -self._integral_max, self._integral_max)
            d_lane = lane_cte - self._prev_cte
            self._prev_cte = lane_cte
            pos_steer = self.Kp * lane_cte + self.Ki * self._integral + self.Kd * d_lane

            pid_steer = self.position_weight * pos_steer + self.heading_weight * head_steer
            cte = lane_cte

            # ── Lưu trạng thái để recovery ──
            self._last_valid_cte = lane_cte
            self._frames_line_lost = 0
            self._last_left_detected = len(valid_l_pts) > 0
            self._last_right_detected = len(valid_r_pts) > 0
        else:
            # ── RECOVERY: Dùng last_valid_cte — dấu đã đúng hướng ──
            # Không hardcode hướng! Dấu của last_valid_cte cho biết xe đang
            # lệch hướng nào, cứ tiếp tục steer theo hướng đó.
            self._frames_line_lost += 1
            ramp = min(self._frames_line_lost / max(self._recovery_max_frames, 1), 1.0)

            if abs(self._last_valid_cte) > 0.01:
                # Tiếp tục steer theo hướng CTE cuối, amplify nhẹ theo thời gian mất
                recovery_cte = self._last_valid_cte * (1.0 + 0.3 * ramp)
            else:
                # CTE cuối cùng gần 0 → dùng ROI offset nhẹ để suy luận
                roi_offset = (mid_bot_x - self.target_center_x) / half_w
                recovery_cte = 0.3 * roi_offset

            # Giảm heading weight khi recovery (heading có thể sai khi mất vạch)
            pid_steer = self.Kp * recovery_cte + 0.2 * head_steer
            cte = recovery_cte
            # Decay integral
            self._integral *= 0.85

        e_heading = heading_err

        # ══════════════════════════════════════════════════
        # BƯỚC 5: PURE PURSUIT (đường cong — chỉ khi có center path đủ)
        # ══════════════════════════════════════════════════
        pursuit_steer = self._pure_pursuit_steer(center_path_pts,
                                                  car_center_x, self.y_bottom)

        if pursuit_steer is not None:
            curvature_factor = min(abs(cte) * 3.0, 1.0)
            blended = (1.0 - curvature_factor) * pid_steer + curvature_factor * pursuit_steer
        else:
            blended = pid_steer

        raw_steer = np.clip(blended, -1.0, 1.0)
        steer_final = self.steer_ema.update(raw_steer)

        # ══════════════════════════════════════════════════
        # VẼ CTE indicator (dual-system)
        # ══════════════════════════════════════════════════
        cte_color = (0, 255, 0) if abs(cte) < 0.05 else (0, 165, 255) if abs(cte) < 0.15 else (0, 0, 255)
        cte_y = self.y_bottom - 20
        actual_center_x = int(self.target_center_x + cte * half_w)
        cv2.arrowedLine(viz_frame,
                        (self.target_center_x, cte_y),
                        (actual_center_x, cte_y),
                        cte_color, 3, tipLength=0.15)
        # System A label
        if lane_cte is not None:
            cv2.putText(viz_frame, f"POS:{lane_cte:+.3f}",
                        (self.target_center_x - 60, cte_y - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        else:
            if self._frames_line_lost > 0:
                cv2.putText(viz_frame, f"REC:{cte:+.3f}",
                            (self.target_center_x - 60, cte_y - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 100, 255), 1)
            else:
                cv2.putText(viz_frame, "POS: N/A",
                            (self.target_center_x - 60, cte_y - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (128, 128, 128), 1)
        # System B label
        head_color = (0, 255, 0) if abs(heading_err) < 0.05 else (0, 165, 255)
        cv2.putText(viz_frame, f"HEAD:{heading_err:+.3f}",
                    (self.target_center_x - 60, cte_y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, head_color, 1)

        if pursuit_steer is not None and len(center_path_pts) >= 3:
            la_dist = self.pursuit_lookahead_min + \
                      (self.pursuit_lookahead_max - self.pursuit_lookahead_min) * 0.5
            cv2.circle(viz_frame, (car_center_x, self.y_bottom),
                       int(la_dist), (100, 255, 100), 1)

        # ══════════════════════════════════════════════════
        # HUD Text
        # ══════════════════════════════════════════════════
        if is_tracking:
            status = "TRACKING"
        elif lane_cte is None and self._frames_line_lost > 0:
            status = f"RECOVERY ({self._frames_line_lost}f)"
        else:
            status = "PREDICTING"

        cv2.putText(viz_frame, f"STEER: {steer_final:+.3f}", (150, 40),
                    cv2.FONT_HERSHEY_DUPLEX, 1, (255, 255, 255), 2)
        cv2.putText(viz_frame, status, (150, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, roi_color, 2)

        mode_txt = "PID+PP" if pursuit_steer is not None else "PID"
        state_lbl = STATE_LABELS.get(self._lane_state, '??')
        lane_src = f"{n_lines_max}L" if lane_cte is not None else "ROI"
        cv2.putText(viz_frame, f"Mode:{mode_txt} St:{state_lbl} Src:{lane_src} H={e_heading:+.2f} W={self._lane_width_ema:.0f}",
                    (10, self.h - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        cv2.putText(viz_frame, f"POS={cte:+.3f} HEAD={heading_err:+.3f} Kalman={self.kalman.cte:+.3f}",
                    (10, self.h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        # ══════════════════════════════════════════════════
        # BEV overlay — góc trên trái
        # ══════════════════════════════════════════════════
        bh, bw = bev_img.shape[:2]
        viz_frame[5:5 + bh, 5:5 + bw] = bev_img

        return steer_final, viz_frame

def main():
    cap = cv2.VideoCapture(0)
    tracker = AdaptiveTrackerV2()
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.resize(frame, (tracker.w, tracker.h))
        steer, viz = tracker.process_frame(frame)
        cv2.imshow("Adaptive ROI Track", viz)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()