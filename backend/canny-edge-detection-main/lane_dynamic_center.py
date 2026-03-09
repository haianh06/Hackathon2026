"""
Dynamic ROI + Centroid Tracking Lane Detection
Lightweight approach: no perspective transform needed.
Scans 2 horizontal lines, finds white line centers, builds dynamic trapezoid,
and uses PD controller for steering.
"""

import cv2
import numpy as np
from collections import deque
from typing import Tuple, Optional, List, Dict


class DynamicLaneTracker:
    def __init__(self, width: int = 640, height: int = 480):
        self.w = width
        self.h = height

        # Cấu hình "Hình thang gần"
        self.y_bottom = int(self.h * 0.95)  # Đáy dưới (Gần mũi xe nhất)
        self.y_top = int(self.h * 0.65)     # Đáy trên (Nhìn xa vừa đủ)

        # Lịch sử độ rộng đường (để dùng khi bị mất 1 bên vạch)
        self.lane_width_history = deque(maxlen=20)
        self.default_lane_width = int(self.w * 0.5)

        # PD gains
        self.K_p = 0.6   # Kéo xe về giữa làn
        self.K_d = 0.8   # Nắn đuôi xe cho thẳng với làn

        # Tracking state
        self.frames_lost = 0

    def _get_white_mask(self, bgr_img: np.ndarray) -> np.ndarray:
        """Lọc màu lấy vạch trắng (Tối ưu cho thảm xám/xanh)"""
        hls = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HLS)
        lower_white = np.array([0, 140, 0], dtype=np.uint8)
        upper_white = np.array([180, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hls, lower_white, upper_white)

        # Xóa nhiễu
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return mask

    def _find_line_centers(self, row_mask: np.ndarray, mid_x: int) -> Tuple[Optional[int], Optional[int]]:
        """Tìm trọng tâm của vạch trắng bên trái và bên phải trên 1 dòng Y"""
        nonzero = np.nonzero(row_mask)[0]
        if len(nonzero) == 0:
            return None, None

        left_pts = nonzero[nonzero < mid_x]
        right_pts = nonzero[nonzero >= mid_x]

        center_l, center_r = None, None

        # Trọng tâm = (cạnh ngoài + cạnh trong) / 2
        if len(left_pts) > 0:
            center_l = int((left_pts[0] + left_pts[-1]) / 2)
        if len(right_pts) > 0:
            center_r = int((right_pts[0] + right_pts[-1]) / 2)

        return center_l, center_r

    def detect_and_steer(self, frame: np.ndarray) -> Tuple[float, float, Dict, np.ndarray]:
        """
        Returns: (steer, quality, debug_info, viz_frame)
        - steer: [-1, 1] steering value
        - quality: [0, 1] lane detection quality
        - debug_info: dict with detection details
        - viz_frame: annotated frame for display
        """
        mask = self._get_white_mask(frame)
        out_img = frame.copy()
        car_center_x = self.w // 2

        # 1. Quét 2 dòng ngang (Đáy và Đỉnh)
        row_bottom = mask[self.y_bottom, :]
        row_top = mask[self.y_top, :]

        # 2. Tìm trọng tâm vạch ở Đáy và Đỉnh
        l_bot, r_bot = self._find_line_centers(row_bottom, car_center_x)
        l_top, r_top = self._find_line_centers(row_top, car_center_x)

        # Đếm số vạch thật tìm được (tối đa 4)
        real_count = sum(1 for v in [l_bot, r_bot, l_top, r_top] if v is not None)

        # Cập nhật lịch sử độ rộng làn đường
        if l_bot is not None and r_bot is not None:
            self.lane_width_history.append(r_bot - l_bot)

        current_width = int(np.mean(self.lane_width_history)) if self.lane_width_history else self.default_lane_width

        # 3. Nội suy nếu mất 1 bên vạch (Virtual Marker)
        def interpolate_missing(l, r, width):
            if l is None and r is not None:
                l = r - width
            if r is None and l is not None:
                r = l + width
            return l, r

        l_bot, r_bot = interpolate_missing(l_bot, r_bot, current_width)
        l_top, r_top = interpolate_missing(l_top, r_top, current_width)

        steer = 0.0
        quality = real_count / 4.0
        debug_info = {
            'real_count': real_count,
            'lane_quality': quality,
            'frames_lost': self.frames_lost,
        }

        # Nếu có đủ dữ liệu để tạo hình thang
        if l_bot is not None and r_bot is not None and l_top is not None and r_top is not None:
            self.frames_lost = 0

            # 4. Tính trung điểm đáy trên và đáy dưới
            mid_bot_x = int((l_bot + r_bot) / 2)
            mid_top_x = int((l_top + r_top) / 2)

            # Vẽ "Hình thang động" (Dynamic Trapezoid)
            pts = np.array([
                [l_bot, self.y_bottom], [l_top, self.y_top],
                [r_top, self.y_top], [r_bot, self.y_bottom]
            ], dtype=np.int32)
            cv2.polylines(out_img, [pts], isClosed=True, color=(0, 255, 0), thickness=2)

            # Vẽ đường Line Center (Đường dẫn đường)
            cv2.line(out_img, (mid_bot_x, self.y_bottom), (mid_top_x, self.y_top), (0, 255, 255), 3)
            # Vẽ mũi tên hướng xe hiện tại
            cv2.line(out_img, (car_center_x, self.h), (car_center_x, self.y_bottom), (0, 0, 255), 2)

            # Vẽ các điểm trọng tâm vạch
            for x, y in [(l_bot, self.y_bottom), (r_bot, self.y_bottom), (l_top, self.y_top), (r_top, self.y_top)]:
                cv2.circle(out_img, (x, y), 5, (255, 0, 255), -1)

            # 5. Tính toán góc Steer
            e_offset = (mid_bot_x - car_center_x) / (self.w / 2.0)
            e_heading = (mid_top_x - mid_bot_x) / (self.w / 2.0)

            raw_steer = (self.K_p * e_offset) + (self.K_d * e_heading)
            steer = float(np.clip(raw_steer, -1.0, 1.0))

            debug_info['e_offset'] = e_offset
            debug_info['e_heading'] = e_heading
            debug_info['mid_bot'] = mid_bot_x
            debug_info['mid_top'] = mid_top_x

            # Hiển thị text lên ảnh
            cv2.putText(out_img, f"Steer: {steer:+.3f}", (20, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            cv2.putText(out_img, f"Offset: {e_offset:+.2f}", (20, 85),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(out_img, f"Heading: {e_heading:+.2f}", (20, 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        else:
            self.frames_lost += 1
            cv2.putText(out_img, "LANE LOST", (20, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        # Quality bar
        bar_w = int(self.w * 0.3)
        h = self.h
        cv2.rectangle(out_img, (self.w - bar_w - 10, h - 30),
                      (self.w - 10, h - 10), (0, 0, 255), -1)
        cv2.rectangle(out_img, (self.w - bar_w - 10, h - 30),
                      (self.w - bar_w - 10 + int(bar_w * quality), h - 10), (0, 255, 0), -1)

        return steer, quality, debug_info, out_img
