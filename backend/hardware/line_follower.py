#!/usr/bin/env python3
"""
Line-Following Module — Canny-only (no UNet).

The physical map has white tape borders on a gray surface.
The car drives on the gray GAP between the two white borders.

Steering pipeline (2-tier):
  1. TARGET X (primary) — dual-target PID lane centering from AdaptiveTrackerV2
  2. CANNY (secondary) — full pipeline PD + Pure Pursuit when target X low confidence

    correction  < 0  →  car drifted RIGHT  → steer LEFT (right servo pulls)
    correction  > 0  →  car drifted LEFT   → steer RIGHT (left servo pulls)
"""

import asyncio
import logging
import os
import numpy as np

logger = logging.getLogger('line_follower')

# Dead-zone: corrections smaller than this are treated as "centred"
DEAD_ZONE = 0.03

# EMA smoothing factor (0 = full smoothing, 1 = no smoothing)
EMA_ALPHA = 0.35


class LineFollower:
    """
    Analyses a camera frame (JPEG bytes) and returns steering correction.

    Return value of `analyse_frame(jpeg_bytes)`:
        float in [-1.0 … +1.0]
        negative = car drifted RIGHT → steer left
        positive = car drifted LEFT  → steer right
        0.0      = centred (or no lane detected)

    Uses AdaptiveTrackerV2 (canny edge detection) as the sole detection engine.
    No UNet — purely canny-based.
    """

    def __init__(self):
        self._ready = False
        self._cv2 = None

        # EMA state
        self._ema_correction = 0.0

        # Canny detector
        self._canny_detector = None
        self._canny_available = False

        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            logger.error("OpenCV (cv2) not available — line-following disabled")
            return

        self._init_canny_detector()
        self._ready = self._canny_available
        if self._ready:
            logger.info("✅ LineFollower ready (Canny-only)")
        else:
            logger.warning("LineFollower not ready — canny detector failed to load")

    def _init_canny_detector(self):
        """Initialise the canny edge detector (AdaptiveTrackerV2)."""
        try:
            import sys
            canny_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      '..', 'canny-edge-detection-main')
            if canny_path not in sys.path:
                sys.path.insert(0, canny_path)
            from test_1_improved import AdaptiveTrackerV2
            self._canny_detector = AdaptiveTrackerV2(width=640, height=480)
            self._canny_available = True
            logger.info("✅ Canny AdaptiveTrackerV2 loaded for lane steering")
        except Exception as e:
            self._canny_available = False
            logger.warning(f"Canny detector not available: {e}")

    def reset(self):
        """Reset EMA state — call when starting a new navigation segment."""
        self._ema_correction = 0.0
        if self._canny_available and self._canny_detector is not None:
            self._canny_detector._integral = 0.0
            self._canny_detector._prev_cte = 0.0
            self._canny_detector._heading_prev = 0.0
            self._canny_detector.steer_ema.value = None
            self._canny_detector.reset_target_x()

    @property
    def is_ready(self):
        return self._ready

    def analyse_frame(self, jpeg_bytes):
        """
        Analyse a JPEG frame and return steering correction [-1 … +1].

        Decision hierarchy (2-tier):
          1. TARGET X (primary) — dual-target PID lane centering.
          2. CANNY (secondary) — full pipeline PD + Pure Pursuit.

        Returns 0.0 on any error.
        """
        if not self._ready or jpeg_bytes is None:
            return 0.0

        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is None:
                return 0.0

            frame_resized = self._cv2.resize(frame, (640, 480))

            # ── TIER 1: TARGET X = PRIMARY (lane centering) ──
            tx_steer = None
            tx_conf = 0.0
            tx_raw_cte = None
            tx_filt_cte = None
            tx_state = None
            if self._canny_available and self._canny_detector is not None:
                try:
                    tx_steer, tx_conf, tx_left, tx_right, tx_raw_cte, tx_filt_cte, tx_state = \
                        self._canny_detector.get_target_x_steering(frame_resized)
                except Exception as e:
                    logger.debug(f"Target X steering error: {e}")

            # ── TIER 2: CANNY = SECONDARY (full pipeline) ──
            canny_steer = None
            if tx_conf < 1.0:
                if self._canny_available and self._canny_detector is not None:
                    try:
                        steer_val, _ = self._canny_detector.process_frame(frame_resized)
                        canny_steer = float(steer_val)
                    except Exception as e:
                        logger.debug(f"Canny detection error: {e}")

            # ── Decision: tiered fallback ──
            raw = 0.0
            primary_source = 'none'

            if tx_steer is not None and tx_conf >= 1.0:
                raw = tx_steer
                primary_source = 'target_x(2L)'
            elif tx_steer is not None and tx_conf >= 0.5:
                if canny_steer is not None:
                    raw = 0.7 * tx_steer + 0.3 * canny_steer
                    primary_source = 'target_x+canny(1L)'
                else:
                    raw = tx_steer
                    primary_source = 'target_x(1L)'
            elif canny_steer is not None:
                raw = canny_steer
                primary_source = 'canny'

            self._ema_correction = raw

            if abs(raw) > 0.01:
                cte_info = ""
                if tx_raw_cte is not None:
                    cte_info += f" rawCTE={tx_raw_cte:+.3f}"
                if tx_filt_cte is not None:
                    cte_info += f" filtCTE={tx_filt_cte:+.3f}"
                state_labels = {2: '2L', 1: 'L', -1: 'R', 0: '--'}
                cte_info += f" st={state_labels.get(tx_state, '??')}"
                logger.debug(
                    f"Lane: tx={tx_steer and f'{tx_steer:+.3f}'} conf={tx_conf:.1f}{cte_info} "
                    f"canny={'N/A' if canny_steer is None else f'{canny_steer:+.3f}'} "
                    f"→ {self._ema_correction:+.3f} [{primary_source}]"
                )

            return self._ema_correction
        except Exception as e:
            logger.debug(f"analyse_frame error: {e}")
            return 0.0

    def analyse_frame_debug(self, jpeg_bytes):
        """
        Like analyse_frame but also returns debug info + visualization.
        """
        result = {
            'correction': 0.0,
            'raw_correction': 0.0,
            'mask_jpeg': None,
            'confidence': 0.0,
        }
        if not self._ready or jpeg_bytes is None:
            return result

        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is None:
                return result

            frame_resized = self._cv2.resize(frame, (640, 480))

            if self._canny_available and self._canny_detector is not None:
                steer_val, viz = self._canny_detector.process_frame(frame_resized)
                result['correction'] = float(steer_val)
                result['raw_correction'] = float(steer_val)
                result['confidence'] = 1.0

                _, jpeg_buf = self._cv2.imencode(
                    '.jpg', viz, [self._cv2.IMWRITE_JPEG_QUALITY, 85])
                result['mask_jpeg'] = bytes(jpeg_buf)

            return result
        except Exception as e:
            logger.debug(f"analyse_frame_debug error: {e}")
            return result
