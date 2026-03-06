#!/usr/bin/env python3
"""
Motor Control Module for Raspberry Pi 5 Delivery Bot
Dual Servo Motor via lgpio PWM on GPIO 12 & 13

Vehicle has 2 continuous-rotation servo motors controlled by PWM:
  - Forward:  Left servo CCW, Right servo CW
  - Backward: Left servo CW,  Right servo CCW
  - Turn Left:  Left servo CCW, Right servo CCW (pivot)
  - Turn Right: Left servo CW,  Right servo CW  (pivot)
  - Stop:     PWM duty = 0 on both pins

PWM Parameters:
  Frequency: 50 Hz (standard servo)
  Neutral:   1500 µs pulse width
  Drive:     ±300 µs from neutral
  Turn:      ±200 µs from neutral
"""

import logging
import time
import math
import asyncio

logger = logging.getLogger('motor_control')

# Try to import lgpio
try:
    import lgpio
    from gpio_handle import gpio_open
    GPIO_AVAILABLE = True
    logger.info("lgpio imported successfully")
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("lgpio not available - motor will use mock mode")


# ─── PWM Servo Constants ───
LEFT_PIN = 12       # BCM GPIO 12 - Left servo
RIGHT_PIN = 13      # BCM GPIO 13 - Right servo
PWM_FREQ = 50       # 50 Hz = standard servo frequency
STOP_VAL = 1500     # µs - neutral / stop position
DRIVE_SPEED = 300   # µs - offset from neutral for straight drive
TURN_SPEED = 200    # µs - offset from neutral for turning


class MockMotorController:
    """Mock controller for development/testing without hardware"""
    def __init__(self):
        self.status = 'idle'
        logger.info("MockMotorController initialized (no real hardware)")

    def forward(self, speed=50):
        self.status = 'forward'
        logger.info(f"[MOCK] Forward speed={speed}")

    def backward(self, speed=50):
        self.status = 'backward'
        logger.info(f"[MOCK] Backward speed={speed}")

    def turn_left(self, speed=50):
        self.status = 'turning_left'
        logger.info("[MOCK] Turn left (pivot)")

    def turn_right(self, speed=50):
        self.status = 'turning_right'
        logger.info("[MOCK] Turn right (pivot)")

    def forward_steer(self, correction):
        self.status = 'forward_steer'
        logger.info(f"[MOCK] Forward steer correction={correction:+.3f}")

    def stop(self):
        self.status = 'idle'
        logger.info("[MOCK] Stop")

    def cleanup(self):
        logger.info("[MOCK] Cleanup")

    def get_status(self):
        return {'driver': 'mock', 'status': self.status, 'type': 'dual_servo'}


class LgpioPWMController:
    """
    Dual Servo Motor controller via lgpio PWM.
    Directly drives 2 continuous-rotation servos on GPIO 12 & 13.
    """
    def __init__(self, config):
        motor_cfg = config.get('motor', {})
        self.left_pin = motor_cfg.get('left_pin', LEFT_PIN)
        self.right_pin = motor_cfg.get('right_pin', RIGHT_PIN)
        self.drive_speed = motor_cfg.get('drive_speed', DRIVE_SPEED)
        self.turn_speed = motor_cfg.get('turn_speed', TURN_SPEED)
        self.status = 'idle'
        self._claimed = False
        self._h = None

        self._init_gpio()

    def _init_gpio(self):
        """Initialize GPIO handle and claim output pins"""
        try:
            self._h = gpio_open()
            if self._h is None:
                raise RuntimeError("gpio_open() returned None")

            if not self._claimed:
                try:
                    lgpio.gpio_claim_output(self._h, self.left_pin)
                    lgpio.gpio_claim_output(self._h, self.right_pin)
                    self._claimed = True
                    logger.info(f"GPIO pins claimed: L={self.left_pin}, R={self.right_pin}")
                except lgpio.error as e:
                    if "busy" in str(e).lower():
                        logger.warning("GPIO pins already claimed, reusing")
                        self._claimed = True
                    else:
                        raise

            logger.info(f"✅ LgpioPWM motor ready: L=GPIO{self.left_pin}, R=GPIO{self.right_pin}")
        except Exception as e:
            logger.error(f"❌ GPIO init failed: {e}")
            self._h = None

    def _set_pwm(self, pin, pulse_us):
        """Set PWM on a pin with given pulse width in microseconds"""
        if self._h is None:
            logger.warning("GPIO not initialized, cannot set PWM")
            return
        try:
            if pulse_us == 0:
                # Stop PWM
                lgpio.tx_pwm(self._h, pin, 0, 0)
            else:
                duty = (pulse_us / 20000.0) * 100.0  # Convert µs to duty cycle %
                lgpio.tx_pwm(self._h, pin, PWM_FREQ, duty)
        except Exception as e:
            logger.error(f"PWM error on pin {pin}: {e}")

    def forward(self, speed=50):
        """Both servos rotate forward (fixed direction)"""
        self.status = 'forward'
        self._set_pwm(self.left_pin, STOP_VAL - self.drive_speed)
        self._set_pwm(self.right_pin, STOP_VAL + self.drive_speed)
        logger.info("Forward: both servos driving forward")

    def backward(self, speed=50):
        """Both servos rotate backward (fixed direction)"""
        self.status = 'backward'
        self._set_pwm(self.left_pin, STOP_VAL + self.drive_speed)
        self._set_pwm(self.right_pin, STOP_VAL - self.drive_speed)
        logger.info("Backward: both servos driving backward")

    def turn_left(self, speed=50):
        """Pivot left: left servo CCW, right servo CCW"""
        self.status = 'turning_left'
        self._set_pwm(self.left_pin, STOP_VAL - self.turn_speed)
        self._set_pwm(self.right_pin, STOP_VAL - self.turn_speed)
        logger.info("Turn Left: pivot (L-CCW, R-CCW)")

    def turn_right(self, speed=50):
        """Pivot right: left servo CW, right servo CW"""
        self.status = 'turning_right'
        self._set_pwm(self.left_pin, STOP_VAL + self.turn_speed)
        self._set_pwm(self.right_pin, STOP_VAL + self.turn_speed)
        logger.info("Turn Right: pivot (L-CW, R-CW)")

    def forward_steer(self, correction):
        """
        Drive forward with differential steering.

        correction in [-1.0 … +1.0]:
          negative → steer LEFT  (slow left wheel, speed right)
          positive → steer RIGHT (speed left wheel, slow right)
          0        → straight

        Works by applying a symmetric offset to both servo PWMs.
        Max steer offset = half the drive speed (150 µs at 300 base).
        """
        MAX_STEER = self.drive_speed * 0.5  # 150 µs at default
        offset = -correction * MAX_STEER    # negative corr → positive offset

        left_pwm  = STOP_VAL - self.drive_speed + offset
        right_pwm = STOP_VAL + self.drive_speed + offset

        # Clamp to avoid crossing neutral
        left_pwm  = min(left_pwm, STOP_VAL - 50)
        right_pwm = max(right_pwm, STOP_VAL + 50)

        self.status = 'forward_steer'
        self._set_pwm(self.left_pin, left_pwm)
        self._set_pwm(self.right_pin, right_pwm)

    def stop(self):
        """Stop both servos"""
        self.status = 'idle'
        self._set_pwm(self.left_pin, 0)
        self._set_pwm(self.right_pin, 0)
        logger.info("Stop: both servos stopped")

    def cleanup(self):
        """Stop motors and release GPIO"""
        self.stop()
        logger.info("Motor GPIO cleanup done")

    def get_status(self):
        return {
            'driver': 'lgpio_pwm',
            'type': 'dual_servo',
            'status': self.status,
            'left_pin': self.left_pin,
            'right_pin': self.right_pin,
            'gpio_connected': self._h is not None
        }


def create_motor_controller(config):
    """Factory: create the appropriate motor controller"""
    motor_cfg = config.get('motor', {})
    driver_type = motor_cfg.get('driver_type', 'auto')

    logger.info(f"Motor config: driver={driver_type}, GPIO available={GPIO_AVAILABLE}")

    # Try lgpio PWM first (direct hardware control)
    if GPIO_AVAILABLE and driver_type in ('lgpio', 'pwm', 'auto'):
        try:
            ctrl = LgpioPWMController(config)
            if ctrl._h is not None:
                return ctrl
            else:
                logger.warning("LgpioPWM created but GPIO handle is None")
        except Exception as e:
            logger.warning(f"LgpioPWM init failed: {e}")

    # Fallback to mock
    logger.info("Using MockMotorController (no hardware)")
    return MockMotorController()


# ─── AutoNavigator ─────────────────────────────────────────
# Drives the vehicle along a Dijkstra-calculated path using servo PWM.
# Calculates turn direction and drive duration based on (x, y) coordinates.
# Reports real-time position via callback.
# ────────────────────────────────────────────────────────────

def _sign(v):
    if v > 0: return 1
    if v < 0: return -1
    return 0


class AutoNavigator:
    """
    Auto-drive controller that follows a path of waypoints.

    Calibration constants (tweak for your physical car):
      DRIVE_TIME_PER_PIXEL  – seconds of motor-on time per canvas-pixel distance
      TURN_90_TIME          – seconds to pivot 90°
      TURN_CORRECTION       – multiplier for turns (>1 = overcorrect, <1 = under)
      DRIFT_CORRECTION_TIME – extra correction pulse at each waypoint
      POSITION_REPORT_INTERVAL – seconds between intermediate position reports
    """

    # ── Calibration (adjust to your chassis / wheel diameter) ──
    DRIVE_TIME_PER_PIXEL  = 0.008    # seconds per canvas-pixel of distance
    TURN_90_TIME          = 1.60     # seconds for a 90° pivot  (calibrated for full rotation)
    TURN_CORRECTION       = 1.05     # 5 % over-rotate to counteract drift
    DRIFT_CORRECTION_TIME = 0.10     # short counter-steer pulse after each turn
    POSITION_REPORT_INTERVAL = 0.40  # seconds between intermediate position reports

    # ── Line-following with continuous differential steering ──
    LINE_FOLLOW_INTERVAL  = 0.10     # seconds between camera checks (faster for smoother control)
    LINE_FOLLOW_GAIN      = 1.0      # proportional gain on correction [-1,1] → steer amount
    LINE_FOLLOW_D_GAIN    = 0.3      # derivative gain to dampen oscillation
    LINE_FOLLOW_THRESHOLD = 0.03     # min |offset| to trigger any steering (matches dead-zone)

    # Stuck detection: if correction stays same sign for too many consecutive frames
    STUCK_CONSECUTIVE_LIMIT = 12     # ~1.2s at 0.10 interval
    STUCK_MIN_MAGNITUDE     = 0.4    # only count as "stuck" if correction > this

    def __init__(self, motor_controller, camera_getter=None, line_follower=None):
        self.motor = motor_controller
        self.heading = None          # current (dx, dy) unit-direction vector
        self.navigating = False
        self._nav_task = None
        self._get_camera_frame = camera_getter   # async () → bytes|None
        self._line_follower = line_follower       # LineFollower instance

        # PD controller state
        self._prev_correction = 0.0
        self._stuck_counter = 0
        self._stuck_sign = 0

        logger.info("AutoNavigator initialised (line-follow=%s)",
                    'enabled' if line_follower and line_follower.is_ready else 'disabled')

    # ── Public API ──────────────────────────────────────────

    async def navigate_path(self, path, emit_cb):
        """
        Navigate *path* (list of dicts with pointId, x, y).
        *emit_cb(event, data)* is an async callable used for real-time reports.
        """
        if len(path) < 2:
            logger.warning("Path too short, nothing to navigate")
            return

        self.navigating = True
        self.heading = None
        start_time = time.time()
        point_ids = [p.get('pointId', '?') for p in path]

        logger.info(f"▶ AUTO-NAV START  path={point_ids}")
        await emit_cb('navigation-log', {
            'type': 'start',
            'pointId': path[0]['pointId'],
            'x': path[0]['x'], 'y': path[0]['y'],
            'timestamp': start_time,
            'route': point_ids,
        })

        for i in range(len(path) - 1):
            if not self.navigating:
                logger.info("⏹ Navigation cancelled mid-route")
                break

            cur  = path[i]
            nxt  = path[i + 1]
            dx   = nxt['x'] - cur['x']
            dy   = nxt['y'] - cur['y']
            dist = math.sqrt(dx * dx + dy * dy)
            target = (_sign(dx), _sign(dy))

            # ── Turn at intersection ──
            if self.heading is not None and self.heading != target:
                turn = self._calc_turn(self.heading, target)
                await self._exec_turn(turn)

            self.heading = target

            # ── Drive forward with continuous differential steering ──
            drive_sec = dist * self.DRIVE_TIME_PER_PIXEL
            logger.info(
                f"  ➜ {cur['pointId']} → {nxt['pointId']}  "
                f"dist={dist:.0f}px  drive={drive_sec:.2f}s  heading={self.heading}"
            )

            # Reset line-follower EMA state for new segment
            if self._line_follower and hasattr(self._line_follower, 'reset'):
                self._line_follower.reset()
            self._prev_correction = 0.0
            self._stuck_counter = 0
            self._stuck_sign = 0

            self.motor.forward()

            # Report interpolated positions during drive
            elapsed = 0.0
            while elapsed < drive_sec and self.navigating:
                step = min(self.LINE_FOLLOW_INTERVAL, drive_sec - elapsed)
                await asyncio.sleep(step)
                elapsed += step

                # ── Camera line-following with PD control + differential steering ──
                correction = await self._get_line_correction()
                if abs(correction) >= self.LINE_FOLLOW_THRESHOLD:
                    # PD controller
                    derivative = correction - self._prev_correction
                    steer = (self.LINE_FOLLOW_GAIN * correction +
                             self.LINE_FOLLOW_D_GAIN * derivative)
                    steer = max(-1.0, min(1.0, steer))

                    # Apply differential steering (no stop-turn-resume!)
                    self.motor.forward_steer(steer)

                    # Stuck detection
                    corr_sign = 1 if correction > 0 else -1
                    if (corr_sign == self._stuck_sign and
                            abs(correction) >= self.STUCK_MIN_MAGNITUDE):
                        self._stuck_counter += 1
                    else:
                        self._stuck_counter = 0
                        self._stuck_sign = corr_sign

                    if self._stuck_counter >= self.STUCK_CONSECUTIVE_LIMIT:
                        # Servo likely stuck — aggressive opposite correction
                        logger.warning(f"  ⚠ STUCK detected ({self._stuck_counter} frames), "
                                       f"aggressive reverse steer")
                        self.motor.stop()
                        await asyncio.sleep(0.1)
                        # Brief hard opposite turn
                        if correction > 0:
                            self.motor.turn_left()
                        else:
                            self.motor.turn_right()
                        await asyncio.sleep(0.25)
                        self.motor.forward()
                        self._stuck_counter = 0
                        await emit_cb('navigation-log', {
                            'type': 'stuck-recovery',
                            'correction': round(correction, 3),
                            'timestamp': time.time(),
                        })

                    logger.debug(f"    🔧 steer {steer:+.3f} (P={correction:+.3f} D={derivative:+.3f})")
                    await emit_cb('navigation-log', {
                        'type': 'line-correct',
                        'correction': round(correction, 3),
                        'steer': round(steer, 3),
                        'timestamp': time.time(),
                    })
                else:
                    # Centered — drive straight
                    self.motor.forward()
                    self._stuck_counter = 0

                self._prev_correction = correction

                # ── Position report at POSITION_REPORT_INTERVAL cadence ──
                progress = min(elapsed / drive_sec, 1.0)
                ix = cur['x'] + dx * progress
                iy = cur['y'] + dy * progress
                # Only emit position at the report interval
                if elapsed == step or int(elapsed / self.POSITION_REPORT_INTERVAL) != int((elapsed - step) / self.POSITION_REPORT_INTERVAL) or progress >= 1.0:
                    await emit_cb('navigation-log', {
                        'type': 'moving',
                        'x': round(ix, 1),
                        'y': round(iy, 1),
                        'fromPoint': cur['pointId'],
                        'toPoint': nxt['pointId'],
                        'progress': round(progress * 100),
                        'timestamp': time.time(),
                    })

            self.motor.stop()
            await asyncio.sleep(0.20)

            # ── Drift correction at waypoint ──
            if i < len(path) - 2:
                next_dx = path[i + 2]['x'] - nxt['x']
                next_dy = path[i + 2]['y'] - nxt['y']
                next_target = (_sign(next_dx), _sign(next_dy))
                if next_target != target:
                    # Approaching a turn — add micro-correction
                    await self._drift_correct(target, next_target)

            # ── Report waypoint reached ──
            logger.info(f"  ✔ Reached {nxt['pointId']} ({nxt['x']},{nxt['y']})")
            await emit_cb('navigation-log', {
                'type': 'waypoint',
                'pointId': nxt['pointId'],
                'x': nxt['x'], 'y': nxt['y'],
                'timestamp': time.time(),
            })
            await emit_cb('vehicle-position-update', {'pointId': nxt['pointId']})

        # ── Done ──
        self.motor.stop()
        end_time = time.time()
        duration = round(end_time - start_time, 2)
        logger.info(f"■ AUTO-NAV COMPLETE  duration={duration}s")
        await emit_cb('navigation-log', {
            'type': 'complete',
            'pointId': path[-1]['pointId'],
            'x': path[-1]['x'], 'y': path[-1]['y'],
            'startTime': start_time,
            'endTime': end_time,
            'duration': duration,
            'timestamp': end_time,
        })
        self.navigating = False

    def stop_navigation(self):
        """Cancel ongoing navigation."""
        self.navigating = False
        self.motor.stop()
        logger.info("AutoNavigator: navigation stopped")

    # ── Internal helpers ────────────────────────────────────

    def _calc_turn(self, cur_h, tgt_h):
        """Return 'left', 'right', or 'uturn'."""
        hx, hy = cur_h
        tx, ty = tgt_h
        cross = hx * ty - hy * tx   # positive → clockwise (screen coords, y-down)
        dot   = hx * tx + hy * ty
        if dot == 1:
            return 'straight'
        if dot == -1:
            return 'uturn'
        return 'right' if cross > 0 else 'left'

    async def _exec_turn(self, direction):
        """Execute a pivot turn."""
        if direction == 'straight':
            return
        t = self.TURN_90_TIME * self.TURN_CORRECTION
        if direction == 'uturn':
            t *= 2
        logger.info(f"  ↻ Turn {direction} for {t:.2f}s")
        if direction in ('right', 'uturn'):
            self.motor.turn_right()
        else:
            self.motor.turn_left()
        await asyncio.sleep(t)
        self.motor.stop()
        await asyncio.sleep(0.15)

    async def _drift_correct(self, cur_dir, next_dir):
        """
        Micro-correction pulse before an upcoming turn to compensate
        for wheel drift over the previous straight segment.
        """
        turn = self._calc_turn(cur_dir, next_dir)
        if turn == 'straight':
            return
        # Brief opposite steer to align wheels
        opp = 'left' if turn == 'right' else 'right'
        if opp == 'left':
            self.motor.turn_left()
        else:
            self.motor.turn_right()
        await asyncio.sleep(self.DRIFT_CORRECTION_TIME)
        self.motor.stop()
        await asyncio.sleep(0.10)

    # ── Camera-based lane correction ──

    async def _get_line_correction(self):
        """
        Get steering correction from camera + line-follower model.
        Returns float in [-1, +1], or 0.0 if unavailable.
        """
        if self._line_follower is None or not self._line_follower.is_ready:
            return 0.0
        if self._get_camera_frame is None:
            return 0.0
        try:
            frame = await self._get_camera_frame()
            if frame is None:
                return 0.0
            return self._line_follower.analyse_frame(frame)
        except Exception as e:
            logger.debug(f"Line-follow error: {e}")
            return 0.0
