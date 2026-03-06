#!/usr/bin/env python3
"""
Hardware Daemon for Raspberry Pi 5 Delivery Bot
Runs as a standalone process, connects to Node.js backend via Socket.IO
Handles: Motor control, Camera WebRTC, Hardware status
"""

import asyncio
import json
import os
import sys
import signal
import logging
import numpy as np

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger('hardware_daemon')

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
try:
    with open(CONFIG_PATH, 'r') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    logger.warning("config.json not found, using defaults")
    CONFIG = {}

# Import hardware modules
from motor_control import create_motor_controller, AutoNavigator
from camera_mjpeg import CameraManager
from line_follower import LineFollower

# Import canny edge detection
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'canny-edge-detection-main'))
try:
    from test_1_improved import PointsOfOrientationLane as CannyLaneDetector, draw_debug as canny_draw_debug
    CANNY_AVAILABLE = True
    logger.info("Canny edge detection loaded successfully")
except ImportError as e:
    CANNY_AVAILABLE = False
    logger.warning(f"Canny edge detection not available: {e}")

# Socket.IO client
try:
    import socketio
    SIO_AVAILABLE = True
except ImportError:
    SIO_AVAILABLE = False
    logger.error("python-socketio not installed: pip install python-socketio[asyncio_client] aiohttp")

# HTTP server for WebRTC signaling fallback
try:
    from aiohttp import web
    import aiohttp_cors
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class HardwareDaemon:
    def __init__(self):
        self.motor = create_motor_controller(CONFIG)
        self.camera = CameraManager(CONFIG)
        self.line_follower = LineFollower()

        # Wire AutoNavigator with camera + line-follower
        async def _cam_getter():
            return await self.camera.get_latest_frame()

        self.navigator = AutoNavigator(
            self.motor,
            camera_getter=_cam_getter,
            line_follower=self.line_follower,
        )
        self.running = False
        self.sio = None
        self.server_url = os.environ.get('SERVER_URL', 'http://localhost:5000')

        # Map builder state
        self._map_building = False
        self._map_x = 0  # grid coordinate x
        self._map_y = 0  # grid coordinate y
        self._map_direction = 0  # 0=up(+y), 1=right(+x), 2=down(-y), 3=left(-x)
        self._map_step_count = 0
        self._canny_detector = CannyLaneDetector(
            n_heights=10, roi_top_frac=0.55,
            heights_frac=(0.92, 0.60),
            canny1=50, canny2=150,
            search_margin_px=220, min_lane_width_px=30,
            ema_center_alpha=0.35, max_frames_lost=30,
            use_virtual_markers=True
        ) if CANNY_AVAILABLE else None

        # ── Drift correction state ──
        self._drift_history = []       # list of (steer, duration) tuples
        self._drift_bias = 0.0         # accumulated servo bias offset
        self._drift_ema = 0.0          # EMA of steering corrections
        self._drift_alpha = 0.15       # EMA alpha for drift tracking

    async def connect_to_server(self):
        """Connect to Node.js backend via Socket.IO"""
        if not SIO_AVAILABLE:
            logger.error("Cannot connect: python-socketio not installed")
            return

        self.sio = socketio.AsyncClient(reconnection=True, reconnection_delay=2)

        @self.sio.event
        async def connect():
            logger.info(f"Connected to server: {self.server_url}")
            await self.sio.emit('join-room', 'hardware')
            # Report hardware status
            await self.report_status()

        @self.sio.event
        async def disconnect():
            logger.warning("Disconnected from server")

        # Motor control commands from server
        @self.sio.on('motor-command')
        async def on_motor_command(data):
            command = data.get('command', 'stop')
            speed = data.get('speed', 50)
            logger.info(f"Motor command: {command} speed={speed}")

            if command == 'forward':
                self.motor.forward(speed)
            elif command == 'backward':
                self.motor.backward(speed)
            elif command == 'left':
                self.motor.turn_left(speed)
            elif command == 'right':
                self.motor.turn_right(speed)
            elif command == 'stop':
                self.motor.stop()

            await self.sio.emit('motor-status', self.motor.get_status())

        # Navigate to a waypoint (follow path) – legacy simple mode
        @self.sio.on('navigate-to')
        async def on_navigate(data):
            path = data.get('path', [])
            logger.info(f"Navigate path (legacy): {[p.get('pointId', p) for p in path]}")
            for i, point in enumerate(path):
                point_id = point.get('pointId', point) if isinstance(point, dict) else point
                await self.sio.emit('vehicle-position-update', {'pointId': point_id})
                logger.info(f"Reached waypoint: {point_id}")
                if i < len(path) - 1:
                    self.motor.forward(50)
                    await asyncio.sleep(2)
                    self.motor.stop()
                    await asyncio.sleep(0.5)
            self.motor.stop()
            await self.sio.emit('navigation-complete', {'destination': path[-1] if path else None})

        # ── Auto-navigate: servo-controlled path following with real-time logs ──
        @self.sio.on('auto-navigate')
        async def on_auto_navigate(data):
            path = data.get('path', [])
            logger.info(f"🚀 Auto-navigate requested: {[p.get('pointId', '?') for p in path]}")
            if len(path) < 2:
                logger.warning("Auto-navigate: path too short")
                return

            async def emit_cb(event, payload):
                """Forward events to backend server."""
                if self.sio and self.sio.connected:
                    await self.sio.emit(event, payload)

            # Run navigation in background so we can still receive stop commands
            asyncio.ensure_future(self.navigator.navigate_path(path, emit_cb))

        # ── Stop navigation ──
        @self.sio.on('stop-navigation')
        async def on_stop_navigation(data=None):
            logger.info("⏹ Stop navigation command received")
            self.navigator.stop_navigation()
            await self.sio.emit('navigation-log', {
                'type': 'cancelled',
                'timestamp': __import__('time').time(),
            })

        @self.sio.on('hardware-status-request')
        async def on_status_request(data):
            await self.report_status()

        # ====== Map Builder Mode ======
        @self.sio.on('map-build-step')
        async def on_map_build_step(data=None):
            """Move one step forward using canny edge centering."""
            logger.info("🗺 Map-build: step forward")
            asyncio.ensure_future(self._map_build_step_forward(data or {}))

        @self.sio.on('map-build-turn')
        async def on_map_build_turn(data):
            """Turn left or right at intersection."""
            direction = data.get('direction', 'left')
            logger.info(f"🗺 Map-build: turn {direction}")
            asyncio.ensure_future(self._map_build_turn(direction))

        @self.sio.on('map-build-stop')
        async def on_map_build_stop(data=None):
            """Stop map-build movement."""
            self.motor.stop()
            self._map_building = False
            logger.info("🗺 Map-build: stopped")

        @self.sio.on('map-build-analyse')
        async def on_map_build_analyse(data=None):
            """Analyse current frame with canny and return results."""
            asyncio.ensure_future(self._map_build_analyse())

        try:
            await self.sio.connect(self.server_url)
        except Exception as e:
            logger.error(f"Failed to connect to server: {e}")

    async def report_status(self):
        """Report full hardware status to server"""
        status = {
            'motor': self.motor.get_status(),
            'camera': self.camera.get_status(),
            'platform': 'Raspberry Pi 5',
            'gpio_available': True,
            'line_follower': self.line_follower.is_ready,
            'canny_available': CANNY_AVAILABLE,
        }
        if self.sio and self.sio.connected:
            await self.sio.emit('hardware-status', status)
        logger.info(f"Hardware status: {json.dumps(status)}")

    # ====== Map Builder Methods ======
    def _direction_delta(self):
        """Return (dx, dy) for current direction. 0=up, 1=right, 2=down, 3=left."""
        return [(0, 1), (1, 0), (0, -1), (-1, 0)][self._map_direction % 4]

    def _direction_name(self):
        return ['up (+Y)', 'right (+X)', 'down (-Y)', 'left (-X)'][self._map_direction % 4]

    async def _map_build_step_forward(self, data):
        """Move one step forward with combined canny + UNet lane centering + drift correction."""
        import time as _time

        try:
            # Get a frame and analyse with BOTH canny and UNet
            canny_analysis = await self._do_canny_analysis()
            unet_correction = await self._get_unet_correction()

            step_duration = data.get('duration', 0.6)
            speed = data.get('speed', 40)

            # ── Primary: Canny steering (edge-based, geometrically precise) ──
            canny_steer = canny_analysis.get('steering', 0.0) if canny_analysis else 0.0
            canny_quality = canny_analysis.get('laneQuality', 0) if canny_analysis else 0
            unet_steer = unet_correction  # already in [-1, 1]

            # ── Canny-primary, UNet-verification fusion ──
            # Canny provides precise geometric centering; UNet validates lane presence
            if canny_steer is not None and canny_quality > 0.3:
                # High-quality canny: use canny as primary
                if abs(unet_steer) > 0.01:
                    # Check agreement: if UNet & Canny agree on direction, trust more
                    direction_agree = (canny_steer * unet_steer) >= 0  # same sign
                    if direction_agree:
                        # Both agree → use canny (more precise), UNet confirms
                        combined_steer = 0.75 * canny_steer + 0.25 * unet_steer
                    else:
                        # Disagree → reduce confidence, use weighted average
                        combined_steer = 0.50 * canny_steer + 0.50 * unet_steer
                else:
                    combined_steer = canny_steer
            elif abs(unet_steer) > 0.01:
                # Canny weak, UNet available → fallback to UNet
                combined_steer = unet_steer
            else:
                combined_steer = 0.0

            combined_steer = max(-1.0, min(1.0, combined_steer))

            # ── Drift correction: compensate systematic servo bias ──
            # Track steering history to detect consistent drift
            self._drift_ema = self._drift_alpha * combined_steer + (1 - self._drift_alpha) * self._drift_ema

            # If we consistently steer in one direction, there's a mechanical drift
            # Accumulate bias slowly to counteract it
            if abs(self._drift_ema) > 0.08 and len(self._drift_history) >= 3:
                # Compute drift angle from recent steering history
                recent_steers = [s for s, _ in self._drift_history[-10:]]
                drift_mean = np.mean(recent_steers)
                drift_std = np.std(recent_steers) if len(recent_steers) > 1 else 1.0

                # Only apply bias if drift is consistent (low std deviation)
                if drift_std < 0.3 and abs(drift_mean) > 0.05:
                    # Compute drift angle in degrees: arctan(drift_mean) * 180/pi
                    drift_angle_deg = float(np.degrees(np.arctan(drift_mean)))
                    # Apply counter-bias (opposite direction, scaled conservatively)
                    self._drift_bias = -0.3 * drift_mean  # 30% counter-correction
                    logger.info(
                        f"DRIFT CORRECTION: angle={drift_angle_deg:+.1f}° "
                        f"mean={drift_mean:+.3f} std={drift_std:.3f} bias={self._drift_bias:+.3f}"
                    )

            # Apply drift bias to combined steering
            corrected_steer = combined_steer + self._drift_bias
            corrected_steer = max(-1.0, min(1.0, corrected_steer))

            # Record for drift tracking
            self._drift_history.append((combined_steer, step_duration))
            if len(self._drift_history) > 50:
                self._drift_history = self._drift_history[-30:]

            # Log detailed info
            virtual_info = ""
            if canny_analysis:
                if canny_analysis.get('virtualLeft'):
                    virtual_info += " [VIRTUAL-L]"
                if canny_analysis.get('virtualRight'):
                    virtual_info += " [VIRTUAL-R]"
            logger.info(
                f"MAP-STEP: canny={canny_steer:+.3f} unet={unet_steer:+.3f} "
                f"combined={combined_steer:+.3f} drift_bias={self._drift_bias:+.3f} "
                f"final={corrected_steer:+.3f} quality={canny_quality:.0%}"
                f"{virtual_info}"
            )

            # Apply differential steering while moving forward
            if abs(corrected_steer) > 0.05:
                self.motor.forward_steer(corrected_steer)
            else:
                self.motor.forward(speed)

            await asyncio.sleep(step_duration)
            self.motor.stop()

            # Update grid coordinates
            dx, dy = self._direction_delta()
            self._map_x += dx
            self._map_y += dy
            self._map_step_count += 1

            # Report position with detailed info
            if self.sio and self.sio.connected:
                await self.sio.emit('map-build-position', {
                    'x': self._map_x,
                    'y': self._map_y,
                    'direction': self._map_direction,
                    'directionName': self._direction_name(),
                    'stepCount': self._map_step_count,
                    'steering': corrected_steer,
                    'cannySteering': canny_steer,
                    'unetSteering': unet_steer,
                    'laneQuality': canny_quality,
                    'virtualLeft': canny_analysis.get('virtualLeft', False) if canny_analysis else False,
                    'virtualRight': canny_analysis.get('virtualRight', False) if canny_analysis else False,
                    'centersCount': canny_analysis.get('centersCount', 0) if canny_analysis else 0,
                    'driftBias': self._drift_bias,
                    'driftEma': self._drift_ema,
                    'timestamp': _time.time(),
                })

        except Exception as e:
            logger.error(f"Map-build step error: {e}")
            self.motor.stop()

    async def _map_build_turn(self, direction):
        """Turn 90 degrees left or right, update direction."""
        import time as _time

        try:
            turn_duration = 1.6  # Doubled for full 90-degree rotation
            speed = 40

            if direction == 'left':
                self.motor.turn_left(speed)
                self._map_direction = (self._map_direction - 1) % 4
            else:
                self.motor.turn_right(speed)
                self._map_direction = (self._map_direction + 1) % 4

            await asyncio.sleep(turn_duration)
            self.motor.stop()

            if self.sio and self.sio.connected:
                await self.sio.emit('map-build-position', {
                    'x': self._map_x,
                    'y': self._map_y,
                    'direction': self._map_direction,
                    'directionName': self._direction_name(),
                    'stepCount': self._map_step_count,
                    'turned': direction,
                    'timestamp': _time.time(),
                })

        except Exception as e:
            logger.error(f"Map-build turn error: {e}")
            self.motor.stop()

    async def _do_canny_analysis(self):
        """Run canny edge detection on current frame."""
        if not CANNY_AVAILABLE or self._canny_detector is None:
            return None

        frame_bytes = await self.camera.get_latest_frame()
        if frame_bytes is None:
            return None

        try:
            import cv2
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                return None

            h, w = frame.shape[:2]
            centers, lefts, rights, debug = self._canny_detector.detect(frame)
            steer, target = self._canny_detector.compute_steering(centers, w, k_gain=1.0)

            midpoint = None
            if len(centers) > 0:
                nearest = max(centers, key=lambda p: p[1])
                midpoint = {'x': float(nearest[0]), 'y': float(nearest[1])}

            virtual = debug.get('virtual_markers', {})

            return {
                'steering': float(steer) if steer is not None else 0.0,
                'laneQuality': float(debug.get('quality', 0)),
                'centersCount': len(centers),
                'leftsCount': len(lefts),
                'rightsCount': len(rights),
                'midpoint': midpoint,
                'frameWidth': w,
                'frameHeight': h,
                'framesLost': debug.get('frames_lost', 0),
                'virtualLeft': virtual.get('used_virtual_left', False),
                'virtualRight': virtual.get('used_virtual_right', False),
            }
        except Exception as e:
            logger.error(f"Canny analysis error: {e}")
            return None

    async def _get_unet_correction(self):
        """Get steering correction from UNet line follower model."""
        if self.line_follower is None or not self.line_follower.is_ready:
            return 0.0
        try:
            frame_bytes = await self.camera.get_latest_frame()
            if frame_bytes is None:
                return 0.0
            return self.line_follower.analyse_frame(frame_bytes)
        except Exception as e:
            logger.debug(f"UNet correction error: {e}")
            return 0.0

    async def _map_build_analyse(self):
        """Analyse and send result to frontend."""
        import time as _time
        analysis = await self._do_canny_analysis()
        if self.sio and self.sio.connected:
            await self.sio.emit('map-build-analysis', {
                'analysis': analysis,
                'position': {
                    'x': self._map_x,
                    'y': self._map_y,
                    'direction': self._map_direction,
                    'directionName': self._direction_name(),
                },
                'timestamp': _time.time(),
            })

    async def start_http_server(self):
        """HTTP API for MJPEG camera stream + hardware control"""
        if not AIOHTTP_AVAILABLE:
            return

        app = web.Application()
        cors = aiohttp_cors.setup(app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        })

        async def handle_status(request):
            return web.json_response({
                'motor': self.motor.get_status(),
                'camera': self.camera.get_status()
            })

        async def handle_motor(request):
            data = await request.json()
            command = data.get('command', 'stop')
            speed = data.get('speed', 50)
            getattr(self.motor, command, self.motor.stop)(speed) if command != 'stop' else self.motor.stop()
            return web.json_response(self.motor.get_status())

        # Register MJPEG camera routes (/stream, /snapshot, /camera/status)
        self.camera.add_routes(app, cors)

        resource_status = cors.add(app.router.add_resource('/hardware/status'))
        cors.add(resource_status.add_route('GET', handle_status))
        resource_motor = cors.add(app.router.add_resource('/hardware/motor'))
        cors.add(resource_motor.add_route('POST', handle_motor))

        # ── Dev / debug endpoints for line-following ──

        async def handle_lane_debug_stream(request):
            """MJPEG stream with lane-detection overlay for dev page."""
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                    'Cache-Control': 'no-cache',
                },
            )
            await response.prepare(request)
            boundary = b'--frame'
            try:
                while True:
                    frame_bytes = await self.camera.get_latest_frame()
                    if frame_bytes and self.line_follower.is_ready:
                        debug = self.line_follower.analyse_frame_debug(frame_bytes)
                        jpeg = debug.get('mask_jpeg') or frame_bytes
                    else:
                        jpeg = frame_bytes
                    if jpeg:
                        await response.write(
                            boundary + b'\r\n'
                            b'Content-Type: image/jpeg\r\n'
                            b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n'
                            b'\r\n' + jpeg + b'\r\n'
                        )
                    await asyncio.sleep(0.15)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return response

        async def handle_lane_debug_json(request):
            """Single-shot JSON with lane correction + debug data."""
            frame_bytes = await self.camera.get_latest_frame()
            if not frame_bytes or not self.line_follower.is_ready:
                return web.json_response({'ready': False, 'correction': 0.0})
            debug = self.line_follower.analyse_frame_debug(frame_bytes)
            return web.json_response({
                'ready': True,
                'correction': debug['correction'],
                'raw_correction': debug.get('raw_correction', 0.0),
                'lane_cx': debug.get('lane_cx'),
                'frame_cx': debug.get('frame_cx'),
                'left_edge': debug.get('left_edge'),
                'right_edge': debug.get('right_edge'),
                'gap_center': debug.get('gap_center'),
                'lane_width': debug.get('lane_width'),
                'confidence': debug.get('confidence', 0.0),
                'borders_found': debug.get('borders_found', 0),
                'ema_lane_width': debug.get('ema_lane_width'),
                'roi_top': debug.get('roi_top'),
            })

        r_lane_stream = cors.add(app.router.add_resource('/lane/stream'))
        cors.add(r_lane_stream.add_route('GET', handle_lane_debug_stream))
        r_lane_json = cors.add(app.router.add_resource('/lane/debug'))
        cors.add(r_lane_json.add_route('GET', handle_lane_debug_json))

        # ── Canny edge detection lane overlay stream ──
        async def handle_canny_stream(request):
            """MJPEG stream with canny lane-detection overlay for Map Builder."""
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                    'Cache-Control': 'no-cache',
                },
            )
            await response.prepare(request)
            boundary = b'--frame'
            try:
                while True:
                    frame_bytes = await self.camera.get_latest_frame()
                    if frame_bytes and CANNY_AVAILABLE and self._canny_detector:
                        try:
                            import cv2
                            nparr = np.frombuffer(frame_bytes, np.uint8)
                            frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            if frame_bgr is not None:
                                h, w = frame_bgr.shape[:2]
                                centers, lefts, rights, debug_info = self._canny_detector.detect(frame_bgr)
                                steer, target = self._canny_detector.compute_steering(centers, w, k_gain=1.0)
                                vis = canny_draw_debug(
                                    frame_bgr, centers, lefts, rights, target, debug_info
                                )
                                # Add position overlay for map builder
                                pos_text = f"Pos: ({self._map_x},{self._map_y}) Dir: {self._direction_name()}"
                                steer_text = f"Steer: {steer:+.3f}" if steer is not None else "Steer: LOST"
                                cv2.putText(vis, pos_text, (15, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                                cv2.putText(vis, steer_text, (15, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                                _, jpeg = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 75])
                                frame_bytes = jpeg.tobytes()
                        except Exception as e:
                            logger.debug(f"Canny overlay error: {e}")
                    if frame_bytes:
                        await response.write(
                            boundary + b'\r\n'
                            b'Content-Type: image/jpeg\r\n'
                            b'Content-Length: ' + str(len(frame_bytes)).encode() + b'\r\n'
                            b'\r\n' + frame_bytes + b'\r\n'
                        )
                    await asyncio.sleep(0.12)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return response

        async def handle_canny_snapshot(request):
            """Single snapshot with canny overlay."""
            frame_bytes = await self.camera.get_latest_frame()
            if not frame_bytes:
                return web.Response(status=503, text='No frame')
            if CANNY_AVAILABLE and self._canny_detector:
                try:
                    import cv2
                    nparr = np.frombuffer(frame_bytes, np.uint8)
                    frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame_bgr is not None:
                        h, w = frame_bgr.shape[:2]
                        centers, lefts, rights, debug_info = self._canny_detector.detect(frame_bgr)
                        steer, target = self._canny_detector.compute_steering(centers, w, k_gain=1.0)
                        vis = canny_draw_debug(
                            frame_bgr, centers, lefts, rights, target, debug_info
                        )
                        _, jpeg = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        frame_bytes = jpeg.tobytes()
                except Exception as e:
                    logger.debug(f"Canny snapshot error: {e}")
            return web.Response(body=frame_bytes, content_type='image/jpeg')

        r_canny_stream = cors.add(app.router.add_resource('/canny/stream'))
        cors.add(r_canny_stream.add_route('GET', handle_canny_stream))
        r_canny_snap = cors.add(app.router.add_resource('/canny/snapshot'))
        cors.add(r_canny_snap.add_route('GET', handle_canny_snapshot))

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8765)
        await site.start()
        logger.info("Hardware HTTP API running on :8765")

    async def run(self):
        """Main run loop"""
        self.running = True
        logger.info("Hardware Daemon starting...")

        # Connect to backend server
        await self.connect_to_server()

        # Start HTTP API (backup)
        await self.start_http_server()

        logger.info("Hardware Daemon running. Press Ctrl+C to stop.")

        # Keep running
        try:
            while self.running:
                await asyncio.sleep(1)
                # Periodic status report
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down Hardware Daemon...")
        self.running = False
        self.motor.cleanup()
        await self.camera.cleanup()
        if self.sio and self.sio.connected:
            await self.sio.disconnect()
        logger.info("Hardware Daemon stopped.")


async def main():
    daemon = HardwareDaemon()

    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.shutdown()))

    await daemon.run()


if __name__ == '__main__':
    print("""
╔══════════════════════════════════════════╗
║   🤖 Pi Delivery Bot - Hardware Daemon   ║
║   Platform: Raspberry Pi 5               ║
║   Camera: IMX219 (MJPEG HTTP)            ║
║   Motor: 2x Servo (lgpio PWM GPIO12,13)  ║
╚══════════════════════════════════════════╝
    """)
    asyncio.run(main())
