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

# RFID reader (custom spidev + lgpio driver for Pi 5)
try:
    from rfid_reader import MFRC522Reader
    RFID_AVAILABLE = True
    logger.info("MFRC522 RFID reader module loaded (spidev+lgpio)")
except ImportError as e:
    RFID_AVAILABLE = False
    logger.warning(f"RFID reader not available: {e}")

# Import canny edge detection
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'canny-edge-detection-main'))
try:
    from lane_dynamic_center import DynamicLaneTracker
    CANNY_AVAILABLE = True
    logger.info("Dynamic lane tracker loaded successfully")
except ImportError as e:
    CANNY_AVAILABLE = False
    logger.warning(f"Dynamic lane tracker not available: {e}")

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
        self._lane_tracker = DynamicLaneTracker(width=640, height=480) if CANNY_AVAILABLE else None

        # ── RFID scanner state ──
        self._rfid_scanning = False
        self._rfid_reader = None
        if RFID_AVAILABLE:
            rfid_cfg = CONFIG.get('rfid', {})
            try:
                self._rfid_reader = MFRC522Reader(
                    bus=rfid_cfg.get('bus', 0),
                    device=rfid_cfg.get('device', 0),
                    rst_pin=rfid_cfg.get('rst_pin', 25),
                    gpio_chip=rfid_cfg.get('gpio_chip', 0),
                )
                logger.info("RFID reader initialized successfully")
            except Exception as e:
                logger.warning(f"RFID reader init failed: {e}")

        # ── Drift correction state ──
        self._drift_history = []       # list of (steer, duration) tuples
        self._drift_bias = 0.0         # accumulated servo bias offset
        self._drift_ema = 0.0          # EMA of steering corrections
        self._drift_alpha = 0.15       # EMA alpha for drift tracking

        # ── Continuous servo control state ──
        self._servo_steer_ema = 0.0    # smoothed steer for servo
        self._prev_steer = 0.0         # previous steer for derivative
        self._steer_integral = 0.0     # integral term for PID

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

        # ====== RFID Scan Commands ======
        @self.sio.on('rfid-start-scan')
        async def on_rfid_start_scan(data=None):
            logger.info("RFID: start scan requested")
            asyncio.ensure_future(self._rfid_scan_loop())

        @self.sio.on('rfid-stop-scan')
        async def on_rfid_stop_scan(data=None):
            logger.info("RFID: stop scan requested")
            self._rfid_scanning = False

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
            'rfid_available': RFID_AVAILABLE and self._rfid_reader is not None,
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
        """
        Move one step forward with CONTINUOUS servo adjustment.

        Instead of: analyse once → steer → drive blind for N seconds → stop,
        this uses a real-time PID loop:
          1. Start driving forward
          2. Every ~80ms: grab frame → BEV → detect → compute steer → adjust motor
          3. Stop after step_duration

        PID gains tuned for continuous-rotation servo differential drive.
        """
        import time as _time
        import cv2

        # Configurable parameters
        step_duration = data.get('duration', 0.8)
        speed = data.get('speed', 40)
        LOOP_INTERVAL = 0.08          # 80ms → ~12.5 Hz servo update rate
        KP = 0.70                     # Proportional gain
        KD = 0.25                     # Derivative gain (dampen oscillation)
        KI = 0.05                     # Integral gain (correct persistent offset)
        STEER_EMA_ALPHA = 0.45        # Smooth steer output to avoid servo jitter
        DEAD_ZONE = 0.04              # Ignore tiny corrections
        MAX_INTEGRAL = 0.3            # Clamp integral windup
        QUALITY_MIN = 0.15            # Minimum quality to trust canny

        try:
            # Reset PID state for this step
            self._steer_integral = 0.0

            # Start driving
            self.motor.forward(speed)
            start_time = _time.time()
            last_canny_steer = 0.0
            last_quality = 0.0
            frames_ok = 0

            while (_time.time() - start_time) < step_duration:
                loop_start = _time.time()

                # ── Grab frame and detect ──
                canny_steer = 0.0
                quality = 0.0
                centers_count = 0
                virtual_info_str = ""

                frame_bytes = await self.camera.get_latest_frame()
                if frame_bytes and CANNY_AVAILABLE and self._lane_tracker:
                    try:
                        nparr = np.frombuffer(frame_bytes, np.uint8)
                        frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if frame_bgr is not None:
                            steer_val, quality, debug, _ = self._lane_tracker.detect_and_steer(frame_bgr)
                            centers_count = debug.get('real_count', 0)

                            if quality >= QUALITY_MIN and centers_count >= 2:
                                canny_steer = steer_val
                                frames_ok += 1
                    except Exception as e:
                        logger.debug(f"Step loop detect error: {e}")

                # ── PID controller ──
                error = canny_steer
                derivative = error - self._prev_steer

                # Only accumulate integral when error is meaningful
                if abs(error) > DEAD_ZONE:
                    self._steer_integral += error * LOOP_INTERVAL
                    self._steer_integral = max(-MAX_INTEGRAL,
                                               min(MAX_INTEGRAL, self._steer_integral))
                else:
                    # Decay integral when centered
                    self._steer_integral *= 0.8

                pid_output = KP * error + KD * derivative + KI * self._steer_integral

                # ── Drift bias (long-term mechanical offset) ──
                self._drift_ema = self._drift_alpha * error + (1 - self._drift_alpha) * self._drift_ema
                if len(self._drift_history) >= 5:
                    recent = [s for s, _ in self._drift_history[-15:]]
                    drift_mean = np.mean(recent)
                    drift_std = np.std(recent) if len(recent) > 1 else 1.0
                    if drift_std < 0.25 and abs(drift_mean) > 0.04:
                        self._drift_bias = -0.25 * drift_mean

                # ── Final steer with EMA smoothing ──
                raw_steer = pid_output + self._drift_bias
                self._servo_steer_ema = (STEER_EMA_ALPHA * raw_steer +
                                         (1 - STEER_EMA_ALPHA) * self._servo_steer_ema)
                final_steer = max(-1.0, min(1.0, self._servo_steer_ema))

                # ── Apply to motor ──
                if abs(final_steer) > DEAD_ZONE:
                    self.motor.forward_steer(final_steer)
                else:
                    self.motor.forward(speed)

                self._prev_steer = error
                self._drift_history.append((error, LOOP_INTERVAL))
                if len(self._drift_history) > 60:
                    self._drift_history = self._drift_history[-40:]
                last_canny_steer = canny_steer
                last_quality = quality

                # Sleep remainder of loop interval
                elapsed = _time.time() - loop_start
                if elapsed < LOOP_INTERVAL:
                    await asyncio.sleep(LOOP_INTERVAL - elapsed)

            self.motor.stop()

            # Update grid coordinates
            dx, dy = self._direction_delta()
            self._map_x += dx
            self._map_y += dy
            self._map_step_count += 1

            logger.info(
                f"MAP-STEP: final_steer={final_steer:+.3f} quality={last_quality:.0%} "
                f"frames_ok={frames_ok} drift_bias={self._drift_bias:+.3f}"
            )

            # Report position
            if self.sio and self.sio.connected:
                await self.sio.emit('map-build-position', {
                    'x': self._map_x,
                    'y': self._map_y,
                    'direction': self._map_direction,
                    'directionName': self._direction_name(),
                    'stepCount': self._map_step_count,
                    'steering': final_steer,
                    'laneQuality': last_quality,
                    'framesOk': frames_ok,
                    'driftBias': self._drift_bias,
                    'timestamp': _time.time(),
                })

        except Exception as e:
            logger.error(f"Map-build step error: {e}")
            self.motor.stop()

    async def _map_build_turn(self, direction):
        """Turn 90 degrees left or right, update direction."""
        import time as _time

        try:
            turn_duration = 2.0  # Doubled for full 90-degree rotation
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
        """Run dynamic lane detection on current frame."""
        if not CANNY_AVAILABLE or self._lane_tracker is None:
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
            steer, quality, debug, _ = self._lane_tracker.detect_and_steer(frame)

            midpoint = None
            if 'mid_bot' in debug:
                midpoint = {'x': float(debug['mid_bot']), 'y': float(self._lane_tracker.y_bottom)}

            return {
                'steering': float(steer),
                'laneQuality': float(quality),
                'centersCount': debug.get('real_count', 0),
                'leftsCount': 0,
                'rightsCount': 0,
                'midpoint': midpoint,
                'frameWidth': w,
                'frameHeight': h,
                'framesLost': debug.get('frames_lost', 0),
                'virtualLeft': False,
                'virtualRight': False,
            }
        except Exception as e:
            logger.error(f"Lane analysis error: {e}")
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

    # ====== RFID Methods ======
    async def _rfid_scan_loop(self):
        """Continuously poll RFID reader until a tag is found or scan is stopped."""
        if not RFID_AVAILABLE or self._rfid_reader is None:
            logger.warning("RFID scan requested but reader not available")
            if self.sio and self.sio.connected:
                await self.sio.emit('rfid-scan-status', {
                    'scanning': False,
                    'error': 'RFID reader not available',
                })
            return

        if self._rfid_scanning:
            logger.info("RFID scan already in progress")
            return

        self._rfid_scanning = True
        if self.sio and self.sio.connected:
            await self.sio.emit('rfid-scan-status', {'scanning': True})

        logger.info("RFID scanning started...")
        try:
            while self._rfid_scanning:
                # Run blocking SPI read in executor to avoid blocking event loop
                loop = asyncio.get_event_loop()
                try:
                    uid, text = await asyncio.wait_for(
                        loop.run_in_executor(None, self._rfid_read_once),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    # No tag detected in this cycle, keep scanning
                    continue

                if uid:
                    rfid_id = str(uid)
                    logger.info(f"RFID tag detected: {rfid_id}")
                    self._rfid_scanning = False  # Stop after successful read
                    if self.sio and self.sio.connected:
                        await self.sio.emit('rfid-scanned', {
                            'rfidId': rfid_id,
                            'text': (text or '').strip(),
                        })
                        await self.sio.emit('rfid-scan-status', {'scanning': False})
                    break

                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"RFID scan error: {e}")
        finally:
            self._rfid_scanning = False
            if self.sio and self.sio.connected:
                await self.sio.emit('rfid-scan-status', {'scanning': False})
            logger.info("RFID scanning stopped")

    def _rfid_read_once(self):
        """Blocking read of one RFID tag. Called in executor thread."""
        try:
            uid = self._rfid_reader.read_id_no_block()
            return uid, ''
        except Exception:
            return None, None

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
            import cv2 as _cv2

            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                    'Cache-Control': 'no-cache',
                },
            )
            await response.prepare(request)
            boundary = b'--frame'

            if not CANNY_AVAILABLE or not self._lane_tracker:
                logger.warning("Lane stream requested but CANNY_AVAILABLE=%s, tracker=%s",
                               CANNY_AVAILABLE, self._lane_tracker is not None)

            try:
                while True:
                    frame_bytes = await self.camera.get_latest_frame()
                    if frame_bytes:
                        if CANNY_AVAILABLE and self._lane_tracker:
                            try:
                                nparr = np.frombuffer(frame_bytes, np.uint8)
                                frame_bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                                if frame_bgr is not None:
                                    h, w = frame_bgr.shape[:2]
                                    steer, quality, debug_info, vis = self._lane_tracker.detect_and_steer(frame_bgr)
                                    frames_lost = debug_info.get('frames_lost', 0)

                                    # Add position + steer text
                                    pos_text = f"Pos: ({self._map_x},{self._map_y}) Dir: {self._direction_name()}"
                                    steer_text = f"Steer: {steer:+.3f}"
                                    q_text = f"Quality: {quality:.0%} | Lost: {frames_lost}"
                                    _cv2.putText(vis, pos_text, (15, h - 70), _cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                                    _cv2.putText(vis, steer_text, (15, h - 45), _cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                                    _cv2.putText(vis, q_text, (15, h - 20), _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                                    _, jpeg = _cv2.imencode('.jpg', vis, [_cv2.IMWRITE_JPEG_QUALITY, 75])
                                    frame_bytes = jpeg.tobytes()
                            except Exception as e:
                                logger.warning(f"Lane overlay error: {e}", exc_info=True)
                                # Draw error text on raw frame so the user sees what's wrong
                                try:
                                    nparr = np.frombuffer(frame_bytes, np.uint8)
                                    err_frame = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                                    if err_frame is not None:
                                        h, w = err_frame.shape[:2]
                                        _cv2.putText(err_frame, "CANNY ERROR", (15, 35),
                                                     _cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
                                        _cv2.putText(err_frame, str(e)[:60], (15, 65),
                                                     _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                                        _, jpeg = _cv2.imencode('.jpg', err_frame, [_cv2.IMWRITE_JPEG_QUALITY, 75])
                                        frame_bytes = jpeg.tobytes()
                                except Exception:
                                    pass
                        else:
                            # Canny not available — show status overlay on raw frame
                            try:
                                nparr = np.frombuffer(frame_bytes, np.uint8)
                                raw_frame = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                                if raw_frame is not None:
                                    h, w = raw_frame.shape[:2]
                                    _cv2.putText(raw_frame, "CANNY NOT AVAILABLE", (15, 35),
                                                 _cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                                    reason = "Module not loaded" if not CANNY_AVAILABLE else "Detector not initialized"
                                    _cv2.putText(raw_frame, reason, (15, 60),
                                                 _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                                    _, jpeg = _cv2.imencode('.jpg', raw_frame, [_cv2.IMWRITE_JPEG_QUALITY, 75])
                                    frame_bytes = jpeg.tobytes()
                            except Exception:
                                pass

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
            if CANNY_AVAILABLE and self._lane_tracker:
                try:
                    import cv2
                    nparr = np.frombuffer(frame_bytes, np.uint8)
                    frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame_bgr is not None:
                        _, _, _, vis = self._lane_tracker.detect_and_steer(frame_bgr)
                        _, jpeg = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        frame_bytes = jpeg.tobytes()
                except Exception as e:
                    logger.debug(f"Lane snapshot error: {e}")
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
