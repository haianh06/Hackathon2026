#!/usr/bin/env python3
"""
MJPEG Camera Streaming for Raspberry Pi 5
Connects to rpicam-vid TCP stream running on the HOST and serves
MJPEG over HTTP (multipart/x-mixed-replace) to browser clients.

Architecture:
  HOST: rpicam-vid --codec mjpeg ... --listen -o tcp://0.0.0.0:8554
  CONTAINER: camera_mjpeg.py --> TCP connect to host:8554
                              --> HTTP serve /stream, /snapshot on :8765
"""

import asyncio
import logging
import os

logger = logging.getLogger('camera_mjpeg')

# JPEG markers
_SOI = b'\xff\xd8'
_EOI = b'\xff\xd9'


class MJPEGCamera:
    """Read MJPEG frames from rpicam-vid TCP stream on the host."""

    def __init__(self, host='host.docker.internal', port=8554,
                 width=1280, height=720, fps=15):
        self.host = host
        self.port = port
        self.width = width
        self.height = height
        self.fps = fps
        self._reader = None
        self._writer = None
        self._latest_frame = None
        self._frame_event = asyncio.Event()
        self._running = False
        self._read_task = None
        self._connect_lock = asyncio.Lock()
        self._last_connect_attempt = 0

    async def start(self):
        async with self._connect_lock:
            if self._running:
                return True

            # Prevent rapid reconnection attempts — wait at least 3s between tries
            import time
            now = time.monotonic()
            since_last = now - self._last_connect_attempt
            if since_last < 3.0:
                remaining = 3.0 - since_last
                logger.debug(f"Camera reconnect throttled, waiting {remaining:.1f}s")
                await asyncio.sleep(remaining)
            self._last_connect_attempt = time.monotonic()

            logger.info(f"Connecting to camera stream at {self.host}:{self.port} ...")

            for attempt in range(5):
                try:
                    self._reader, self._writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port),
                        timeout=5
                    )
                    self._running = True
                    self._read_task = asyncio.ensure_future(self._read_frames())
                    logger.info(f"Camera connected to tcp://{self.host}:{self.port}")
                    return True
                except Exception as e:
                    logger.warning(f"Camera connect attempt {attempt+1}/5 failed: {e}")
                    await asyncio.sleep(2)

            logger.error(
                f"Cannot connect to camera stream at {self.host}:{self.port}\n"
                "Make sure rpicam-vid is running on the host:\n"
                f"  rpicam-vid --codec mjpeg -t 0 --nopreview "
                f"--width {self.width} --height {self.height} "
                f"--framerate {self.fps} --listen "
                f"-o tcp://0.0.0.0:{self.port}"
            )
            return False

    async def _read_frames(self):
        buf = b''
        try:
            while self._running:
                chunk = await self._reader.read(262144)
                if not chunk:
                    logger.warning("Camera TCP stream ended (EOF)")
                    break
                buf += chunk

                while True:
                    soi = buf.find(_SOI)
                    if soi == -1:
                        buf = b''
                        break
                    eoi = buf.find(_EOI, soi + 2)
                    if eoi == -1:
                        buf = buf[soi:]
                        break
                    frame = buf[soi:eoi + 2]
                    buf = buf[eoi + 2:]
                    self._latest_frame = frame
                    self._frame_event.set()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Frame reader error: {e}")
        finally:
            self._running = False
            logger.warning("Camera frame reader stopped - will retry on next request")

    async def _ensure_connected(self):
        if self._running:
            return
        # Lock is inside start(), so concurrent callers will wait
        # rather than spawning multiple parallel reconnects
        self._latest_frame = None
        self._frame_event.clear()
        await self.start()

    async def get_frame(self):
        await self._ensure_connected()
        if self._latest_frame is None:
            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                return None
        return self._latest_frame

    async def stop(self):
        self._running = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        self._latest_frame = None
        self._frame_event.clear()
        logger.info("Camera stopped")

    @property
    def is_running(self):
        return self._running


class CameraManager:
    """
    MJPEG camera manager.
    HTTP routes:
      GET /stream        -> multipart MJPEG (use in <img> tag)
      GET /snapshot       -> single JPEG
      GET /camera/status  -> JSON
    """

    def __init__(self, config=None):
        config = config or {}
        cam_cfg = config.get('camera', {})
        self.width = cam_cfg.get('width', 1280)
        self.height = cam_cfg.get('height', 720)
        self.fps = cam_cfg.get('fps', 15)

        cam_host = os.environ.get('CAMERA_HOST', 'host.docker.internal')
        cam_port = int(os.environ.get('CAMERA_PORT', '8554'))

        self._camera = MJPEGCamera(
            host=cam_host, port=cam_port,
            width=self.width, height=self.height, fps=self.fps,
        )

    async def handle_stream(self, request):
        from aiohttp import web
        response = web.StreamResponse(
            status=200,
            headers={
                'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
            },
        )
        await response.prepare(request)

        boundary = b'--frame'
        try:
            while True:
                frame = await self._camera.get_frame()
                if frame is None:
                    await asyncio.sleep(0.2)
                    continue
                await response.write(
                    boundary + b'\r\n'
                    b'Content-Type: image/jpeg\r\n'
                    b'Content-Length: ' + str(len(frame)).encode() + b'\r\n'
                    b'\r\n' +
                    frame + b'\r\n'
                )
                await asyncio.sleep(1.0 / self.fps)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def handle_snapshot(self, request):
        from aiohttp import web
        frame = await self._camera.get_frame()
        if frame is None:
            return web.json_response({'error': 'No frame available'}, status=503)
        return web.Response(
            body=frame,
            content_type='image/jpeg',
            headers={'Cache-Control': 'no-cache'},
        )

    async def handle_status(self, request):
        from aiohttp import web
        return web.json_response(self.get_status())

    # ---- lifecycle ----
    async def cleanup(self):
        await self._camera.stop()

    async def get_latest_frame(self):
        """Return the most recent JPEG frame bytes, or None."""
        return await self._camera.get_frame()

    def get_status(self):
        return {
            'streaming': 'mjpeg',
            'camera_active': self._camera.is_running,
            'source': f'tcp://{self._camera.host}:{self._camera.port}',
            'resolution': f'{self.width}x{self.height}',
            'fps': self.fps,
        }

    def add_routes(self, app, cors=None):
        stream_res = app.router.add_resource('/stream')
        stream_res.add_route('GET', self.handle_stream)
        snap_res = app.router.add_resource('/snapshot')
        snap_res.add_route('GET', self.handle_snapshot)
        status_res = app.router.add_resource('/camera/status')
        status_res.add_route('GET', self.handle_status)
        if cors:
            cors.add(stream_res)
            cors.add(snap_res)
            cors.add(status_res)
