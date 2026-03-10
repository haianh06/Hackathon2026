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
        
        # Vị trí kỳ vọng ban đầu cho thuật toán Cửa sổ trượt (Sliding Window)
        self.l_bot_exp = int(self.w * 0.15)
        self.r_bot_exp = int(self.w * 0.85)
        self.l_top_exp = int(self.w * 0.35)
        self.r_top_exp = int(self.w * 0.65)
        
        # Hình thang dự phòng chuẩn xác
        self.prev_src_pts = np.float32([
            [self.l_bot_exp, self.y_bottom], [self.r_bot_exp, self.y_bottom],
            [self.l_top_exp, self.y_top], [self.r_top_exp, self.y_top]
        ])

        self.Kp = 0.85
        self.Kd = 0.60
        self.steer_ema = EMAFilter(alpha=0.3)

    # ==========================================
    # CẢI TIẾN 1: TÁCH NỀN SIÊU SẠCH (Từ phiên bản của bạn)
    # ==========================================
    def _get_clean_mask(self, bgr_img: np.ndarray) -> np.ndarray:
        """Chỉ tập trung bắt màu sáng/trắng, loại bỏ nhiễu Canny rối rắm"""
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        
        # Lọc nhị phân: Chỉ giữ lại các pixel cực sáng (vạch trắng). 
        # LƯU Ý: Có thể chỉnh 180 tùy độ sáng môi trường.
        _, mask = cv2.threshold(blur, 180, 255, cv2.THRESH_BINARY)
        
        # Xóa các đốm nhiễu nhỏ
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
        return mask

    # ==========================================
    # CẢI TIẾN 2: CỬA SỔ TRƯỢT CHỐNG NGÃ TƯ (Thay thế hoàn toàn Raycast)
    # ==========================================
    def _find_line_near(self, mask_1d: np.ndarray, expected_x: int, window: int = 80) -> Optional[int]:
        """Chỉ quét tìm vạch trong một giới hạn (window) quanh vị trí cũ"""
        start = max(0, expected_x - window)
        end = min(self.w, expected_x + window)
        
        local_slice = mask_1d[start:end]
        white_indices = np.where(local_slice > 0)[0]
        
        if len(white_indices) == 0:
            return None  # Không thấy vạch
            
        # TÍNH NĂNG VÀNG: ĐÓNG BĂNG KHI GẶP VẠCH NGANG (NGÃ TƯ)
        # Nếu vạch trắng lấp đầy > 75% cửa sổ -> Chắc chắn là ngã tư -> Trả về vị trí cũ để đi thẳng
        if len(white_indices) > (end - start) * 0.75:
            return expected_x 
            
        # Nếu bình thường, tính tâm của vạch trắng trong cửa sổ
        local_center = int(np.mean(white_indices))
        return start + local_center

    def process_frame(self, frame: np.ndarray):
        mask = self._get_clean_mask(frame)
        viz_frame = frame.copy()
        
        # Hiển thị mask nhỏ ở góc màn hình để dễ debug độ sáng
        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mask_small = cv2.resize(mask_bgr, (160, 120))
        viz_frame[0:120, 0:160] = mask_small
        cv2.putText(viz_frame, "Binary Mask", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        car_center_x = self.w // 2

        # Lấy dải ảnh (slice) dày 10 pixel thay vì 1 pixel để chống nhiễu đứt đoạn
        slice_h = 5
        bot_slice = mask[max(0, self.y_bottom - slice_h) : min(self.h, self.y_bottom + slice_h), :]
        top_slice = mask[max(0, self.y_top - slice_h) : min(self.h, self.y_top + slice_h), :]
        row_bot = np.max(bot_slice, axis=0)
        row_top = np.max(top_slice, axis=0)

        # Quét tìm vạch bằng Cửa sổ trượt (Sliding Window)
        l_bot = self._find_line_near(row_bot, self.l_bot_exp, window=100)
        r_bot = self._find_line_near(row_bot, self.r_bot_exp, window=100)
        l_top = self._find_line_near(row_top, self.l_top_exp, window=70)
        r_top = self._find_line_near(row_top, self.r_top_exp, window=70)

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

        # ĐIỀU KIỆN CHẶT CHẼ: Hình thang không được phép chéo nhau
        if (l_bot is not None and r_bot is not None and l_top is not None and r_top is not None 
            and l_bot < r_bot and l_top < r_top):
            
            # CẬP NHẬT VỊ TRÍ KỲ VỌNG CHO KHUNG HÌNH SAU (Giúp cửa sổ trượt bám theo cua)
            self.l_bot_exp = int(0.7 * self.l_bot_exp + 0.3 * l_bot)
            self.r_bot_exp = int(0.7 * self.r_bot_exp + 0.3 * r_bot)
            self.l_top_exp = int(0.7 * self.l_top_exp + 0.3 * l_top)
            self.r_top_exp = int(0.7 * self.r_top_exp + 0.3 * r_top)

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

            # Vẽ các ô tìm kiếm (Sliding Windows) màu xanh dương để debug
            cv2.rectangle(viz_frame, (self.l_bot_exp - 100, self.y_bottom-10), (self.l_bot_exp + 100, self.y_bottom+10), (255,0,0), 1)
            cv2.rectangle(viz_frame, (self.r_bot_exp - 100, self.y_bottom-10), (self.r_bot_exp + 100, self.y_bottom+10), (255,0,0), 1)
            cv2.rectangle(viz_frame, (self.l_top_exp - 70, self.y_top-10), (self.l_top_exp + 70, self.y_top+10), (255,0,0), 1)
            cv2.rectangle(viz_frame, (self.r_top_exp - 70, self.y_top-10), (self.r_top_exp + 70, self.y_top+10), (255,0,0), 1)

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
            
            cv2.line(bev_vis, (self.w//2, self.h), (self.w//2, 0), (0, 255, 0), 1, cv2.LINE_AA)
            
        else:
            # FALLBACK: Giữ bộ nhớ cũ khi hình thang chéo nhau hoặc mất vạch!
            steer_final = self.steer_ema.update(None)
            cv2.putText(viz_frame, "FALLBACK: KEEPING PREVIOUS SHAPE", (180, 80), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            # Vẽ lại hình thang cũ mờ hơn (màu đỏ) để báo hiệu
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
        steer, viz, bev = tracker.process_frame(frame)
        cv2.imshow("Main Output", viz)
        cv2.imshow("BEV", bev)
        if cv2.waitKey(1) & 0xFF == ord('q'): break
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()