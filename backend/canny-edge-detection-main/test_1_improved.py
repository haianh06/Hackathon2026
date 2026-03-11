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
        
        self.y_bottom = int(self.h * 0.90)  
        self.y_top = int(self.h * 0.55)
        self.num_scans = 10  # Số lượng đường cắt ngang để vẽ thang
        
        # Vị trí kỳ vọng tâm của vạch trắng (Ban đầu)
        self.l_bot_exp = int(self.w * 0.15)
        self.r_bot_exp = int(self.w * 0.85)
        self.l_top_exp = int(self.w * 0.35)
        self.r_top_exp = int(self.w * 0.65)
        
        # HÌNH THANG ROI ẢO (Dự phòng) - Lưu trữ 4 góc của hình thang lý tưởng
        self.virtual_roi_pts = np.float32([
            [self.l_bot_exp, self.y_bottom], [self.r_bot_exp, self.y_bottom],
            [self.l_top_exp, self.y_top], [self.r_top_exp, self.y_top]
        ])

        self.Kp = 0.85
        self.Kd = 0.60
        self.steer_ema = EMAFilter(alpha=0.3)
        self.roi_alpha = 0.2 

    def _get_clean_mask(self, bgr_img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, mask = cv2.threshold(blur, 180, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return mask

    # TRẢ VỀ CẢ MÉP TRONG, MÉP NGOÀI VÀ TRUNG ĐIỂM
    def _get_thick_line_info(self, mask_1d: np.ndarray, expected_x: int, window: int = 80) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        start = max(0, expected_x - window)
        end = min(self.w, expected_x + window)
        
        local_slice = mask_1d[start:end]
        white_indices = np.where(local_slice > 0)[0]
        
        if len(white_indices) < 3: 
            return None, None, None
            
        local_edge_1 = white_indices[0]
        local_edge_2 = white_indices[-1]
        line_thickness = local_edge_2 - local_edge_1
        
        # Đóng băng ngã tư nếu vạch quá dày
        if line_thickness > (end - start) * 0.6:
            return None, None, None 
            
        global_e1 = start + local_edge_1
        global_e2 = start + local_edge_2
        global_center = start + (local_edge_1 + local_edge_2) // 2
        return global_e1, global_e2, global_center

    def process_frame(self, frame: np.ndarray):
        mask = self._get_clean_mask(frame)
        viz_frame = frame.copy()
        
        # Mask debug góc màn hình
        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mask_small = cv2.resize(mask_bgr, (160, 120))
        viz_frame[0:120, 0:160] = mask_small

        car_center_x = self.w // 2

        # Lấy các điểm neo chuẩn từ ROI ảo để định hướng tìm kiếm
        vl_bot, vr_bot = int(self.virtual_roi_pts[0][0]), int(self.virtual_roi_pts[1][0])
        vl_top, vr_top = int(self.virtual_roi_pts[2][0]), int(self.virtual_roi_pts[3][0])

        # Tập hợp các điểm trung tâm (Orientation Points) để vẽ đường chạy
        orientation_points = []
        is_perfect_frame = True
        
        detected_l_bot, detected_r_bot = None, None
        detected_l_top, detected_r_top = None, None

        # Quét nhiều dòng (Tạo ra các bậc thang ngang)
        y_steps = np.linspace(self.y_bottom, self.y_top, self.num_scans, dtype=int)
        
        for i, y in enumerate(y_steps):
            # Tính tỉ lệ vị trí của dòng hiện tại (0.0 ở bottom, 1.0 ở top)
            ratio = (self.y_bottom - y) / (self.y_bottom - self.y_top + 1e-5)
            
            # Tính nội suy vị trí dự kiến dựa vào ROI ảo trước đó
            exp_l = int(vl_bot + ratio * (vl_top - vl_bot))
            exp_r = int(vr_bot + ratio * (vr_top - vr_bot))
            
            row_slice = np.max(mask[max(0, y-2):min(self.h, y+2), :], axis=0)
            
            # Tìm kiếm thông tin vạch
            le1, le2, l_mid = self._get_thick_line_info(row_slice, exp_l, window=60)
            re1, re2, r_mid = self._get_thick_line_info(row_slice, exp_r, window=60)
            
            # Nếu mất vạch, dùng luôn điểm ảo của ROI dự phòng (để hình thang luôn kín)
            if l_mid is None: 
                l_mid = exp_l; le1 = exp_l - 10; le2 = exp_l + 10; is_perfect_frame = False
            if r_mid is None: 
                r_mid = exp_r; re1 = exp_r - 10; re2 = exp_r + 10; is_perfect_frame = False

            # Ghi nhận 4 góc thực tế (Top và Bot) để cập nhật ROI nếu mọi thứ hoàn hảo
            if i == 0:  # Đang ở Bottom
                detected_l_bot, detected_r_bot = l_mid, r_mid
            elif i == self.num_scans - 1:  # Đang ở Top
                detected_l_top, detected_r_top = l_mid, r_mid

            # ==========================================
            # VẼ TRỰC QUAN HÓA (VISUALIZATION)
            # ==========================================
            # 1. Vẽ 4 cạnh (Mép trong/ngoài) & Độ dày vạch
            cv2.line(viz_frame, (le1, y), (le2, y), (0, 255, 0), 2)  # Xanh lá: Khoảng cách độ dày
            cv2.line(viz_frame, (re1, y), (re2, y), (0, 255, 0), 2)
            cv2.circle(viz_frame, (le1, y), 3, (255, 0, 0), -1)      # Xanh dương: Mép 1
            cv2.circle(viz_frame, (le2, y), 3, (255, 255, 0), -1)    # Xanh ngọc: Mép 2
            cv2.circle(viz_frame, (re1, y), 3, (255, 255, 0), -1)
            cv2.circle(viz_frame, (re2, y), 3, (255, 0, 0), -1)

            # 2. Vẽ Trung điểm của làn xe
            cv2.circle(viz_frame, (l_mid, y), 4, (0, 255, 255), -1)  # Vàng: Trung điểm trái
            cv2.circle(viz_frame, (r_mid, y), 4, (0, 255, 255), -1)  # Vàng: Trung điểm phải

            # 3. Vẽ Đường nối ngang 2 bên làn
            cv2.line(viz_frame, (l_mid, y), (r_mid, y), (200, 100, 200), 1) # Tím mờ: Đường ngang
            
            # 4. Tính toán và vẽ Điểm định hướng (Point of Orientation)
            row_center_x = (l_mid + r_mid) // 2
            orientation_points.append((row_center_x, y))
            cv2.circle(viz_frame, (row_center_x, y), 5, (0, 0, 255), -1) # Đỏ: Point of Orientation

        # CẬP NHẬT ROI ẢO (Nếu frame hoàn hảo, không bị mất nét nào)
        if is_perfect_frame and detected_l_bot < detected_r_bot and detected_l_top < detected_r_top:
            self.virtual_roi_pts[0][0] = (1 - self.roi_alpha) * self.virtual_roi_pts[0][0] + self.roi_alpha * detected_l_bot
            self.virtual_roi_pts[1][0] = (1 - self.roi_alpha) * self.virtual_roi_pts[1][0] + self.roi_alpha * detected_r_bot
            self.virtual_roi_pts[2][0] = (1 - self.roi_alpha) * self.virtual_roi_pts[2][0] + self.roi_alpha * detected_l_top
            self.virtual_roi_pts[3][0] = (1 - self.roi_alpha) * self.virtual_roi_pts[3][0] + self.roi_alpha * detected_r_top

        # ==========================================
        # VẼ HÌNH THANG ROI & ĐƯỜNG LÁI (STEERING PATH)
        # ==========================================
        curr_l_bot = int(self.virtual_roi_pts[0][0])
        curr_r_bot = int(self.virtual_roi_pts[1][0])
        curr_l_top = int(self.virtual_roi_pts[2][0])
        curr_r_top = int(self.virtual_roi_pts[3][0])

        trap_pts = np.array([[curr_l_bot, self.y_bottom], [curr_l_top, self.y_top], 
                             [curr_r_top, self.y_top], [curr_r_bot, self.y_bottom]], np.int32)

        # Nối các điểm Orientation lại thành Xương sống (Steering path)
        for i in range(len(orientation_points) - 1):
            cv2.line(viz_frame, orientation_points[i], orientation_points[i+1], (0, 255, 255), 2)
        
        if is_perfect_frame:
            cv2.polylines(viz_frame, [trap_pts], True, (0, 165, 255), 3)  # Cam: Đang bám tốt
            status_text = "ROI: LOCKED & TRACKING"
            color_status = (0, 255, 0)
        else:
            cv2.polylines(viz_frame, [trap_pts], True, (0, 0, 255), 3)    # Đỏ: Dùng bộ nhớ ảo
            status_text = "VIRTUAL ROI: HOLDING SHAPE!"
            color_status = (0, 0, 255)

        cv2.putText(viz_frame, status_text, (180, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_status, 2)

        # TÍNH GÓC ĐÁNH LÁI DỰA TRÊN ĐIỂM ORIENTATION TRÊN VÀ DƯỚI CÙNG
        mid_bot_x = orientation_points[0][0]
        mid_top_x = orientation_points[-1][0]

        e_offset = (mid_bot_x - car_center_x) / (self.w / 2.0)
        e_heading = (mid_top_x - mid_bot_x) / (self.w / 2.0)
        raw_steer = np.clip((self.Kp * e_offset) + (self.Kd * e_heading), -1.0, 1.0)
        steer_final = self.steer_ema.update(raw_steer)
        cv2.putText(viz_frame, f"STEER: {steer_final:+.3f}", (180, 40), cv2.FONT_HERSHEY_DUPLEX, 1, (255, 255, 255), 2)

        # HIỂN THỊ BEV
        dst_bev = np.float32([[int(self.w * 0.25), self.h], [int(self.w * 0.75), self.h], 
                              [int(self.w * 0.25), 0], [int(self.w * 0.75), 0]])
        matrix_bev = cv2.getPerspectiveTransform(self.virtual_roi_pts, dst_bev)
        bev_vis = cv2.warpPerspective(frame, matrix_bev, (self.w, self.h))
        mask_bev = cv2.warpPerspective(mask, matrix_bev, (self.w, self.h))
        mask_bev_bgr = cv2.cvtColor(mask_bev, cv2.COLOR_GRAY2BGR)
        bev_vis = cv2.addWeighted(bev_vis, 0.7, mask_bev_bgr, 1.0, 0)
        cv2.line(bev_vis, (self.w//2, self.h), (self.w//2, 0), (0, 255, 0), 1, cv2.LINE_AA)
            
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