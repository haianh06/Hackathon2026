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

# ── Limit CPU threads BEFORE importing torch/ultralytics ──
_max_threads = int(os.environ.get('TORCH_NUM_THREADS', '2'))

try:
    import torch
    torch.set_num_threads(_max_threads)
    torch.set_num_interop_threads(1)
except ImportError:
    pass

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
    from test_1_improved import AdaptiveTrackerV2
    CANNY_AVAILABLE = True
    logger.info("AdaptiveTrackerV2 loaded successfully")
except ImportError as e:
    CANNY_AVAILABLE = False
    logger.warning(f"AdaptiveTrackerV2 not available: {e}")

# Import road sign detector
try:
    from road_sign_detector import RoadSignDetector
    SIGN_DETECTOR_AVAILABLE = True
    logger.info("RoadSignDetector module loaded")
except ImportError as e:
    SIGN_DETECTOR_AVAILABLE = False
    logger.warning(f"RoadSignDetector not available: {e}")

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
        self._lane_tracker = AdaptiveTrackerV2(width=640, height=480) if CANNY_AVAILABLE else None

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

        # ── Motor calibration state ──
        self._calibrate_running = False

        # ── Road sign detection state ──
        self._sign_detecting = False
        self._sign_detector = None
        if SIGN_DETECTOR_AVAILABLE:
            try:
                sign_cfg = CONFIG.get('sign_detection', {})
                self._sign_detector = RoadSignDetector(
                    conf_threshold=sign_cfg.get('confidence', 0.5)
                )
                if self._sign_detector.is_ready:
                    logger.info("Road sign detector initialized")
                else:
                    logger.warning("Road sign detector model not ready")
            except Exception as e:
                logger.warning(f"Road sign detector init failed: {e}")

    def _calibrate_pwm(self, pin_name, pulse_us):
        """Apply PWM for calibration, inverting left motor around 1500µs.
        Left servo is mirror-mounted, so its direction is inverted:
        left_actual = 3000 - pulse_us (e.g. 1800→1200, 1300→1700)
        This makes both motors respond in the same physical direction
        for the same commanded pulse value."""
        if pin_name == 'left':
            actual = 3000 - pulse_us
        else:
            actual = pulse_us
        target_pin = self.motor.left_pin if pin_name == 'left' else self.motor.right_pin
        if hasattr(self.motor, '_set_pwm'):
            self.motor._set_pwm(target_pin, actual)

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
            order_id = data.get('orderId')
            is_return = data.get('isReturn', False)
            initial_heading = data.get('heading')  # [dx, dy] from server
            logger.info(f"🚀 Auto-navigate requested: {[p.get('pointId', '?') for p in path]} orderId={order_id} isReturn={is_return} heading={initial_heading}")
            if len(path) < 2:
                logger.warning("Auto-navigate: path too short")
                return

            # Restore heading from server if daemon has no heading yet
            if self.navigator.heading is None and initial_heading:
                self.navigator.set_heading(initial_heading)

            # Prevent concurrent navigations — stop any running one first
            if self.navigator.navigating:
                logger.warning("⚠ Navigation already in progress, stopping previous one first")
                self.navigator.stop_navigation()
                await asyncio.sleep(0.3)

            async def emit_cb(event, payload):
                """Forward events to backend server. Safe — never throws."""
                try:
                    if self.sio and self.sio.connected:
                        await self.sio.emit(event, payload)
                except Exception as e:
                    logger.warning(f"emit_cb failed ({event}): {e}")

            async def run_and_complete():
                try:
                    await self.navigator.navigate_path(path, emit_cb)
                    # Emit navigation-complete so backend can update order status
                    heading = list(self.navigator.heading) if self.navigator.heading else None
                    await emit_cb('navigation-complete', {
                        'orderId': order_id,
                        'isReturn': is_return,
                        'destination': path[-1].get('pointId') if path else None,
                        'heading': heading,
                    })
                    logger.info(f"✅ navigation-complete emitted orderId={order_id} isReturn={is_return} heading={heading}")
                except Exception as e:
                    logger.error(f"❌ Navigation failed: {e}")
                finally:
                    # ALWAYS stop motor — prevents servo spinning forever
                    self.motor.stop()
                    self.navigator.navigating = False
                    logger.info("🛑 Motor guaranteed stopped after navigation")

            # Run navigation in background so we can still receive stop commands
            asyncio.ensure_future(run_and_complete())

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

        # ====== Motor Calibration Commands ======
        @self.sio.on('motor-calibrate-set')
        async def on_motor_calibrate_set(data):
            """Set PWM pulse width on a specific motor pin (auto-inverts left)."""
            pin = data.get('pin', 'left')  # 'left' or 'right'
            pulse_us = int(data.get('pulse_us', 1500))
            logger.info(f"🔧 Calibrate: set {pin} to {pulse_us}µs")
            self._calibrate_pwm(pin, pulse_us)
            # Report back
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'set',
                    'pin': pin,
                    'pulse_us': pulse_us,
                    'timestamp': asyncio.get_event_loop().time()
                })

        @self.sio.on('motor-calibrate-sweep')
        async def on_motor_calibrate_sweep(data):
            """Run a sweep test: ramp PWM from start to end and back."""
            logger.info(f"🔧 Calibrate: sweep test requested")
            self._calibrate_running = True
            asyncio.ensure_future(self._run_sweep_test(data))

        @self.sio.on('motor-calibrate-step')
        async def on_motor_calibrate_step(data):
            """Run a step response test: jump to target PWM and hold."""
            logger.info(f"🔧 Calibrate: step test requested")
            self._calibrate_running = True
            asyncio.ensure_future(self._run_step_test(data))

        @self.sio.on('motor-calibrate-stop')
        async def on_motor_calibrate_stop(data=None):
            """Stop any running calibration test."""
            logger.info("🔧 Calibrate: stop")
            self._calibrate_running = False
            self.motor.stop()

        @self.sio.on('motor-calibrate-deadband')
        async def on_motor_calibrate_deadband(data):
            """Run a deadband test: sweep outward from neutral to find dead zone."""
            logger.info("🔧 Calibrate: deadband test requested")
            self._calibrate_running = True
            asyncio.ensure_future(self._run_deadband_test(data))

        # ====== Road Sign Detection Commands ======
        @self.sio.on('sign-detect-start')
        async def on_sign_detect_start(data=None):
            """Start continuous road sign detection loop."""
            logger.info("🚦 Sign detection: start requested")
            if self._sign_detecting:
                logger.info("Sign detection already running")
                return
            asyncio.ensure_future(self._sign_detect_loop())

        @self.sio.on('sign-detect-stop')
        async def on_sign_detect_stop(data=None):
            """Stop road sign detection loop."""
            logger.info("🚦 Sign detection: stop")
            self._sign_detecting = False

        @self.sio.on('sign-detect-once')
        async def on_sign_detect_once(data=None):
            """Run a single detection on the current frame."""
            logger.info("🚦 Sign detection: single frame")
            asyncio.ensure_future(self._sign_detect_single())

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
            'sign_detector_available': SIGN_DETECTOR_AVAILABLE and self._sign_detector is not None and self._sign_detector.is_ready,
            'sign_detecting': self._sign_detecting,
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

        AdaptiveTrackerV2 has a built-in PD controller + EMA filter
        that outputs steer in [-1, +1]. We trust that output directly
        and only add drift-bias compensation for mechanical offset.

          1. Start driving forward
          2. Every ~80ms: grab frame → canny process_frame → get steer → adjust motor
          3. Stop after step_duration
        """
        import time as _time
        import cv2

        # Configurable parameters
        step_duration = data.get('duration', 0.8)
        speed = data.get('speed', 40)
        LOOP_INTERVAL = 0.08          # 80ms → ~12.5 Hz servo update rate
        DEAD_ZONE = 0.04              # Ignore tiny corrections

        try:

            # Start driving
            self.motor.forward(speed)
            start_time = _time.time()
            last_canny_steer = 0.0
            frames_ok = 0

            while (_time.time() - start_time) < step_duration:
                loop_start = _time.time()

                # ── Grab frame and detect ──
                canny_steer = 0.0

                frame_bytes = await self.camera.get_latest_frame()
                if frame_bytes and CANNY_AVAILABLE and self._lane_tracker:
                    try:
                        nparr = np.frombuffer(frame_bytes, np.uint8)
                        frame_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if frame_bgr is not None:
                            frame_bgr = cv2.resize(frame_bgr, (640, 480))
                            steer_val, _ = self._lane_tracker.process_frame(frame_bgr)
                            canny_steer = steer_val
                            frames_ok += 1
                    except Exception as e:
                        logger.debug(f"Step loop detect error: {e}")

                # ── Use canny steer directly ──
                # Canny's process_frame() already has PD controller + EMA.
                # We only add minimal drift bias correction here.
                error = canny_steer

                # ── Drift bias (long-term mechanical offset) ──
                self._drift_ema = self._drift_alpha * error + (1 - self._drift_alpha) * self._drift_ema
                if len(self._drift_history) >= 5:
                    recent = [s for s, _ in self._drift_history[-15:]]
                    drift_mean = np.mean(recent)
                    drift_std = np.std(recent) if len(recent) > 1 else 1.0
                    if drift_std < 0.25 and abs(drift_mean) > 0.04:
                        self._drift_bias = -0.25 * drift_mean

                # ── Final steer: canny output + drift bias only ──
                final_steer = max(-1.0, min(1.0, canny_steer + self._drift_bias))

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
                f"MAP-STEP: final_steer={final_steer:+.3f} "
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
        """Run AdaptiveTrackerV2 on current frame."""
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
            frame = cv2.resize(frame, (640, 480))
            steer, viz_frame = self._lane_tracker.process_frame(frame)

            return {
                'steering': float(steer),
                'laneQuality': 1.0 if abs(steer) > 0.001 else 0.0,
                'centersCount': 0,
                'leftsCount': 0,
                'rightsCount': 0,
                'midpoint': None,
                'frameWidth': w,
                'frameHeight': h,
                'framesLost': 0,
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

    # ====== Motor Calibration Methods ======
    async def _run_sweep_test(self, data):
        """Ramp PWM from start_us to end_us, then back, reporting data points."""
        pin = data.get('pin', 'both')  # 'left', 'right', or 'both'
        start_us = int(data.get('start_us', 1500))
        end_us = int(data.get('end_us', 1800))
        step_us = int(data.get('step_us', 10))
        hold_ms = int(data.get('hold_ms', 200))

        pin_names = []
        if pin in ('left', 'both'):
            pin_names.append('left')
        if pin in ('right', 'both'):
            pin_names.append('right')

        direction = 1 if end_us > start_us else -1
        current = start_us
        t0 = asyncio.get_event_loop().time()

        # Sweep forward
        while self._calibrate_running:
            for pname in pin_names:
                self._calibrate_pwm(pname, current)
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'sweep', 'pin': pin,
                    'pulse_us': current, 'time': round(elapsed, 3),
                    'phase': 'forward'
                })
            await asyncio.sleep(hold_ms / 1000.0)
            current += step_us * direction
            if (direction > 0 and current > end_us) or (direction < 0 and current < end_us):
                break

        # Sweep back
        current = end_us
        while self._calibrate_running:
            for pname in pin_names:
                self._calibrate_pwm(pname, current)
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'sweep', 'pin': pin,
                    'pulse_us': current, 'time': round(elapsed, 3),
                    'phase': 'reverse'
                })
            await asyncio.sleep(hold_ms / 1000.0)
            current -= step_us * direction
            if (direction > 0 and current < start_us) or (direction < 0 and current > start_us):
                break

        # Done - stop motors
        self.motor.stop()
        self._calibrate_running = False
        if self.sio and self.sio.connected:
            await self.sio.emit('motor-calibrate-data', {
                'type': 'sweep_done', 'pin': pin
            })

    async def _run_step_test(self, data):
        """Jump to target PWM and hold, reporting data over time."""
        pin = data.get('pin', 'both')
        target_us = int(data.get('target_us', 1800))
        duration_s = float(data.get('duration_s', 3.0))
        sample_ms = int(data.get('sample_ms', 50))

        pin_names = []
        if pin in ('left', 'both'):
            pin_names.append('left')
        if pin in ('right', 'both'):
            pin_names.append('right')

        t0 = asyncio.get_event_loop().time()

        # Initial neutral reading
        for pname in pin_names:
            self._calibrate_pwm(pname, 1500)
        if self.sio and self.sio.connected:
            await self.sio.emit('motor-calibrate-data', {
                'type': 'step', 'pin': pin,
                'pulse_us': 1500, 'time': 0.0, 'phase': 'idle'
            })
        await asyncio.sleep(0.5)

        # Step to target
        for pname in pin_names:
            self._calibrate_pwm(pname, target_us)

        elapsed = 0
        while self._calibrate_running and elapsed < duration_s:
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'step', 'pin': pin,
                    'pulse_us': target_us, 'time': round(elapsed, 3),
                    'phase': 'active'
                })
            await asyncio.sleep(sample_ms / 1000.0)

        # Return to neutral
        self.motor.stop()
        elapsed = asyncio.get_event_loop().time() - t0
        if self.sio and self.sio.connected:
            await self.sio.emit('motor-calibrate-data', {
                'type': 'step', 'pin': pin,
                'pulse_us': 0, 'time': round(elapsed, 3),
                'phase': 'done'
            })
        self._calibrate_running = False

    async def _run_deadband_test(self, data):
        """Find motor dead band by sweeping outward from 1500µs in small steps.
        Sweeps forward (1500→1500+max) then reverse (1500→1500-max).
        User observes when motor starts spinning to determine dead zone edges."""
        pin = data.get('pin', 'both')
        step_us = int(data.get('step_us', 1))
        hold_ms = int(data.get('hold_ms', 100))
        max_offset = int(data.get('max_offset', 150))

        pin_names = []
        if pin in ('left', 'both'):
            pin_names.append('left')
        if pin in ('right', 'both'):
            pin_names.append('right')

        t0 = asyncio.get_event_loop().time()

        # Phase 1: Sweep forward (above neutral)
        for offset in range(0, max_offset + 1, step_us):
            if not self._calibrate_running:
                break
            pulse = 1500 + offset
            for pname in pin_names:
                self._calibrate_pwm(pname, pulse)
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'deadband', 'pin': pin,
                    'pulse_us': pulse, 'offset': offset,
                    'direction': 'forward',
                    'time': round(elapsed, 3),
                    'phase': 'forward'
                })
            await asyncio.sleep(hold_ms / 1000.0)

        # Return to neutral briefly
        for pname in pin_names:
            self._calibrate_pwm(pname, 1500)
        await asyncio.sleep(0.3)

        # Phase 2: Sweep reverse (below neutral)
        for offset in range(0, max_offset + 1, step_us):
            if not self._calibrate_running:
                break
            pulse = 1500 - offset
            for pname in pin_names:
                self._calibrate_pwm(pname, pulse)
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'deadband', 'pin': pin,
                    'pulse_us': pulse, 'offset': -offset,
                    'direction': 'reverse',
                    'time': round(elapsed, 3),
                    'phase': 'reverse'
                })
            await asyncio.sleep(hold_ms / 1000.0)

        # Done
        self.motor.stop()
        self._calibrate_running = False
        if self.sio and self.sio.connected:
            await self.sio.emit('motor-calibrate-data', {
                'type': 'deadband_done', 'pin': pin
            })

    # ====== Road Sign Detection Methods ======
    async def _sign_detect_loop(self):
        """Continuously detect road signs from camera frames."""
        if not self._sign_detector or not self._sign_detector.is_ready:
            logger.warning("Sign detection requested but detector not ready")
            if self.sio and self.sio.connected:
                await self.sio.emit('sign-detect-status', {
                    'detecting': False,
                    'error': 'Detector not available',
                })
            return

        self._sign_detecting = True
        if self.sio and self.sio.connected:
            await self.sio.emit('sign-detect-status', {'detecting': True})
        logger.info("🚦 Sign detection loop started")

        try:
            while self._sign_detecting:
                frame_bytes = await self.camera.get_latest_frame()
                if frame_bytes is None:
                    await asyncio.sleep(0.5)
                    continue

                # Run detection in executor to avoid blocking event loop
                loop = asyncio.get_event_loop()
                detections = await loop.run_in_executor(
                    None, self._sign_detector.detect, frame_bytes
                )

                if detections and self.sio and self.sio.connected:
                    import time as _time
                    await self.sio.emit('sign-detected', {
                        'detections': detections,
                        'count': len(detections),
                        'timestamp': _time.time(),
                    })
                    logger.info(f"🚦 Detected {len(detections)} sign(s): "
                                f"{[d['class'] for d in detections]}")

                # ~2 FPS detection rate to keep CPU manageable on Pi 5
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"Sign detection loop error: {e}")
        finally:
            self._sign_detecting = False
            if self.sio and self.sio.connected:
                await self.sio.emit('sign-detect-status', {'detecting': False})
            logger.info("🚦 Sign detection loop stopped")

    async def _sign_detect_single(self):
        """Run a single detection on the current frame and return annotated image."""
        if not self._sign_detector or not self._sign_detector.is_ready:
            if self.sio and self.sio.connected:
                await self.sio.emit('sign-detect-result', {
                    'detections': [],
                    'error': 'Detector not available',
                })
            return

        frame_bytes = await self.camera.get_latest_frame()
        if frame_bytes is None:
            if self.sio and self.sio.connected:
                await self.sio.emit('sign-detect-result', {
                    'detections': [],
                    'error': 'No camera frame',
                })
            return

        loop = asyncio.get_event_loop()
        detections = await loop.run_in_executor(
            None, self._sign_detector.detect, frame_bytes
        )

        import time as _time
        if self.sio and self.sio.connected:
            await self.sio.emit('sign-detect-result', {
                'detections': detections,
                'count': len(detections),
                'timestamp': _time.time(),
            })

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
                    await asyncio.sleep(0.07)
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
                                    frame_bgr = _cv2.resize(frame_bgr, (640, 480))
                                    steer, vis = self._lane_tracker.process_frame(frame_bgr)

                                    # Add position text on viz
                                    pos_text = f"Pos: ({self._map_x},{self._map_y}) Dir: {self._direction_name()}"
                                    _cv2.putText(vis, pos_text, (15, 480 - 30), _cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                                    _, jpeg = _cv2.imencode('.jpg', vis, [_cv2.IMWRITE_JPEG_QUALITY, 90])
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
                                        _, jpeg = _cv2.imencode('.jpg', err_frame, [_cv2.IMWRITE_JPEG_QUALITY, 90])
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
                                    _, jpeg = _cv2.imencode('.jpg', raw_frame, [_cv2.IMWRITE_JPEG_QUALITY, 90])
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
                    await asyncio.sleep(0.07)
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
                        frame_bgr = cv2.resize(frame_bgr, (640, 480))
                        _, vis = self._lane_tracker.process_frame(frame_bgr)
                        _, jpeg = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        frame_bytes = jpeg.tobytes()
                except Exception as e:
                    logger.debug(f"Lane snapshot error: {e}")
            return web.Response(body=frame_bytes, content_type='image/jpeg')

        r_canny_stream = cors.add(app.router.add_resource('/canny/stream'))
        cors.add(r_canny_stream.add_route('GET', handle_canny_stream))
        r_canny_snap = cors.add(app.router.add_resource('/canny/snapshot'))
        cors.add(r_canny_snap.add_route('GET', handle_canny_snapshot))

        # ── Unified processed stream (mode via query param) ──
        async def handle_processed_stream(request):
            """MJPEG stream with switchable processing: raw|canny|unet|sign|all."""
            mode = request.query.get('mode', 'raw')

            # Only import cv2 when a processing mode actually needs it
            _cv2 = None
            if mode in ('canny', 'sign', 'all'):
                try:
                    import cv2 as _cv2
                except ImportError:
                    logger.warning("cv2 not available for mode=%s", mode)

            sleep_map = {
                'raw': 0.03, 'canny': 0.07, 'unet': 0.10,
                'sign': 0.15, 'all': 0.20,
            }
            sleep_time = sleep_map.get(mode, 0.1)

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
                    if not frame_bytes:
                        await asyncio.sleep(0.1)
                        continue

                    jpeg = frame_bytes

                    try:
                        if mode == 'canny':
                            if CANNY_AVAILABLE and self._lane_tracker and _cv2:
                                nparr = np.frombuffer(frame_bytes, np.uint8)
                                bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                                if bgr is not None:
                                    bgr = _cv2.resize(bgr, (640, 480))
                                    _, vis = self._lane_tracker.process_frame(bgr)
                                    _, enc = _cv2.imencode(
                                        '.jpg', vis,
                                        [_cv2.IMWRITE_JPEG_QUALITY, 90])
                                    jpeg = enc.tobytes()

                        elif mode == 'unet':
                            if self.line_follower.is_ready:
                                debug = self.line_follower.analyse_frame_debug(
                                    frame_bytes)
                                jpeg = debug.get('mask_jpeg') or frame_bytes

                        elif mode == 'sign':
                            if (SIGN_DETECTOR_AVAILABLE
                                    and self._sign_detector
                                    and self._sign_detector.is_ready):
                                _, annotated = \
                                    self._sign_detector.detect_annotated(
                                        frame_bytes)
                                if annotated:
                                    jpeg = annotated

                        elif mode == 'all' and _cv2:
                            nparr = np.frombuffer(frame_bytes, np.uint8)
                            bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                            if bgr is not None:
                                bgr = _cv2.resize(bgr, (640, 480))

                                # Re-encode at 640x480 for consistent coords
                                _, resized_enc = _cv2.imencode(
                                    '.jpg', bgr,
                                    [_cv2.IMWRITE_JPEG_QUALITY, 85])
                                resized_bytes = resized_enc.tobytes()

                                # 1) Canny lane overlay → base visualization
                                if CANNY_AVAILABLE and self._lane_tracker:
                                    _, vis = self._lane_tracker.process_frame(
                                        bgr)
                                else:
                                    vis = bgr.copy()

                                # 2) Sign detection bboxes on canny vis
                                if (SIGN_DETECTOR_AVAILABLE
                                        and self._sign_detector
                                        and self._sign_detector.is_ready):
                                    dets = self._sign_detector.detect(
                                        resized_bytes)
                                    for d in dets:
                                        x1, y1, x2, y2 = d['bbox']
                                        _cv2.rectangle(
                                            vis, (x1, y1), (x2, y2),
                                            (0, 255, 0), 2)
                                        lbl = (f"{d['class']} "
                                               f"{d['confidence']:.0%}")
                                        (tw, th), _ = _cv2.getTextSize(
                                            lbl,
                                            _cv2.FONT_HERSHEY_SIMPLEX,
                                            0.6, 1)
                                        _cv2.rectangle(
                                            vis,
                                            (x1, y1 - th - 8),
                                            (x1 + tw + 6, y1),
                                            (0, 255, 0), -1)
                                        _cv2.putText(
                                            vis, lbl,
                                            (x1 + 3, y1 - 5),
                                            _cv2.FONT_HERSHEY_SIMPLEX,
                                            0.6, (0, 0, 0), 1)

                                # 3) UNet correction overlay text
                                if self.line_follower.is_ready:
                                    dbg = \
                                        self.line_follower.analyse_frame_debug(
                                            resized_bytes)
                                    corr = dbg.get('correction', 0.0)
                                    conf = dbg.get('confidence', 0.0)
                                    _cv2.putText(
                                        vis,
                                        f"UNet corr={corr:.3f} "
                                        f"conf={conf:.1%}",
                                        (15, 25),
                                        _cv2.FONT_HERSHEY_SIMPLEX,
                                        0.6, (0, 255, 255), 2)

                                # 4) Mode label
                                h = vis.shape[0]
                                _cv2.putText(
                                    vis, "[ALL] Canny + Sign + UNet",
                                    (15, h - 15),
                                    _cv2.FONT_HERSHEY_SIMPLEX,
                                    0.5, (255, 255, 0), 1)

                                _, enc = _cv2.imencode(
                                    '.jpg', vis,
                                    [_cv2.IMWRITE_JPEG_QUALITY, 90])
                                jpeg = enc.tobytes()

                    except Exception as e:
                        logger.warning(
                            "Processed stream (%s) error: %s", mode, e)

                    await response.write(
                        boundary + b'\r\n'
                        b'Content-Type: image/jpeg\r\n'
                        b'Content-Length: '
                        + str(len(jpeg)).encode() + b'\r\n'
                        b'\r\n' + jpeg + b'\r\n'
                    )
                    await asyncio.sleep(sleep_time)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return response

        r_processed = cors.add(
            app.router.add_resource('/processed/stream'))
        cors.add(r_processed.add_route('GET', handle_processed_stream))

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
