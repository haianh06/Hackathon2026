#!/usr/bin/env python3
"""
Road Sign Detector Module for Raspberry Pi 5 Delivery Bot
Uses YOLOv5 (ultralytics) to detect traffic signs from camera frames.

Model: best_trans.pt (pre-trained on traffic sign dataset)
"""

import logging
import os
import time
import cv2
import numpy as np

logger = logging.getLogger('road_sign_detector')

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'detect-road-sign', 'best_trans.pt'
)

# Try to import torch + ultralytics YOLO
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
    logger.info("ultralytics YOLO imported successfully")
except ImportError:
    YOLO_AVAILABLE = False
    logger.warning("ultralytics not available — road sign detection disabled")


class RoadSignDetector:
    """
    Detect traffic/road signs in camera frames using a YOLO model.

    Usage:
        detector = RoadSignDetector()
        if detector.is_ready:
            detections = detector.detect(jpeg_bytes)
            # detections = [{'class': 'stop', 'confidence': 0.92, 'bbox': [x1,y1,x2,y2]}, ...]
    """

    def __init__(self, model_path=None, conf_threshold=0.5):
        self._model = None
        self._conf = conf_threshold
        self._ready = False

        path = model_path or MODEL_PATH
        if not YOLO_AVAILABLE:
            logger.warning("RoadSignDetector: ultralytics not installed")
            return
        if not os.path.isfile(path):
            logger.warning(f"RoadSignDetector: model not found at {path}")
            return

        try:
            self._model = YOLO(path)
            self._ready = True
            logger.info(f"RoadSignDetector loaded model: {path}")
        except Exception as e:
            logger.error(f"RoadSignDetector: failed to load model: {e}")

    @property
    def is_ready(self):
        return self._ready

    def detect(self, frame_bytes):
        """
        Run detection on a JPEG frame.

        Args:
            frame_bytes: raw JPEG bytes from camera

        Returns:
            list of dicts: [{'class': str, 'confidence': float,
                             'bbox': [x1, y1, x2, y2]}]
        """
        if not self._ready or frame_bytes is None:
            return []

        try:
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                return []

            results = self._model(frame, conf=self._conf, verbose=False)
            detections = []
            for r in results:
                boxes = r.boxes
                if boxes is None:
                    continue
                for i in range(len(boxes)):
                    cls_id = int(boxes.cls[i])
                    conf = float(boxes.conf[i])
                    x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                    cls_name = r.names.get(cls_id, str(cls_id))
                    detections.append({
                        'class': cls_name,
                        'confidence': round(conf, 3),
                        'bbox': [round(x1), round(y1), round(x2), round(y2)],
                    })
            return detections

        except Exception as e:
            logger.error(f"RoadSignDetector.detect error: {e}")
            return []

    def detect_annotated(self, frame_bytes):
        """
        Run detection and return both detections list and annotated JPEG bytes.

        Returns:
            (detections_list, annotated_jpeg_bytes or None)
        """
        if not self._ready or frame_bytes is None:
            return [], None

        try:
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                return [], None

            results = self._model(frame, conf=self._conf, verbose=False)
            detections = []
            for r in results:
                boxes = r.boxes
                if boxes is None:
                    continue
                for i in range(len(boxes)):
                    cls_id = int(boxes.cls[i])
                    conf = float(boxes.conf[i])
                    x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                    cls_name = r.names.get(cls_id, str(cls_id))
                    detections.append({
                        'class': cls_name,
                        'confidence': round(conf, 3),
                        'bbox': [round(x1), round(y1), round(x2), round(y2)],
                    })

                    # Draw on frame
                    color = (0, 255, 0)
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                    label = f"{cls_name} {conf:.0%}"
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    cv2.rectangle(frame, (int(x1), int(y1) - th - 6),
                                  (int(x1) + tw + 4, int(y1)), color, -1)
                    cv2.putText(frame, label, (int(x1) + 2, int(y1) - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return detections, jpeg.tobytes()

        except Exception as e:
            logger.error(f"RoadSignDetector.detect_annotated error: {e}")
            return [], None
