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
from motor_control import create_motor_controller, AutoNavigator, LEFT_MIRROR, LEFT_NEUTRAL, RIGHT_NEUTRAL
from camera_mjpeg import CameraManager
from line_follower import LineFollower
from odometry import OdometryTracker

# RFID reader (custom spidev + lgpio driver for RST pin)
try:
    from rfid_reader import MFRC522Reader
    RFID_AVAILABLE = True
    logger.info("MFRC522 RFID reader module loaded (spidev+lgpio)")
except ImportError as e:
    RFID_AVAILABLE = False
    logger.warning(f"RFID reader not available: {e}")

# Line follower: Sliding Window BEV lane detection (see line_follower.py)

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
        self.odometry = OdometryTracker()

        # Wire AutoNavigator with camera + line-follower
        async def _cam_getter():
            return await self.camera.get_latest_frame()

        self.navigator = AutoNavigator(
            self.motor,
            camera_getter=_cam_getter,
            line_follower=self.line_follower,
            odometry=self.odometry,
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
        # Lane analysis now handled by self.line_follower (Sliding Window BEV)

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

        # ── Road sign detection overlay state ──
        self._sign_detections = []   # latest [{class, confidence, bbox}]
        self._sign_ts = 0            # timestamp of last detection

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

    def _calibrate_pwm(self, pin_name, pulse_us):
        """Apply PWM for calibration.
        If LEFT_MIRROR is set (env MOTOR_LEFT_MIRROR=1), the left motor
        pulse is inverted around 3000µs for mirror-mounted servos."""
        if pin_name == 'left' and LEFT_MIRROR:
            actual = 3000 - pulse_us
        else:
            actual = pulse_us
        if hasattr(self.motor, '_set_pwm'):
            self.motor._set_pwm(pin_name, actual)

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

        @self.sio.on('vehicle-returned')
        async def on_vehicle_returned(data=None):
            logger.info("Vehicle returned — resetting heading and line follower")
            self.navigator.heading = None
            self.navigator._prev_correction = 0.0
            if self.navigator._line_follower:
                self.navigator._line_follower.reset()

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
            """Move one step forward using lane follower."""
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
            """Analyse current frame and return results."""
            asyncio.ensure_future(self._map_build_analyse())

        # ====== Motor Calibration Commands ======
        # ====== Road Sign Detection Overlay ======
        @self.sio.on('sign-detected')
        async def on_sign_detected(data):
            self._sign_detections = data.get('detections', [])
            self._sign_ts = __import__('time').time()

        @self.sio.on('sign-detect-result')
        async def on_sign_detect_result(data):
            self._sign_detections = data.get('detections', [])
            self._sign_ts = __import__('time').time()

        @self.sio.on('sign-detect-status')
        async def on_sign_detect_status(data):
            if not data.get('detecting', False):
                self._sign_detections = []

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

        # Retry loop: backend may not be ready when hardware daemon starts
        max_retries = 30
        for attempt in range(1, max_retries + 1):
            try:
                await self.sio.connect(self.server_url)
                logger.info(f"✅ Socket.IO connected to {self.server_url}")
                break
            except Exception as e:
                logger.warning(f"Socket.IO connect attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                else:
                    logger.error(f"❌ Failed to connect to server after {max_retries} attempts")

    async def report_status(self):
        """Report full hardware status to server"""
        status = {
            'motor': self.motor.get_status(),
            'camera': self.camera.get_status(),
            'platform': 'Raspberry Pi 5',
            'gpio_available': True,
            'line_follower': self.line_follower.is_ready,
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

        Uses the Sliding Window BEV line follower which outputs
        steer in [-1, +1]. We trust that output directly
        and only add drift-bias compensation for mechanical offset.

          1. Start driving forward
          2. Every ~80ms: grab frame -> line follower -> get steer -> adjust motor
          3. Stop after step_duration
        """
        import time as _time
        import cv2

        # Configurable parameters
        step_duration = data.get('duration', 0.8)
        speed = data.get('speed', 40)
        LOOP_INTERVAL = 0.03          # 30ms → ~33 Hz servo update

        try:

            # Reset line follower state for new segment
            if self.line_follower and hasattr(self.line_follower, 'reset'):
                self.line_follower.reset()

            # Reset odometry for new step session
            if self._map_step_count == 0:
                self.odometry.reset()

            # Start with forward_steer(0) — continuous steering from tick 0
            # Never call motor.forward() in the loop — always forward_steer()
            self.motor.forward_steer(0.0)
            start_time = _time.time()
            last_steer = 0.0
            frames_ok = 0

            while (_time.time() - start_time) < step_duration:
                loop_start = _time.time()

                # ── Grab frame -> lane follower full analysis ──
                lane_steer = 0.0
                vision_cte = None
                vision_conf = 0.0

                frame_bytes = await self.camera.get_latest_frame()
                if frame_bytes and self.line_follower and self.line_follower.is_ready:
                    try:
                        result = self.line_follower.analyse_frame_full(frame_bytes)
                        lane_steer = result['steering']
                        vision_cte = result['vision_cte']
                        vision_conf = result['confidence']
                        frames_ok += 1
                    except Exception as e:
                        logger.debug(f"Step loop detect error: {e}")

                # ── Apply single-servo steering EVERY tick ──
                final_steer = max(-1.0, min(1.0, lane_steer))
                self.motor.forward_steer(final_steer)
                last_steer = lane_steer

                # ── Update odometry with sensor fusion ──
                odom_entry = self.odometry.update(
                    steering=final_steer,
                    vision_cte=vision_cte,
                    vision_confidence=vision_conf,
                    is_driving=True,
                )

                # ── Emit odom-log to frontend ──
                if self.sio and self.sio.connected:
                    await self.sio.emit('odom-log', odom_entry)

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
                f"frames_ok={frames_ok}"
            )

            # Report position
            if self.sio and self.sio.connected:
                odom_pose = self.odometry.pose
                await self.sio.emit('map-build-position', {
                    'x': self._map_x,
                    'y': self._map_y,
                    'direction': self._map_direction,
                    'directionName': self._direction_name(),
                    'stepCount': self._map_step_count,
                    'steering': final_steer,
                    'framesOk': frames_ok,
                    'odom': odom_pose,
                    'odomDist': self.odometry.total_distance,
                    'odomCte': self.odometry.fused_cte,
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
                self.odometry.handle_turn('left')
            else:
                self.motor.turn_right(speed)
                self._map_direction = (self._map_direction + 1) % 4
                self.odometry.handle_turn('right')

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

    async def _do_lane_analysis(self):
        """Run Sliding Window BEV lane follower on current frame."""
        if not self.line_follower or not self.line_follower.is_ready:
            return None

        frame_bytes = await self.camera.get_latest_frame()
        if frame_bytes is None:
            return None

        try:
            debug = self.line_follower.analyse_frame_debug(frame_bytes)
            return {
                'steering': float(debug.get('correction', 0)),
                'laneQuality': float(debug.get('confidence', 0)),
                'status': debug.get('status', 'N/A'),
                'intersection': debug.get('intersection', False),
            }
        except Exception as e:
            logger.error(f"Lane analysis error: {e}")
            return None



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

        # Initial neutral reading (use per-motor neutral)
        for pname in pin_names:
            neutral = LEFT_NEUTRAL if pname == 'left' else RIGHT_NEUTRAL
            self._calibrate_pwm(pname, neutral)
        if self.sio and self.sio.connected:
            await self.sio.emit('motor-calibrate-data', {
                'type': 'step', 'pin': pin,
                'pulse_us': 0, 'time': 0.0, 'phase': 'idle'
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
        """Find motor dead band by sweeping outward from neutral in small steps.
        Sweeps forward (neutral→neutral+max) then reverse (neutral→neutral-max).
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
            for pname in pin_names:
                neutral = LEFT_NEUTRAL if pname == 'left' else RIGHT_NEUTRAL
                pulse = neutral + offset
                self._calibrate_pwm(pname, pulse)
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'deadband', 'pin': pin,
                    'offset': offset,
                    'direction': 'forward',
                    'time': round(elapsed, 3),
                    'phase': 'forward'
                })
            await asyncio.sleep(hold_ms / 1000.0)

        # Return to neutral briefly
        for pname in pin_names:
            neutral = LEFT_NEUTRAL if pname == 'left' else RIGHT_NEUTRAL
            self._calibrate_pwm(pname, neutral)
        await asyncio.sleep(0.3)

        # Phase 2: Sweep reverse (below neutral)
        for offset in range(0, max_offset + 1, step_us):
            if not self._calibrate_running:
                break
            for pname in pin_names:
                neutral = LEFT_NEUTRAL if pname == 'left' else RIGHT_NEUTRAL
                pulse = neutral - offset
                self._calibrate_pwm(pname, pulse)
            elapsed = asyncio.get_event_loop().time() - t0
            if self.sio and self.sio.connected:
                await self.sio.emit('motor-calibrate-data', {
                    'type': 'deadband', 'pin': pin,
                    'offset': -offset,
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
        analysis = await self._do_lane_analysis()
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

        # ── Lane detection overlay stream (BEV sliding window) ──
        async def handle_lane_overlay_stream(request):
            """MJPEG stream with lane-detection overlay (sliding window BEV)."""
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
                    if frame_bytes and self.line_follower and self.line_follower.is_ready:
                        try:
                            debug = self.line_follower.analyse_frame_debug(frame_bytes)
                            jpeg = debug.get('mask_jpeg') or frame_bytes
                            frame_bytes = jpeg
                        except Exception as e:
                            logger.warning(f"Lane overlay error: {e}", exc_info=True)

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

        async def handle_lane_overlay_snapshot(request):
            """Single snapshot with lane-detection overlay."""
            frame_bytes = await self.camera.get_latest_frame()
            if not frame_bytes:
                return web.Response(status=503, text='No frame')
            if self.line_follower and self.line_follower.is_ready:
                try:
                    debug = self.line_follower.analyse_frame_debug(frame_bytes)
                    jpeg = debug.get('mask_jpeg')
                    if jpeg:
                        frame_bytes = jpeg
                except Exception as e:
                    logger.debug(f"Lane snapshot error: {e}")
            return web.Response(body=frame_bytes, content_type='image/jpeg')

        r_lane_overlay = cors.add(app.router.add_resource('/lane/overlay/stream'))
        cors.add(r_lane_overlay.add_route('GET', handle_lane_overlay_stream))
        r_lane_snap = cors.add(app.router.add_resource('/lane/overlay/snapshot'))
        cors.add(r_lane_snap.add_route('GET', handle_lane_overlay_snapshot))

        # ── Unified processed stream (mode via query param) ──
        async def handle_processed_stream(request):
            """MJPEG stream with switchable processing: raw|lane|all."""
            mode = request.query.get('mode', 'raw')

            # Only import cv2 when a processing mode actually needs it
            _cv2 = None
            if mode in ('sign', 'all'):
                try:
                    import cv2 as _cv2
                except ImportError:
                    logger.warning("cv2 not available for mode=%s", mode)

            sleep_map = {
                'raw': 0.03, 'lane': 0.07,
                'all': 0.10, 'sign': 0.05,
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
                        if mode == 'lane':
                            if self.line_follower and self.line_follower.is_ready:
                                try:
                                    debug = self.line_follower.analyse_frame_debug(frame_bytes)
                                    debug_jpeg = debug.get('mask_jpeg')
                                    if debug_jpeg:
                                        jpeg = debug_jpeg
                                except Exception:
                                    pass

                        elif mode == 'sign' and _cv2:
                            nparr = np.frombuffer(frame_bytes, np.uint8)
                            bgr = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
                            if bgr is not None:
                                orig_h, orig_w = bgr.shape[:2]
                                bgr = _cv2.resize(bgr, (640, 480))
                                sx = 640.0 / orig_w
                                sy = 480.0 / orig_h
                                import time as _time
                                dets = self._sign_detections
                                age = _time.time() - self._sign_ts
                                # Draw bounding boxes if detections are fresh (< 2s)
                                if dets and age < 2.0:
                                    # Colors per class
                                    _colors = {
                                        'go_straight_sign': (0, 200, 0),
                                        'park_sign': (200, 200, 0),
                                        'turn_left_sign': (200, 0, 0),
                                        'turn_right_sign': (0, 0, 200),
                                    }
                                    for d in dets:
                                        bbox = d.get('bbox', [])
                                        if len(bbox) != 4:
                                            continue
                                        x1 = int(bbox[0] * sx)
                                        y1 = int(bbox[1] * sy)
                                        x2 = int(bbox[2] * sx)
                                        y2 = int(bbox[3] * sy)
                                        cls = d.get('class', 'unknown')
                                        conf = d.get('confidence', 0)
                                        color = _colors.get(cls, (128, 128, 128))
                                        _cv2.rectangle(bgr, (x1, y1), (x2, y2), color, 2)
                                        label = f"{cls} {conf*100:.0f}%"
                                        (tw, th), _ = _cv2.getTextSize(label, _cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                                        _cv2.rectangle(bgr, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
                                        _cv2.putText(bgr, label, (x1 + 2, y1 - 4),
                                                     _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
                                else:
                                    # No active detections — show scan status
                                    _cv2.putText(bgr, "[SIGN] Scanning...",
                                                 (15, 25), _cv2.FONT_HERSHEY_SIMPLEX,
                                                 0.6, (0, 255, 255), 2)
                                # Mode label
                                h = bgr.shape[0]
                                _cv2.putText(bgr, "[SIGN] Road Sign Detection",
                                             (15, h - 15), _cv2.FONT_HERSHEY_SIMPLEX,
                                             0.5, (255, 255, 0), 1)
                                _, enc = _cv2.imencode('.jpg', bgr,
                                                       [_cv2.IMWRITE_JPEG_QUALITY, 90])
                                jpeg = enc.tobytes()

                        elif mode == 'all' and _cv2:
                            # Lane overlay via line follower debug
                            if self.line_follower and self.line_follower.is_ready:
                                try:
                                    debug = self.line_follower.analyse_frame_debug(frame_bytes)
                                    debug_jpeg = debug.get('mask_jpeg')
                                    if debug_jpeg:
                                        jpeg = debug_jpeg
                                except Exception:
                                    pass

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
║   Motor: 2x Servo (HW PWM GPIO12,13)     ║
╚══════════════════════════════════════════╝
    """)
    asyncio.run(main())
