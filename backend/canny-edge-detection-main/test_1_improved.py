import cv2
import numpy as np
from collections import deque
import time
from typing import Optional, Tuple, List, Dict

# =========================
# Utility: Exponential Moving Average
# =========================
class EMA:
    """Bộ lọc trung bình động hàm mũ (EMA) giúp làm mượt dữ liệu."""
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
# Core Lane Detector
# =========================
class PointsOfOrientationLane:
    """
    Nhận diện làn đường sử dụng Canny Edge, kết hợp Virtual Markers và làm mượt bằng hồi quy đa thức.
    """
    def __init__(
        self,
        heights_frac: Tuple[float, float] = (0.92, 0.60),
        n_heights: int = 10,
        roi_top_frac: float = 0.55,
        canny1: int = 50,
        canny2: int = 150,
        search_margin_px: int = 220,
        min_lane_width_px: int = 30,
        ema_center_alpha: float = 0.35,
        max_frames_lost: int = 30,
        use_virtual_markers: bool = True
    ):
        self.heights_frac = heights_frac
        self.n_heights = n_heights
        self.roi_top_frac = roi_top_frac
        self.canny1, self.canny2 = canny1, canny2
        self.search_margin_px = search_margin_px
        self.min_lane_width_px = min_lane_width_px
        self.max_frames_lost = max_frames_lost
        self.use_virtual_markers = use_virtual_markers
        
        self.center_ema = EMA(alpha=ema_center_alpha)
        self.last_good_center = None
        self.frames_lost = 0
        
        self.last_left_poly = None
        self.last_right_poly = None
        self.lane_width_history = deque(maxlen=20)

    def _roi_mask(self, h: int, w: int) -> np.ndarray:
        """Tạo mặt nạ ROI hình thang."""
        roi = np.zeros((h, w), dtype=np.uint8)
        top = int(self.roi_top_frac * h)
        poly = np.array([[(int(0.05*w), h), (int(0.42*w), top), 
                          (int(0.58*w), top), (int(0.95*w), h)]], dtype=np.int32)
        cv2.fillPoly(roi, poly, 255)
        return roi

    def _binary_edges(self, bgr: np.ndarray) -> np.ndarray:
        """Trích xuất cạnh bằng không gian màu HSV và thuật toán Canny."""
        h, w = bgr.shape[:2]
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        
        # Bắt màu trắng và vàng
        white = cv2.inRange(hsv, (0, 0, 175), (180, 60, 255))
        yellow = cv2.inRange(hsv, (15, 100, 100), (35, 255, 255))
        lane_mask = cv2.bitwise_or(white, yellow)
        
        # Áp dụng ROI và morphology
        masked = cv2.bitwise_and(lane_mask, self._roi_mask(h, w))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        masked = cv2.morphologyEx(masked, cv2.MORPH_CLOSE, kernel)
        
        blur = cv2.GaussianBlur(masked, (5, 5), 1)
        edges = cv2.Canny(blur, self.canny1, self.canny2)
        return edges

    def _fit_lane_poly(self, points: List[Tuple[float, float]]) -> Optional[np.ndarray]:
        """Fit đa thức bậc 2 cho các điểm để tạo đường cong mượt."""
        if len(points) < 3: return None
        xs = np.array([p[0] for p in points], dtype=np.float32)
        ys = np.array([p[1] for p in points], dtype=np.float32)
        try:
            return np.polyfit(ys, xs, 2)
        except (np.linalg.LinAlgError, ValueError):
            return None

    def detect(self, frame_bgr: np.ndarray) -> Tuple[List, List, List, Dict]:
        """Thực thi pipeline nhận diện làn đường, trả về các đường con mượt (smooth)."""
        h, w = frame_bgr.shape[:2]
        edges = self._binary_edges(frame_bgr)

        ys = np.linspace(int(self.heights_frac[0]*h), int(self.heights_frac[1]*h), self.n_heights).astype(int)
        x_center_ref = self.last_good_center if self.last_good_center is not None else (w / 2)

        raw_lefts, raw_rights = [], []

        # Quét từng hàng ngang (Scanlines)
        for y in ys:
            y_idx = int(np.clip(y, 0, h - 1))
            row = edges[y_idx, :]
            
            l_bound = max(0, int(x_center_ref - self.search_margin_px))
            r_bound = min(w - 1, int(x_center_ref + self.search_margin_px))
            
            # Tìm biên trái và phải từ điểm tham chiếu trung tâm
            xL, xR = None, None
            for x in range(int(x_center_ref), l_bound - 1, -1):
                if row[x] != 0: xL = x; break
            for x in range(int(x_center_ref), r_bound + 1, 1):
                if row[x] != 0: xR = x; break

            if xL and xR and (xR - xL) >= self.min_lane_width_px:
                raw_lefts.append((xL, y))
                raw_rights.append((xR, y))
                x_center_ref = 0.5 * (xL + xR)
                self.lane_width_history.append(xR - xL)

        # Cố gắng fit đa thức từ dữ liệu thô
        left_poly = self._fit_lane_poly(raw_lefts) or self.last_left_poly
        right_poly = self._fit_lane_poly(raw_rights) or self.last_right_poly
        virtual_info = {"used_virtual_left": False, "used_virtual_right": False}

        # Virtual Markers: Nội suy làn bị mất
        if self.use_virtual_markers and len(self.lane_width_history) > 0:
            avg_width = np.mean(self.lane_width_history)
            if left_poly is None and right_poly is not None:
                # Mất trái -> Sinh trái từ phải
                left_poly = np.array([right_poly[0], right_poly[1], right_poly[2] - avg_width])
                virtual_info["used_virtual_left"] = True
            elif right_poly is None and left_poly is not None:
                # Mất phải -> Sinh phải từ trái
                right_poly = np.array([left_poly[0], left_poly[1], left_poly[2] + avg_width])
                virtual_info["used_virtual_right"] = True

        self.last_left_poly, self.last_right_poly = left_poly, right_poly

        # TẠO ĐIỂM MƯỢT TỪ ĐA THỨC (Smoothing)
        smooth_lefts, smooth_rights, smooth_centers = [], [], []
        if left_poly is not None and right_poly is not None:
            for y in ys:
                xl = left_poly[0]*y**2 + left_poly[1]*y + left_poly[2]
                xr = right_poly[0]*y**2 + right_poly[1]*y + right_poly[2]
                smooth_lefts.append((xl, float(y)))
                smooth_rights.append((xr, float(y)))
                smooth_centers.append(((xl + xr) / 2, float(y)))

        # Quản lý tracking (tránh mất làn hoàn toàn)
        if len(smooth_centers) > 0:
            self.last_good_center = self.center_ema.update(smooth_centers[0][0])
            self.frames_lost = 0
            lane_quality = min(1.0, len(raw_lefts) / self.n_heights)
        else:
            self.center_ema.update(None)
            self.frames_lost += 1
            lane_quality = 0.0
            if self.frames_lost > self.max_frames_lost:
                self.last_good_center, self.last_left_poly, self.last_right_poly = None, None, None
                self.center_ema.reset()

        debug = {
            "edges": edges, 
            "quality": lane_quality, 
            "frames_lost": self.frames_lost, 
            "virtual_markers": virtual_info
        }
        return smooth_centers, smooth_lefts, smooth_rights, debug

    def compute_steering(self, centers: List[Tuple[float, float]], frame_w: int, k_gain: float = 1.15) -> Tuple[Optional[float], Optional[Tuple[float, float]]]:
        """Tính góc lái (steering) bằng Pure Pursuit với đa thức."""
        if len(centers) < 3: return None, None
        
        xs = np.array([p[0] for p in centers], dtype=np.float32)
        ys = np.array([p[1] for p in centers], dtype=np.float32)
        
        try:
            poly = np.polyfit(ys, xs, 2)
        except np.linalg.LinAlgError:
            return None, None

        y_look = 0.3 * ys.min() + 0.7 * ys.max()  # Lookahead 70%
        x_look = poly[0]*y_look**2 + poly[1]*y_look + poly[2]

        err = np.clip((x_look - (frame_w / 2.0)) / (frame_w / 2.0), -1.0, 1.0)
        return float(np.clip(k_gain * err, -1.0, 1.0)), (float(x_look), float(y_look))

# =========================
# Visualization (Đã tối ưu vẽ nét liền mượt)
# =========================
def draw_debug(frame: np.ndarray, centers: List, lefts: List, rights: List, target_point: Optional[Tuple], debug: Dict) -> np.ndarray:
    out = frame.copy()
    vi = debug.get("virtual_markers", {})

    # Vẽ polygon bao phủ làn đường mượt
    if len(lefts) > 1 and len(rights) > 1:
        poly_points = lefts + list(reversed(rights))
        cv2.fillPoly(out, [np.array(poly_points, dtype=np.int32)], (200, 255, 200)) # Xanh nhạt
        out = cv2.addWeighted(frame, 0.4, out, 0.6, 0)

    # Vẽ 2 đường biên (Nét liền đa thức)
    if len(lefts) > 1:
        cv2.polylines(out, [np.array(lefts, dtype=np.int32).reshape((-1, 1, 2))], False, (0, 0, 255), 4, cv2.LINE_AA)
    if len(rights) > 1:
        cv2.polylines(out, [np.array(rights, dtype=np.int32).reshape((-1, 1, 2))], False, (255, 0, 0), 4, cv2.LINE_AA)
    if len(centers) > 1:
        cv2.polylines(out, [np.array(centers, dtype=np.int32).reshape((-1, 1, 2))], False, (0, 255, 0), 2, cv2.LINE_AA)

    # Info Virtual Markers
    if vi.get("used_virtual_left"):
        cv2.putText(out, "[VIRTUAL LEFT]", (15, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
    if vi.get("used_virtual_right"):
        cv2.putText(out, "[VIRTUAL RIGHT]", (15, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

    # Điểm Target Lookahead
    if target_point:
        cv2.circle(out, (int(target_point[0]), int(target_point[1])), 8, (0, 255, 255), -1)

    # HUD Status
    if debug["frames_lost"] > 5:
        cv2.putText(out, f"⚠ LANE LOST ({debug['frames_lost']}f)", (15, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        
    return out

# =========================
# Main Execution Loop
# =========================
def main():
    cap = cv2.VideoCapture(0)  # Đổi thành đường dẫn video nếu muốn test trên file
    if not cap.isOpened():
        raise RuntimeError("Cannot open camera/video.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    detector = PointsOfOrientationLane()
    fps_counter = deque(maxlen=30)
    last_time = time.time()

    print("[*] Lane Detector Started (Canny + Virtual Markers + Smooth Curves)")
    print("[*] Nhấn 'Q' hoặc 'ESC' để thoát.")

    while True:
        ok, frame = cap.read()
        if not ok: break

        # Xử lý
        centers, lefts, rights, debug = detector.detect(frame)
        steer, target = detector.compute_steering(centers, frame.shape[1])

        # Tính FPS
        now = time.time()
        if (now - last_time) > 0: fps_counter.append(1.0 / (now - last_time))
        last_time = now

        # Hiển thị
        vis = draw_debug(frame, centers, lefts, rights, target, debug)
        
        steer_str = f"{steer:+.3f}" if steer is not None else "LOST"
        fps_str = f"FPS: {np.mean(fps_counter):.1f}" if fps_counter else "FPS: 0"
        cv2.putText(vis, f"{fps_str} | Steer: {steer_str}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        cv2.imshow("Lane Detection (Smooth)", vis)
        cv2.imshow("Canny Edges", debug["edges"])

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q')): break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()