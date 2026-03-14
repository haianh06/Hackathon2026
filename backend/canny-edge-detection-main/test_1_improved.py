import cv2
import numpy as np
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

class AdaptiveTrackerV2:
    def __init__(self, width: int = 640, height: int = 480):
        self.w = width
        self.h = height
        
        # ==========================================
        # 1. THRESHOLD CĂN CHỈNH CAMERA
        # ==========================================
        self.camera_x_offset = 0    
        
        # ĐẨY ROI RA XA XE HƠN (Giá trị càng nhỏ càng xa xe)
        self.y_bottom = int(self.h * 0.85) # Cạnh dưới của thang (đã đẩy xa hơn so với 0.95 cũ)
        self.y_top = int(self.h * 0.40)    # Cạnh trên của thang (đẩy xa lên mốc 40% ảnh)
        
        self.num_scans = 8  # Số lượng vạch gióng ngang quét trong ROI
        self.standard_lane_width = int(self.w * 0.6) 
        
        # Trạng thái ROI ban đầu
        self.roi_x = np.array([self.w*0.2, self.w*0.8, self.w*0.35, self.w*0.65], dtype=np.float32)
        self.roi_ema_alpha = 0.2 

        self.Kp = 0.85
        self.Kd = 0.60
        self.steer_ema = EMAFilter(alpha=0.3)

    def _get_clean_mask(self, bgr_img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, mask = cv2.threshold(blur, 180, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    def _get_line_edges(self, mask_1d: np.ndarray, expected_x: int, window: int = 80) -> Optional[Tuple[int, int]]:
        """Tìm rìa trong và rìa ngoài của 1 vạch kẻ trắng"""
        start = max(0, expected_x - window)
        end = min(self.w, expected_x + window)
        local_slice = mask_1d[start:end]
        white_indices = np.where(local_slice > 0)[0]
        
        if len(white_indices) < 3: return None
            
        l_edge_local = white_indices[0]
        r_edge_local = white_indices[-1]
        
        # Bỏ qua nếu vệt trắng quá lớn (ví dụ: ngã tư, nhiễu ánh sáng)
        if (r_edge_local - l_edge_local) > (end - start) * 0.6: return None
            
        return start + l_edge_local, start + r_edge_local

    def process_frame(self, frame: np.ndarray):
        mask = self._get_clean_mask(frame)
        viz_frame = frame.copy()
        car_center_x = (self.w // 2) + self.camera_x_offset

        l_bot_exp, r_bot_exp, l_top_exp, r_top_exp = self.roi_x
        y_steps = np.linspace(self.y_bottom, self.y_top, self.num_scans, dtype=int)
        
        valid_l_pts, valid_r_pts = [], []
        center_path_pts = [] # Lưu các điểm trung tâm (chấm đỏ) để vẽ xương sống
        
        # Màu sắc hiển thị
        COLOR_OUTER = (150, 0, 0)   # Xanh sẫm (rìa ngoài)
        COLOR_INNER = (255, 255, 0) # Xanh lơ (rìa trong)
        COLOR_MID = (0, 255, 255)   # Vàng (trung điểm vạch)
        COLOR_CENTER = (0, 0, 255)  # Đỏ (tâm làn đường)

        # ==========================================
        # BƯỚC 1: QUÉT TÌM CẠNH, ĐIỂM VÀ VẼ LƯỚI
        # ==========================================
        for y in y_steps:
            ratio = (self.y_bottom - y) / (self.y_bottom - self.y_top + 1e-5)
            exp_l = int(l_bot_exp + ratio * (l_top_exp - l_bot_exp))
            exp_r = int(r_bot_exp + ratio * (r_top_exp - r_bot_exp))
            
            row_slice = np.max(mask[max(0, y-2):min(self.h, y+2), :], axis=0)
            
            l_edges = self._get_line_edges(row_slice, exp_l, window=60)
            r_edges = self._get_line_edges(row_slice, exp_r, window=60)
            
            l_mid_val, r_mid_val = None, None

            # Xử lý vạch trái
            if l_edges:
                l_out, l_in = l_edges # Rìa ngoài và rìa trong của vạch trái
                l_mid_val = (l_out + l_in) // 2
                valid_l_pts.append((y, l_mid_val))
                
                # Nối rìa trong - rìa ngoài bằng nét màu xanh lá
                cv2.line(viz_frame, (l_out, y), (l_in, y), (0, 255, 0), 2)
                # Vẽ các chấm
                cv2.circle(viz_frame, (l_out, y), 3, COLOR_OUTER, -1)
                cv2.circle(viz_frame, (l_in, y), 3, COLOR_INNER, -1)
                cv2.circle(viz_frame, (l_mid_val, y), 3, COLOR_MID, -1)

            # Xử lý vạch phải
            if r_edges:
                r_in, r_out = r_edges # Rìa trong và rìa ngoài của vạch phải
                r_mid_val = (r_in + r_out) // 2
                valid_r_pts.append((y, r_mid_val))
                
                # Nối rìa trong - rìa ngoài bằng nét màu xanh lá
                cv2.line(viz_frame, (r_in, y), (r_out, y), (0, 255, 0), 2)
                # Vẽ các chấm
                cv2.circle(viz_frame, (r_in, y), 3, COLOR_INNER, -1)
                cv2.circle(viz_frame, (r_out, y), 3, COLOR_OUTER, -1)
                cv2.circle(viz_frame, (r_mid_val, y), 3, COLOR_MID, -1)

            # Dự đoán nếu thiếu 1 bên (giúp vẽ lưới đều)
            if l_mid_val is not None and r_mid_val is None:
                r_mid_val = l_mid_val + self.standard_lane_width
            elif r_mid_val is not None and l_mid_val is None:
                l_mid_val = r_mid_val - self.standard_lane_width

            # Nếu có cả 2 bên (thực tế hoặc dự đoán), vẽ đường gióng và tâm
            if l_mid_val is not None and r_mid_val is not None:
                # 1. Đường gióng ngang (màu tím) nối 2 vạch kẻ
                cv2.line(viz_frame, (l_mid_val, y), (r_mid_val, y), (200, 100, 200), 1)
                
                # 2. Điểm tâm giữa làn đường
                center_x = (l_mid_val + r_mid_val) // 2
                center_path_pts.append((center_x, y))
                cv2.circle(viz_frame, (center_x, y), 4, COLOR_CENTER, -1) # Chấm đỏ giữa

        # ==========================================
        # BƯỚC 2: HỒI QUY XÁC ĐỊNH CẠNH CỦA ROI THANG
        # ==========================================
        poly_l, poly_r = None, None
        
        if len(valid_l_pts) >= 3:
            y_coords, x_coords = zip(*valid_l_pts)
            poly_l = np.poly1d(np.polyfit(y_coords, x_coords, 1))
            
        if len(valid_r_pts) >= 3:
            y_coords, x_coords = zip(*valid_r_pts)
            poly_r = np.poly1d(np.polyfit(y_coords, x_coords, 1))

        # Cứu hộ ROI
        if poly_l is not None and poly_r is None:
            poly_r = np.poly1d([poly_l[1], poly_l[0] + self.standard_lane_width])
        elif poly_r is not None and poly_l is None:
            poly_l = np.poly1d([poly_r[1], poly_r[0] - self.standard_lane_width])

        is_tracking = False
        if poly_l is not None and poly_r is not None:
            is_tracking = True
            target_l_bot, target_l_top = poly_l(self.y_bottom), poly_l(self.y_top)
            target_r_bot, target_r_top = poly_r(self.y_bottom), poly_r(self.y_top)
            
            new_target = np.array([target_l_bot, target_r_bot, target_l_top, target_r_top], dtype=np.float32)
            self.roi_x = (self.roi_ema_alpha * new_target) + ((1 - self.roi_ema_alpha) * self.roi_x)

        curr_l_bot, curr_r_bot, curr_l_top, curr_r_top = map(int, self.roi_x)

        # ==========================================
        # BƯỚC 3: VẼ KHUNG ROI VÀ ĐƯỜNG ĐỊNH HƯỚNG
        # ==========================================
        # Vẽ khung thang (cạnh ngoài cùng)
        trap_pts = np.array([[curr_l_bot, self.y_bottom], [curr_l_top, self.y_top], 
                             [curr_r_top, self.y_top], [curr_r_bot, self.y_bottom]], np.int32)
        cv2.polylines(viz_frame, [trap_pts], True, (0, 0, 255), 3) # Vạch đỏ đậm bọc ngoài

        # Vẽ xương sống lượn sóng (nối các tâm thực tế tìm được)
        if len(center_path_pts) > 1:
            for i in range(len(center_path_pts) - 1):
                cv2.line(viz_frame, center_path_pts[i], center_path_pts[i+1], COLOR_MID, 2)
                
        # Vẽ Point of Orientation (Tâm trên cùng) nối về xe
        mid_bot_x = (curr_l_bot + curr_r_bot) // 2
        mid_top_x = (curr_l_top + curr_r_top) // 2
        
        # Đường thẳng từ Point of Orientation xuống đuôi
        cv2.line(viz_frame, (mid_top_x, self.y_top), (car_center_x, self.h), (0, 255, 255), 2)
        
        # Đánh dấu Point of Orientation
        cv2.circle(viz_frame, (mid_top_x, self.y_top), 6, COLOR_CENTER, -1)

        # Tính toán vô lăng
        e_offset = (mid_bot_x - car_center_x) / (self.w / 2.0)
        e_heading = (mid_top_x - mid_bot_x) / (self.w / 2.0)
        raw_steer = np.clip((self.Kp * e_offset) + (self.Kd * e_heading), -1.0, 1.0)
        steer_final = self.steer_ema.update(raw_steer)
        
        # Hiển thị text
        status = "VIRTUAL ROI: HOLDING SHAPE!" if is_tracking else "VIRTUAL ROI: PREDICTING"
        cv2.putText(viz_frame, f"STEER: {steer_final:+.3f}", (150, 40), cv2.FONT_HERSHEY_DUPLEX, 1, (255, 255, 255), 2)
        cv2.putText(viz_frame, status, (150, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
        return steer_final, viz_frame

def main():
    cap = cv2.VideoCapture(0)
    tracker = AdaptiveTrackerV2()
    while True:
        ret, frame = cap.read()
        if not ret: break
        frame = cv2.resize(frame, (640, 480))
        steer, viz = tracker.process_frame(frame)
        cv2.imshow("Adaptive ROI Track", viz)
        if cv2.waitKey(1) & 0xFF == ord('q'): break
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()