import cv2
import numpy as np
from collections import deque
from typing import Tuple, Optional

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

class UltimateDynamicTracker:
    def __init__(self, width: int = 640, height: int = 480):
        self.w = width
        self.h = height
        
        self.y_bottom = int(self.h * 0.90)  # Đẩy lên một chút để tránh dính mũi xe/bóng râm
        self.y_top = int(self.h * 0.55)
        
        self.default_lane_width = int(self.w * 0.6)
        self.lane_width_history = deque(maxlen=30)
        
        # Hình thang dự phòng chuẩn xác
        self.prev_src_pts = np.float32([
            [int(self.w * 0.1), self.y_bottom], [int(self.w * 0.9), self.y_bottom],
            [int(self.w * 0.3), self.y_top], [int(self.w * 0.7), self.y_top]
        ])

        self.Kp = 0.85
        self.Kd = 0.60
        self.steer_ema = EMAFilter(alpha=0.3)

    # ==========================================
    # CẢI TIẾN 1: TÁCH NỀN SIÊU SẠCH CHO THẢM XÁM / VẠCH TRẮNG
    # ==========================================
    def _get_clean_mask(self, bgr_img: np.ndarray) -> np.ndarray:
        """Chỉ tập trung bắt màu sáng/trắng, loại bỏ nhiễu Canny rối rắm"""
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Lọc nhị phân: Chỉ giữ lại các pixel cực sáng (vạch trắng). 
        # LƯU Ý: Nếu phòng tối, hãy giảm số 180 xuống (VD: 150). Nếu phòng sáng chói, tăng lên 200.
        _, mask = cv2.threshold(blur, 180, 255, cv2.THRESH_BINARY)
        
        # Xóa các đốm nhiễu nhỏ
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
        return mask

    # ==========================================
    # CẢI TIẾN 2: THUẬT TOÁN QUÉT TIA (RAYCAST) CHỐNG CHÉO HÌNH THANG
    # ==========================================
    def _scan_outwards(self, row_mask: np.ndarray, start_x: int) -> Tuple[Optional[int], Optional[int]]:
        """Bắn tia từ giữa xe sang 2 bên để tìm mép trong của vạch trắng"""
        row_1d = row_mask  # Lấy mảng 1D pixel trắng/đen
        
        l_pt = None
        r_pt = None
        
        # 1. Quét từ tâm sang TRÁI
        for x in range(start_x, 0, -1):
            if row_1d[x] > 0:  # Chạm pixel trắng đầu tiên
                l_pt = x
                break
                
        # 2. Quét từ tâm sang PHẢI
        for x in range(start_x, self.w - 1):
            if row_1d[x] > 0:  # Chạm pixel trắng đầu tiên
                r_pt = x
                break
                
        return l_pt, r_pt

    def process_frame(self, frame: np.ndarray):
        mask = self._get_clean_mask(frame)
        viz_frame = frame.copy()
        
        # Hiển thị mask nhỏ ở góc màn hình để bạn dễ debug độ sáng
        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mask_small = cv2.resize(mask_bgr, (160, 120))
        viz_frame[0:120, 0:160] = mask_small
        cv2.putText(viz_frame, "Binary Mask", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        car_center_x = self.w // 2

        # Lấy 2 dòng pixel (quét ngang) tại y_bottom và y_top
        row_bot = mask[self.y_bottom, :]
        row_top = mask[self.y_top, :]

        # Dùng thuật toán tia quét để tìm điểm neo
        l_bot, r_bot = self._scan_outwards(row_bot, car_center_x)
        l_top, r_top = self._scan_outwards(row_top, car_center_x)

        # Logic nội suy độ rộng làn đường
        valid_widths = []
        if l_bot is not None and r_bot is not None: valid_widths.append(r_bot - l_bot)
        if l_top is not None and r_top is not None: valid_widths.append(r_top - l_top)
        
        if valid_widths:
            self.lane_width_history.append(np.mean(valid_widths))
        current_w = int(np.mean(self.lane_width_history)) if self.lane_width_history else self.default_lane_width

        if l_bot is None and r_bot is not None: l_bot = max(0, r_bot - current_w)
        if r_bot is None and l_bot is not None: r_bot = min(self.w, l_bot + current_w)
        if l_top is None and r_top is not None: l_top = max(0, r_top - current_w)
        if r_top is None and l_top is not None: r_top = min(self.w, l_top + current_w)

        steer_final = 0.0
        bev_vis = np.zeros((self.h, self.w, 3), dtype=np.uint8)

        # ĐIỀU KIỆN CHẶT CHẼ: Hình thang không được phép chéo nhau (l_bot phải < r_bot)
        if (l_bot is not None and r_bot is not None and l_top is not None and r_top is not None 
            and l_bot < r_bot and l_top < r_top):
            
            mid_bot_x = int((l_bot + r_bot) / 2)
            mid_top_x = int((l_top + r_top) / 2)
            point_of_orientation = (mid_top_x, self.y_top)

            # Cập nhật bộ nhớ dự phòng
            self.prev_src_pts = np.float32([
                [l_bot, self.y_bottom], [r_bot, self.y_bottom],
                [l_top, self.y_top], [r_top, self.y_top]
            ])

            # Vẽ Hình thang động ôm khít mép trong vạch kẻ
            trap_pts = np.array([[l_bot, self.y_bottom], [l_top, self.y_top], 
                                 [r_top, self.y_top], [r_bot, self.y_bottom]], np.int32)
            cv2.polylines(viz_frame, [trap_pts], True, (0, 165, 255), 3)
            
            # Vẽ đường Line Center và Point of Orientation
            cv2.line(viz_frame, (mid_bot_x, self.y_bottom), point_of_orientation, (0, 255, 255), 3)
            cv2.circle(viz_frame, point_of_orientation, 8, (0, 0, 255), -1)
            cv2.putText(viz_frame, "Point of Orientation", (mid_top_x + 15, self.y_top), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            # PD Controller
            e_offset = (mid_bot_x - car_center_x) / (self.w / 2.0)
            e_heading = (mid_top_x - mid_bot_x) / (self.w / 2.0)
            raw_steer = np.clip((self.Kp * e_offset) + (self.Kd * e_heading), -1.0, 1.0)
            steer_final = self.steer_ema.update(raw_steer)

            # BEV
            dst_bev = np.float32([[int(self.w * 0.25), self.h], [int(self.w * 0.75), self.h], 
                                  [int(self.w * 0.25), 0], [int(self.w * 0.75), 0]])
            matrix_bev = cv2.getPerspectiveTransform(self.prev_src_pts, dst_bev)
            bev_vis = cv2.warpPerspective(frame, matrix_bev, (self.w, self.h))
            
            mask_bev = cv2.warpPerspective(mask, matrix_bev, (self.w, self.h))
            mask_bev_bgr = cv2.cvtColor(mask_bev, cv2.COLOR_GRAY2BGR)
            bev_vis = cv2.addWeighted(bev_vis, 0.7, mask_bev_bgr, 1.0, 0)
            
            cv2.line(bev_vis, (self.w//2, self.h), (self.w//2, 0), (0, 255, 0), 2, cv2.LINE_DASH)
            
        else:
            # Nếu vi phạm điều kiện (ví dụ 2 vạch chéo nhau), sử dụng bộ nhớ cũ!
            steer_final = self.steer_ema.update(None)
            cv2.putText(viz_frame, "FALLBACK: KEEPING PREVIOUS SHAPE", (180, 80), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            # Vẽ lại hình thang cũ mờ hơn để báo hiệu
            trap_pts = np.array([[self.prev_src_pts[0][0], self.prev_src_pts[0][1]], 
                                 [self.prev_src_pts[2][0], self.prev_src_pts[2][1]], 
                                 [self.prev_src_pts[3][0], self.prev_src_pts[3][1]], 
                                 [self.prev_src_pts[1][0], self.prev_src_pts[1][1]]], np.int32)
            cv2.polylines(viz_frame, [trap_pts], True, (0, 0, 255), 2)

        cv2.putText(viz_frame, f"STEER: {steer_final:+.3f}", (180, 40), cv2.FONT_HERSHEY_DUPLEX, 1, (255, 255, 255), 2)
        return steer_final, viz_frame, bev_vis

def main():
    cap = cv2.VideoCapture(0)
    tracker = UltimateDynamicTracker()
    while True:
        ret, frame = cap.read()
        if not ret: break
        frame = cv2.resize(frame, (640, 480))
        steer, viz, bev = tracker.process_frame(frame)1, cv2.LINE_AA
        cv2.imshow("Main Output", viz)
        cv2.imshow("BEV", bev)
        if cv2.waitKey(1) & 0xFF == ord('q'): break
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()