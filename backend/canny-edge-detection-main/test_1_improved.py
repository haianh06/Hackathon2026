import cv2
import numpy as np
from collections import deque
import time
from typing import Optional, Tuple, List, Dict

# =========================
# Utility: Exponential Moving Average
# =========================
class EMA:
    """Exponential Moving Average for smoothing noisy sensor data."""
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


# =========================
# Utility: Perspective Transform (Bird's-Eye View)
# =========================
class BirdEyeView:
    """Handles perspective warp and unwarp operations."""
    def __init__(self, w: int, h: int):
        self.w = w
        self.h = h
        
        # Tọa độ hình thang trên ảnh gốc (Bạn cần tinh chỉnh theo góc cam thực tế của xe)
        # Thông số này đang thiết lập cho ảnh 640x480, camera đặt giữa xe
        self.src_pts = np.float32([
            [int(w * 0.15), h],                 # Bottom-left
            [int(w * 0.85), h],                 # Bottom-right
            [int(w * 0.40), int(h * 0.60)],     # Top-left
            [int(w * 0.60), int(h * 0.60)]      # Top-right
        ])
        
        # Tọa độ hình chữ nhật trên ảnh Bird's-Eye View
        self.offset = int(w * 0.25)
        self.dst_pts = np.float32([
            [self.offset, h],                   # Bottom-left
            [w - self.offset, h],               # Bottom-right
            [self.offset, 0],                   # Top-left
            [w - self.offset, 0]                # Top-right
        ])
        
        # Tính toán ma trận biến đổi
        self.M = cv2.getPerspectiveTransform(self.src_pts, self.dst_pts)
        self.Minv = cv2.getPerspectiveTransform(self.dst_pts, self.src_pts)

    def warp(self, img: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(img, self.M, (self.w, self.h), flags=cv2.INTER_LINEAR)

    def unwarp(self, img: np.ndarray) -> np.ndarray:
        return cv2.warpPerspective(img, self.Minv, (self.w, self.h), flags=cv2.INTER_LINEAR)


# =========================
# Lane Detector with Virtual Markers & BEV
# =========================
class PointsOfOrientationLane:
    def __init__(
        self,
        n_heights: int = 15,           # Tăng số điểm quét trên BEV
        search_margin_px: int = 150,   # Margin nhỏ lại vì vạch trong BEV song song
        min_lane_width_px: int = 100,  # Độ rộng vạch đường trong BEV lớn hơn
        ema_center_alpha: float = 0.35,
        max_frames_lost: int = 30,
        use_virtual_markers: bool = True
    ):
        self.n_heights = n_heights
        self.search_margin_px = search_margin_px
        self.min_lane_width_px = min_lane_width_px
        self.max_frames_lost = max_frames_lost
        self.use_virtual_markers = use_virtual_markers
        
        self.center_ema = EMA(alpha=ema_center_alpha, init=None)
        self.last_good_center = None
        self.frames_lost = 0
        
        self.last_left_poly = None
        self.last_right_poly = None
        self.lane_width_history = deque(maxlen=30)

    def _binary_edges(self, bev_bgr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Tối ưu hóa: Auto-Canny + CLAHE + HLS trên ảnh Bird's-Eye View"""
        # 1. Tiền xử lý ánh sáng bằng CLAHE
        lab = cv2.cvtColor(bev_bgr, cv2.COLOR_BGR2LAB)
        l_channel, a, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl = clahe.apply(l_channel)
        limg = cv2.merge((cl, a, b_channel))
        bgr_enhanced = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)

        # 2. Không gian màu HLS
        hls = cv2.cvtColor(bgr_enhanced, cv2.COLOR_BGR2HLS)
        white = cv2.inRange(hls, (0, 200, 0), (180, 255, 255)) 
        yellow = cv2.inRange(hls, (15, 30, 115), (35, 204, 255))
        color_mask = cv2.bitwise_or(white, yellow)
        
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel)

        # 3. AUTO-CANNY
        gray = cv2.cvtColor(bgr_enhanced, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        
        v = np.median(blur)
        sigma = 0.33
        lower_canny = int(max(0, (1.0 - sigma) * v))
        upper_canny = int(min(255, (1.0 + sigma) * v))
        edges = cv2.Canny(blur, lower_canny, upper_canny)

        # 4. FUSION
        dilated_color = cv2.dilate(color_mask, kernel, iterations=1)
        final_edges = cv2.bitwise_and(edges, dilated_color)

        return final_edges, color_mask

    @staticmethod
    def _find_edge_x_in_row(row: np.ndarray, x_ref: float, direction: int, left_bound: int, right_bound: int) -> Optional[int]:
        w = row.shape[0]
        if direction < 0:
            xs = range(int(x_ref), left_bound - 1, -1)
        else:
            xs = range(int(x_ref), right_bound + 1, 1)

        for x in xs:
            if 0 <= x < w and row[x] != 0:
                return x
        return None

    def _fit_lane_poly(self, points: List[Tuple[float, float]]) -> Optional[np.ndarray]:
        if len(points) < 3: return None
        xs = np.array([p[0] for p in points], dtype=np.float32)
        ys = np.array([p[1] for p in points], dtype=np.float32)
        try:
            return np.polyfit(ys, xs, 2)
        except (np.linalg.LinAlgError, ValueError):
            return None

    def _extrapolate_lane(self, lefts: List[Tuple[float, float]], rights: List[Tuple[float, float]], frame_h: int) -> Tuple[List, List, Dict]:
        virtual_info = {"used_virtual_left": False, "used_virtual_right": False}
        extended_lefts = list(lefts)
        extended_rights = list(rights)
        
        left_poly = self._fit_lane_poly(lefts)
        right_poly = self._fit_lane_poly(rights)
        
        if left_poly is None and self.last_left_poly is not None: left_poly = self.last_left_poly
        if right_poly is None and self.last_right_poly is not None: right_poly = self.last_right_poly
        
        if self.use_virtual_markers:
            ys = np.linspace(0, frame_h - 1, self.n_heights).astype(int)
            
            if left_poly is None and right_poly is not None and len(self.lane_width_history) > 0:
                avg_width = np.median(self.lane_width_history)
                virtual_info["used_virtual_left"] = True
                for y in ys:
                    x_right = right_poly[0]*y*y + right_poly[1]*y + right_poly[2]
                    extended_lefts.append((x_right - avg_width, int(y)))
                left_poly = self._fit_lane_poly(extended_lefts)
            
            if right_poly is None and left_poly is not None and len(self.lane_width_history) > 0:
                avg_width = np.median(self.lane_width_history)
                virtual_info["used_virtual_right"] = True
                for y in ys:
                    x_left = left_poly[0]*y*y + left_poly[1]*y + left_poly[2]
                    extended_rights.append((x_left + avg_width, int(y)))
                right_poly = self._fit_lane_poly(extended_rights)
        
        if left_poly is not None: self.last_left_poly = left_poly
        if right_poly is not None: self.last_right_poly = right_poly
        
        return extended_lefts, extended_rights, virtual_info

    def detect(self, bev_frame: np.ndarray) -> Tuple[List, List, List, Dict]:
        h, w = bev_frame.shape[:2]
        edges, masked = self._binary_edges(bev_frame)

        # Quét từ dưới lên trên trong ảnh BEV
        ys = np.linspace(h - 1, 0, self.n_heights).astype(int)
        x_center_ref = self.last_good_center if self.last_good_center is not None else (w / 2)
        
        centers, lefts, rights = [], [], []

        for y in ys:
            row = edges[y, :]
            left_bound = max(0, int(x_center_ref - self.search_margin_px))
            right_bound = min(w - 1, int(x_center_ref + self.search_margin_px))

            xL = self._find_edge_x_in_row(row, x_center_ref, -1, left_bound, right_bound)
            xR = self._find_edge_x_in_row(row, x_center_ref, +1, left_bound, right_bound)

            if xL is None or xR is None: continue
            if (xR - xL) < self.min_lane_width_px: continue

            xC = 0.5 * (xL + xR)
            self.lane_width_history.append(xR - xL)
            lefts.append((xL, y))
            rights.append((xR, y))
            centers.append((xC, y))
            x_center_ref = xC  # Cập nhật center cho window tiếp theo

        extended_lefts, extended_rights, virtual_info = self._extrapolate_lane(lefts, rights, h)

        if len(extended_lefts) > 0 and len(extended_rights) > 0:
            centers.clear()
            for l, r in zip(extended_lefts, extended_rights):
                centers.append(((l[0] + r[0]) / 2, (l[1] + r[1]) / 2))

        if len(centers) > 0:
            # Lấy điểm đáy (gần xe nhất) làm reference cho frame sau
            x_bottom = max(centers, key=lambda p: p[1])[0]
            self.last_good_center = self.center_ema.update(x_bottom)
            self.frames_lost = 0
            lane_quality = len(lefts) / self.n_heights if len(lefts) > 0 else 0.5
        else:
            self.center_ema.update(None)
            self.frames_lost += 1
            lane_quality = 0.0
            if self.frames_lost > self.max_frames_lost:
                self.last_good_center = None
                self.center_ema.reset()

        debug = {
            "edges": edges, "masked": masked,
            "lane_quality": lane_quality, "frames_lost": self.frames_lost,
            "virtual_markers": virtual_info,
            "extended_lefts": extended_lefts, "extended_rights": extended_rights
        }
        return centers, extended_lefts, extended_rights, debug

    def compute_steering(self, centers: List[Tuple[float, float]], frame_w: int, frame_h: int, k_gain: float = 1.15, lookahead_y_frac: float = 0.60) -> Tuple[Optional[float], Optional[Tuple[float, float]]]:
        if len(centers) < 2: return None, None
        xs = np.array([p[0] for p in centers], dtype=np.float32)
        ys = np.array([p[1] for p in centers], dtype=np.float32)
        try:
            a, b, c = np.polyfit(ys, xs, 2)
        except np.linalg.LinAlgError:
            return None, None

        # Look-ahead point (tính từ đáy màn hình đi lên)
        y_look = frame_h * (1.0 - lookahead_y_frac)
        x_look = a*y_look*y_look + b*y_look + c

        # Điểm tâm xe (đáy màn hình)
        car_center = frame_w / 2.0
        
        # Sai số đánh lái trong hệ tọa độ BEV
        err = (x_look - car_center) / car_center
        steer = np.clip(k_gain * np.clip(err, -1.0, 1.0), -1.0, 1.0)
        
        return float(steer), (float(x_look), float(y_look))

# =========================
# Main Display and Overlay
# =========================
def draw_overlay(original_frame: np.ndarray, bev: BirdEyeView, lefts: List, rights: List, target_point: Optional[Tuple], quality: float, frames_lost: int) -> np.ndarray:
    """Vẽ vùng làn đường trên BEV, sau đó unwarp dán lên ảnh gốc."""
    h, w = original_frame.shape[:2]
    color_warp = np.zeros_like(original_frame)
    
    # 1. Vẽ đa giác làn đường trên ảnh trống BEV
    if len(lefts) > 1 and len(rights) > 1:
        poly_points = lefts + list(reversed(rights))
        cv2.fillPoly(color_warp, [np.array(poly_points, dtype=np.int32)], (0, 255, 0))
    
    # 2. Unwarp cái vùng xanh lá đó trở lại góc 3D
    newwarp = bev.unwarp(color_warp)
    
    # 3. Chồng ảnh
    result = cv2.addWeighted(original_frame, 1, newwarp, 0.3, 0)
    
    # 4. Vẽ UI/UX
    # Vẽ hình thang nguồn để debug góc cam
    pts = bev.src_pts.astype(np.int32)
    cv2.polylines(result, [pts], isClosed=True, color=(0, 0, 255), thickness=2)
    
    # Bar chất lượng
    bar_w = int(w * 0.3)
    cv2.rectangle(result, (w - bar_w - 10, h - 30), (w - 10, h - 10), (0, 0, 255), -1)
    cv2.rectangle(result, (w - bar_w - 10, h - 30), (w - bar_w - 10 + int(bar_w * quality), h - 10), (0, 255, 0), -1)
    
    if frames_lost > 5:
        cv2.putText(result, "LANE LOST", (w//2 - 100, h//2), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)

    return result

def draw_bev_debug(bev_img: np.ndarray, centers: List, lefts: List, rights: List, target_point: Optional[Tuple], virtual_info: Dict) -> np.ndarray:
    """Vẽ debug trên màn hình Bird's-Eye View"""
    out = bev_img.copy()
    for (x, y) in lefts: cv2.circle(out, (int(x), int(y)), 4, (0, 0, 255), -1)
    for (x, y) in rights: cv2.circle(out, (int(x), int(y)), 4, (255, 0, 0), -1)
    for c in centers: cv2.circle(out, (int(c[0]), int(c[1])), 4, (0, 255, 0), -1)
    
    if target_point:
        cv2.line(out, (out.shape[1]//2, out.shape[0]), (int(target_point[0]), int(target_point[1])), (0, 255, 255), 2)
        cv2.circle(out, (int(target_point[0]), int(target_point[1])), 8, (0, 255, 255), -1)

    if virtual_info.get("used_virtual_left"): cv2.putText(out, "[V-LEFT]", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
    if virtual_info.get("used_virtual_right"): cv2.putText(out, "[V-RIGHT]", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
    
    return out

# =========================
# Main Loop
# =========================
def main():
    # Sử dụng video hoặc cam
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Khởi tạo ma trận biến đổi và bộ phát hiện làn
    bev = BirdEyeView(w=640, h=480)
    lane = PointsOfOrientationLane(n_heights=15, use_virtual_markers=True)
    
    while True:
        ok, frame = cap.read()
        if not ok: break
        
        # 1. Transform sang Bird's-Eye View
        bev_frame = bev.warp(frame)
        
        # 2. Xử lý nhận diện trên ảnh BEV
        centers, lefts, rights, debug = lane.detect(bev_frame)
        
        # 3. Tính góc lái trên BEV (Chính xác toán học nhất)
        steer, target_bev = lane.compute_steering(centers, frame.shape[1], frame.shape[0], k_gain=1.15)
        
        # 4. Hiển thị
        # Ảnh BEV Debug (Nhìn từ trên xuống)
        bev_vis = draw_bev_debug(bev_frame, centers, lefts, rights, target_bev, debug["virtual_markers"])
        
        # Ảnh Canny Debug (Để thấy bộ lọc hoạt động tốt thế nào)
        edges_bgr = cv2.cvtColor(debug["edges"], cv2.COLOR_GRAY2BGR)
        
        # Ảnh gốc hiển thị cho người lái (Dán mask xanh lá)
        final_vis = draw_overlay(frame, bev, debug["extended_lefts"], debug["extended_rights"], target_bev, debug["lane_quality"], debug["frames_lost"])
        
        # In góc lái
        steer_str = f"Steer: {steer:+.3f}" if steer is not None else "Steer: LOST"
        cv2.putText(final_vis, steer_str, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        # Trình chiếu
        cv2.imshow("Driver View (Unwarped)", final_vis)
        cv2.imshow("BEV View (Processing)", bev_vis)
        cv2.imshow("BEV Canny Edges", edges_bgr)
        
        if cv2.waitKey(1) & 0xFF == ord('q'): break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()