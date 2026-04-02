
#!/usr/bin/env python3
"""
Object Detection Service for Delivery Bot — Color Detection
============================================================

  1. Connects to the MJPEG camera stream from the hardware daemon
  2. Connects to the Node.js backend via Socket.IO
  3. Provides start/stop/detect_once/set_target HTTP endpoints
  4. Runs a detection loop that checks mean color of ROI against a target
"""

import asyncio
import json
import logging
import os
import signal
import time

import aiohttp
from aiohttp import web
import cv2
import numpy as np
import socketio

# ── Config from environment ──
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://backend:5000')
CAMERA_MJPEG_URL = os.environ.get('CAMERA_MJPEG_URL', 'http://hardware:8765/stream')
DETECT_PORT = int(os.environ.get('DETECT_PORT', '9002'))
DETECT_INTERVAL_MS = int(os.environ.get('DETECT_INTERVAL_MS', '500'))
CONF_THRESHOLD = float(os.environ.get('CONF_THRESHOLD', '0.5'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger('detect-object')


class ObjectDetector:
    """Color-based object detector using mean pixel value in a ROI."""

    def __init__(self):
        self.ready = False
        self.target = None       # target mean value (grayscale)
        self.threshold = 30      # max allowed difference
        # ROI as fraction of frame: [top%, left%, bottom%, right%]  (0.0–1.0)
        self.roi_frac = [0.25, 0.25, 0.75, 0.75]  # center 50%
        self.ready = True
        logger.info("ObjectDetector: color detector ready (set target to begin matching)")

    def set_target(self, target, threshold=None, roi=None):
        """Set the target mean value, optional threshold and ROI."""
        self.target = float(target)
        if threshold is not None:
            self.threshold = float(threshold)
        if roi is not None and len(roi) == 4:
            self.roi_frac = [float(v) for v in roi]
        logger.info(f"Target set: value={self.target}, threshold={self.threshold}, roi={self.roi_frac}")

    def detect(self, frame_bgr):
        """Run color detection on a BGR frame.
        Returns list of detection dicts compatible with the frontend."""
        if frame_bgr is None or self.target is None:
            return []
        return self._detect_frame(frame_bgr)

    def _detect_frame(self, frame):
        h, w = frame.shape[:2]
        y1 = int(h * self.roi_frac[0])
        x1 = int(w * self.roi_frac[1])
        y2 = int(h * self.roi_frac[2])
        x2 = int(w * self.roi_frac[3])
        roi = frame[y1:y2, x1:x2]

        mean_val = float(np.mean(roi))
        diff = abs(self.target - mean_val)
        matched = diff < self.threshold

        # Build confidence: 1.0 when diff=0, 0.0 when diff>=threshold
        confidence = max(0.0, 1.0 - diff / self.threshold) if self.threshold > 0 else (1.0 if diff == 0 else 0.0)

        if matched:
            return [{
                "class": "color_match",
                "confidence": round(confidence, 3),
                "bbox": [x1, y1, x2, y2],
                "mean": round(mean_val, 1),
                "target": self.target,
                "matched": True,
            }]
        else:
            return [{
                "class": "no_match",
                "confidence": round(confidence, 3),
                "bbox": [x1, y1, x2, y2],
                "mean": round(mean_val, 1),
                "target": self.target,
                "matched": False,
            }]

class DetectionService:
    """
    Main service: HTTP API + Socket.IO client + MJPEG consumer + detection loop.
    """

    def __init__(self):
        self.detector = ObjectDetector()
        self.detecting = False
        self._detect_task = None
        self.sio = socketio.AsyncClient(reconnection=True, reconnection_delay=2)
        self._latest_frame = None
        self._frame_lock = asyncio.Lock()
        self._mjpeg_task = None
        self._backend_task = None

    # ── MJPEG stream consumer ──

    async def _consume_mjpeg(self):
        """Continuously read MJPEG stream and keep latest frame.
        Uses exponential backoff on failures and a read timeout to detect stalls."""
        backoff = 2
        max_backoff = 30
        # Timeout: 10s to connect, 15s read timeout (detects stalled stream)
        timeout = aiohttp.ClientTimeout(connect=10, sock_read=15)

        while True:
            try:
                logger.info(f"MJPEG consumer: connecting to {CAMERA_MJPEG_URL}")
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(CAMERA_MJPEG_URL) as resp:
                        if resp.status != 200:
                            logger.warning(f"MJPEG stream returned HTTP {resp.status}")
                            raise aiohttp.ClientError(f"HTTP {resp.status}")
                        backoff = 2  # reset on successful connect
                        logger.info("MJPEG consumer: connected, reading frames")
                        buffer = b''
                        async for chunk in resp.content.iter_any():
                            buffer += chunk
                            while True:
                                start = buffer.find(b'\xff\xd8')
                                end = buffer.find(b'\xff\xd9', start + 2) if start >= 0 else -1
                                if start < 0 or end < 0:
                                    if start >= 0:
                                        buffer = buffer[start:]
                                    elif len(buffer) > 200000:
                                        buffer = buffer[-10000:]
                                    break
                                jpeg_data = buffer[start:end + 2]
                                buffer = buffer[end + 2:]
                                nparr = np.frombuffer(jpeg_data, np.uint8)
                                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                                if frame is not None:
                                    async with self._frame_lock:
                                        self._latest_frame = frame
                        # Stream ended cleanly (EOF)
                        logger.warning("MJPEG stream ended (EOF)")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"MJPEG consumer error: {e}")
            # Exponential backoff
            logger.info(f"MJPEG consumer: retrying in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, max_backoff)

    async def _get_frame(self):
        async with self._frame_lock:
            return self._latest_frame

    # ── Detection loop ──

    async def _detection_loop(self):
        logger.info(f"Detection loop started (interval={DETECT_INTERVAL_MS}ms)")
        interval = DETECT_INTERVAL_MS / 1000.0

        while self.detecting:
            loop_start = time.time()
            try:
                frame = await self._get_frame()
                if frame is not None:
                    detections = self.detector.detect(frame)
                    if self.sio.connected:
                        await self.sio.emit('object-detect-result', {
                            'detections': detections,
                            'timestamp': time.time(),
                        })
                    matched = [d for d in detections if d.get('matched')]
                    if matched:
                        logger.info(f"Color matched! mean={matched[0]['mean']}")
            except Exception as e:
                logger.error(f"Detection loop error: {e}")

            elapsed = time.time() - loop_start
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)

        logger.info("Detection loop stopped")

    async def _detect_once(self):
        frame = await self._get_frame()
        if frame is None:
            return {'detections': [], 'error': 'No camera frame available'}

        detections = self.detector.detect(frame)
        result = {'detections': detections, 'timestamp': time.time()}

        if self.sio.connected:
            await self.sio.emit('object-detect-result', result)

        return result

    def start_detecting(self):
        if self.detecting:
            return
        if self.detector.target is None:
            logger.warning("Cannot start: target not set. Call /set_target first.")
            if self.sio.connected:
                asyncio.ensure_future(self.sio.emit('object-detect-status', {
                    'detecting': False,
                    'error': 'Target chưa được thiết lập. Hãy set target trước.'
                }))
            return
        self.detecting = True
        self._detect_task = asyncio.ensure_future(self._detection_loop())
        if self.sio.connected:
            asyncio.ensure_future(self.sio.emit('object-detect-status', {'detecting': True}))
        logger.info("Detection started")

    def stop_detecting(self):
        self.detecting = False
        if self._detect_task:
            self._detect_task.cancel()
            self._detect_task = None
        if self.sio.connected:
            asyncio.ensure_future(self.sio.emit('object-detect-status', {'detecting': False}))
        logger.info("Detection stopped")

    # ── Socket.IO connection to backend ──

    async def connect_backend(self):
        @self.sio.event
        async def connect():
            logger.info(f"Connected to backend: {BACKEND_URL}")
            await self.sio.emit('join-room', 'detect-object')

        @self.sio.event
        async def disconnect():
            logger.warning("Disconnected from backend")

        @self.sio.on('object-detect-start-cmd')
        async def on_start(data=None):
            self.start_detecting()

        @self.sio.on('object-detect-stop-cmd')
        async def on_stop(data=None):
            self.stop_detecting()

        @self.sio.on('object-detect-once-cmd')
        async def on_once(data=None):
            await self._detect_once()

        @self.sio.on('object-detect-set-target-cmd')
        async def on_set_target(data=None):
            if data:
                self.detector.set_target(
                    target=data.get('target', 128),
                    threshold=data.get('threshold'),
                    roi=data.get('roi'),
                )
                await self.sio.emit('object-detect-status', {
                    'detecting': self.detecting,
                    'target': self.detector.target,
                    'threshold': self.detector.threshold,
                    'roi': self.detector.roi_frac,
                })

        # Retry initial connection with exponential backoff
        backoff = 2
        max_backoff = 30
        while True:
            try:
                await self.sio.connect(BACKEND_URL)
                logger.info("Socket.IO connected to backend")
                return
            except Exception as e:
                logger.warning(f"Backend connection failed: {e}, retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, max_backoff)

    # ── HTTP API ──

    async def handle_start(self, request):
        self.start_detecting()
        return web.json_response({
            'status': 'started' if self.detecting else 'error',
            'detecting': self.detecting,
        })

    async def handle_stop(self, request):
        self.stop_detecting()
        return web.json_response({'status': 'stopped'})

    async def handle_detect_once(self, request):
        result = await self._detect_once()
        return web.json_response(result)

    async def handle_set_target(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'Invalid JSON'}, status=400)

        target = data.get('target')
        if target is None:
            return web.json_response({'error': 'Missing "target" field'}, status=400)

        self.detector.set_target(
            target=target,
            threshold=data.get('threshold'),
            roi=data.get('roi'),
        )

        if self.sio.connected:
            await self.sio.emit('object-detect-status', {
                'detecting': self.detecting,
                'target': self.detector.target,
                'threshold': self.detector.threshold,
                'roi': self.detector.roi_frac,
            })

        return web.json_response({
            'status': 'ok',
            'target': self.detector.target,
            'threshold': self.detector.threshold,
            'roi': self.detector.roi_frac,
        })

    async def handle_health(self, request):
        return web.json_response({
            'status': 'ok',
            'detecting': self.detecting,
            'model_ready': self.detector.ready,
            'camera_connected': self._latest_frame is not None,
            'target': self.detector.target,
            'threshold': self.detector.threshold,
            'roi': self.detector.roi_frac,
        })

    # ── Main entry ──

    async def run(self):
        # Start MJPEG consumer in background (has its own retry loop)
        self._mjpeg_task = asyncio.ensure_future(self._consume_mjpeg())
        # Connect to backend in background (has its own retry loop)
        self._backend_task = asyncio.ensure_future(self.connect_backend())

        app = web.Application()
        app.router.add_post('/start', self.handle_start)
        app.router.add_post('/stop', self.handle_stop)
        app.router.add_post('/detect_once', self.handle_detect_once)
        app.router.add_post('/set_target', self.handle_set_target)
        app.router.add_get('/health', self.handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', DETECT_PORT)
        await site.start()
        logger.info(f"HTTP API listening on port {DETECT_PORT}")

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            self.stop_detecting()
            if self._mjpeg_task:
                self._mjpeg_task.cancel()
            if self._backend_task:
                self._backend_task.cancel()
            await runner.cleanup()


def main():
    service = DetectionService()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: loop.stop())

    try:
        loop.run_until_complete(service.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == '__main__':
    main()
