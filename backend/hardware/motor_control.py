#!/usr/bin/env python3
"""
Motor Control Module for Raspberry Pi 5 Delivery Bot
Dual Servo Motor via Hardware PWM on GPIO 12 & 13

Uses rpi-hardware-pwm (sysfs /sys/class/pwm) for jitter-free PWM.
Requires dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4 in
/boot/firmware/config.txt.

Vehicle has 2 continuous-rotation servo motors controlled by PWM.
Each motor has its own neutral point and drive offset (asymmetric).
Servos are mirror-mounted: opposite pulse directions = same wheel direction.

  - Left motor:  pulse < neutral = FORWARD,  pulse > neutral = BACKWARD
  - Right motor: pulse > neutral = FORWARD,  pulse < neutral = BACKWARD
  - Forward:  left=neutral-offset, right=neutral+offset
  - Backward: left=neutral+offset, right=neutral-offset
  - Turn Left:  left backward (neutral+offset), right forward (neutral+offset)
  - Turn Right: left forward (neutral-offset), right backward (neutral-offset)
  - Stop:     PWM disabled on both channels

All PWM parameters are loaded from environment variables for easy
tuning without rebuilding Docker. See hardware.env for defaults.
"""

import logging
import os
import time
import math
import asyncio
import threading

logger = logging.getLogger('motor_control')

# Try to import rpi-hardware-pwm
try:
    from rpi_hardware_pwm import HardwarePWM
    HW_PWM_AVAILABLE = True
    logger.info("rpi-hardware-pwm imported successfully")
except ImportError:
    HW_PWM_AVAILABLE = False
    logger.warning("rpi-hardware-pwm not available - motor will use mock mode")



# ─── PWM Servo Constants (from environment variables) ───
# dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
# GPIO 12 → PWM chip 0, channel 0
# GPIO 13 → PWM chip 0, channel 1
LEFT_PWM_CHIP  = int(os.environ.get('MOTOR_LEFT_PWM_CHIP', 0))
LEFT_PWM_CHAN  = int(os.environ.get('MOTOR_LEFT_PWM_CHANNEL', 0))
RIGHT_PWM_CHIP = int(os.environ.get('MOTOR_RIGHT_PWM_CHIP', 0))
RIGHT_PWM_CHAN = int(os.environ.get('MOTOR_RIGHT_PWM_CHANNEL', 1))
PWM_FREQ       = int(os.environ.get('MOTOR_PWM_FREQ', 50))

# Legacy pin numbers (kept for status reporting / calibration UI)
LEFT_PIN       = int(os.environ.get('MOTOR_LEFT_PIN', 12))
RIGHT_PIN      = int(os.environ.get('MOTOR_RIGHT_PIN', 13))

# Each motor has its own neutral (dead band center) point
LEFT_NEUTRAL   = int(os.environ.get('MOTOR_LEFT_NEUTRAL', 1500))
RIGHT_NEUTRAL  = int(os.environ.get('MOTOR_RIGHT_NEUTRAL', 1500))

# Stop point calibration: exact µs where servo truly stops
LEFT_STOP_POINT  = int(os.environ.get('MOTOR_LEFT_STOP_POINT', 1500))
RIGHT_STOP_POINT = int(os.environ.get('MOTOR_RIGHT_STOP_POINT', 1500))

# Drive/turn offsets per motor (above neutral = forward)
LEFT_DRIVE_OFFSET  = int(os.environ.get('MOTOR_LEFT_DRIVE_OFFSET', 200))
RIGHT_DRIVE_OFFSET = int(os.environ.get('MOTOR_RIGHT_DRIVE_OFFSET', 200))
LEFT_TURN_OFFSET   = int(os.environ.get('MOTOR_LEFT_TURN_OFFSET', 200))
RIGHT_TURN_OFFSET  = int(os.environ.get('MOTOR_RIGHT_TURN_OFFSET', 200))

# Left motor mirror-mounted? (calibration UI inversion)
LEFT_MIRROR = os.environ.get('MOTOR_LEFT_MIRROR', '0') == '1'

# Drift bias
DRIFT_BIAS = float(os.environ.get('MOTOR_DRIFT_BIAS', '0.0'))

# Steering inversion: set to 1 to swap which servo drives for +/- correction
STEER_INVERT = os.environ.get('MOTOR_STEER_INVERT', '0') == '1'

# ─── PWM Ramp Constants ───
RAMP_STEP_US = int(os.environ.get('MOTOR_RAMP_STEP_US', 10))
RAMP_STEP_MS = int(os.environ.get('MOTOR_RAMP_STEP_MS', 10))

# ─── Deadband: suppress PWM near neutral to prevent buzz/creep ───
# If |pulse - neutral| < DEADBAND_US → force PWM OFF entirely
DEADBAND_US = int(os.environ.get('MOTOR_DEADBAND_US', 15))

# ─── EMA (Exponential Moving Average) filter for PWM noise reduction ───
# Lower alpha = more smoothing (slower response), higher = less smoothing
# 0.3 is a good balance: filters spikes without adding noticeable lag
EMA_ALPHA = float(os.environ.get('MOTOR_EMA_ALPHA', '0.3'))

# ─── Force-stop: number of retries to ensure PWM is truly killed ───
STOP_RETRIES = int(os.environ.get('MOTOR_STOP_RETRIES', 3))
STOP_RETRY_MS = int(os.environ.get('MOTOR_STOP_RETRY_MS', 20))


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


class HardwarePWMController:
    """
    Dual Servo Motor controller via Pi5 Hardware PWM (sysfs).
    Uses rpi-hardware-pwm for rock-solid, jitter-free PWM output.
    
    dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
      GPIO 12 → pwmchip0/pwm0  (left motor)
      GPIO 13 → pwmchip0/pwm1  (right motor)
    """
    def __init__(self, config):
        motor_cfg = config.get('motor', {})
        self.left_pin = motor_cfg.get('left_pin', LEFT_PIN)
        self.right_pin = motor_cfg.get('right_pin', RIGHT_PIN)

        # Per-motor neutral and offsets (from env vars)
        self.left_neutral = LEFT_NEUTRAL
        self.right_neutral = RIGHT_NEUTRAL
        self.left_stop_point = LEFT_STOP_POINT
        self.right_stop_point = RIGHT_STOP_POINT
        self.left_drive_offset = LEFT_DRIVE_OFFSET
        self.right_drive_offset = RIGHT_DRIVE_OFFSET
        self.left_turn_offset = LEFT_TURN_OFFSET
        self.right_turn_offset = RIGHT_TURN_OFFSET
        self.drift_bias = DRIFT_BIAS

        self.status = 'idle'
        self._pwm_active = False

        # PWM ramp state
        self._current_left_us = self.left_neutral
        self._current_right_us = self.right_neutral
        self._ramp_cancel = threading.Event()
        self._ramp_thread = None

        # EMA filter state (tracks smoothed pulse values)
        self._ema_left_us = float(self.left_neutral)
        self._ema_right_us = float(self.right_neutral)

        # Hardware PWM instances
        self._left_pwm = None
        self._right_pwm = None
        self._hw_ready = False

        self._init_hw_pwm()

    def _init_hw_pwm(self):
        """Initialize Hardware PWM channels via sysfs."""
        try:
            self._left_pwm = HardwarePWM(
                pwm_channel=LEFT_PWM_CHAN,
                hz=PWM_FREQ,
                chip=LEFT_PWM_CHIP,
            )
            self._right_pwm = HardwarePWM(
                pwm_channel=RIGHT_PWM_CHAN,
                hz=PWM_FREQ,
                chip=RIGHT_PWM_CHIP,
            )
            self._hw_ready = True
            logger.info(
                f"✅ HardwarePWM ready: "
                f"L=chip{LEFT_PWM_CHIP}/ch{LEFT_PWM_CHAN}(GPIO{self.left_pin}), "
                f"R=chip{RIGHT_PWM_CHIP}/ch{RIGHT_PWM_CHAN}(GPIO{self.right_pin})"
            )
        except Exception as e:
            logger.error(f"❌ Hardware PWM init failed: {e}")
            self._hw_ready = False

    def _apply_deadband(self, pulse_us, neutral):
        """If pulse is within deadband of neutral, return 0 (stop).
        Prevents servo buzz/creep from tiny PWM offsets near neutral."""
        if abs(pulse_us - neutral) < DEADBAND_US:
            return 0
        return pulse_us

    def _ema_filter(self, target_us, prev_ema, alpha=None):
        """Exponential Moving Average filter.
        Smooths sudden PWM spikes before reaching the servo.
        Returns: new EMA value (float)."""
        if alpha is None:
            alpha = EMA_ALPHA
        return alpha * target_us + (1.0 - alpha) * prev_ema

    def _set_pwm(self, channel, pulse_us):
        """Set PWM pulse width in microseconds on a channel.
        channel: 'left' or 'right'.
        pulse_us=0 → disable PWM on that channel.
        Applies deadband check (skip EMA for single-channel direct set)."""
        pwm_obj = self._left_pwm if channel == 'left' else self._right_pwm
        neutral = self.left_neutral if channel == 'left' else self.right_neutral
        if pwm_obj is None:
            return
        try:
            # Deadband: force off if too close to neutral
            if pulse_us != 0:
                pulse_us = self._apply_deadband(pulse_us, neutral)

            if pulse_us == 0:
                pwm_obj.stop()
                if channel == 'left':
                    self._pwm_active = False  # will be reassessed
                return

            duty = (pulse_us / 20000.0) * 100.0  # µs → duty cycle %
            if not self._pwm_active:
                pwm_obj.start(duty)
            else:
                pwm_obj.change_duty_cycle(duty)
        except Exception as e:
            logger.error(f"HW PWM error ({channel}): {e}")

    def _set_both_pwm(self, left_us, right_us):
        """Set PWM on both channels with deadband enforcement.
        Values within deadband of neutral → that channel is stopped."""
        if not self._hw_ready:
            return

        # Apply deadband independently per channel
        left_db = self._apply_deadband(left_us, self.left_neutral)
        right_db = self._apply_deadband(right_us, self.right_neutral)

        try:
            if left_db == 0 and right_db == 0:
                # Both in deadband → stop both
                self._force_stop_pwm()
                return

            if not self._pwm_active:
                # Starting from stopped: use actual values (or neutral for deadband channels)
                l_start = left_db if left_db != 0 else self.left_neutral
                r_start = right_db if right_db != 0 else self.right_neutral
                self._left_pwm.start((l_start / 20000.0) * 100.0)
                self._right_pwm.start((r_start / 20000.0) * 100.0)
                self._pwm_active = True
            else:
                # Active channels: update duty cycle
                # Deadband channels: FORCE to neutral (do NOT skip!)
                # Skipping leaves the old duty running → servo never stops.
                l_duty = (left_db / 20000.0) * 100.0 if left_db != 0 \
                    else (self.left_neutral / 20000.0) * 100.0
                r_duty = (right_db / 20000.0) * 100.0 if right_db != 0 \
                    else (self.right_neutral / 20000.0) * 100.0
                self._left_pwm.change_duty_cycle(l_duty)
                self._right_pwm.change_duty_cycle(r_duty)
        except Exception as e:
            logger.error(f"HW PWM set_both error: {e}")

    def _ema_set_both(self, left_target_us, right_target_us):
        """Apply EMA filter + deadband, then set PWM on both channels.
        Used for high-frequency paths (forward_steer) to smooth jitter.

        CRITICAL: When a channel targets neutral (stop), snap EMA
        immediately instead of slowly transitioning.  This prevents
        the "stopped" servo from running forward for 8+ frames due
        to EMA lag."""
        # Snap to neutral instantly when target is within deadband
        if abs(left_target_us - self.left_neutral) < DEADBAND_US:
            self._ema_left_us = float(self.left_neutral)
        else:
            self._ema_left_us = self._ema_filter(left_target_us, self._ema_left_us)

        if abs(right_target_us - self.right_neutral) < DEADBAND_US:
            self._ema_right_us = float(self.right_neutral)
        else:
            self._ema_right_us = self._ema_filter(right_target_us, self._ema_right_us)

        filtered_left = int(round(self._ema_left_us))
        filtered_right = int(round(self._ema_right_us))
        self._set_both_pwm(filtered_left, filtered_right)
        self._current_left_us = filtered_left
        self._current_right_us = filtered_right

    def _ensure_pwm_at_neutral(self):
        """Start PWM at neutral if currently stopped."""
        if not self._pwm_active:
            self._set_both_pwm(self.left_neutral, self.right_neutral)
            self._current_left_us = self.left_neutral
            self._current_right_us = self.right_neutral
            time.sleep(0.05)
            logger.debug("HW PWM re-enabled at neutral before ramp")

    # ─── PWM Ramping ───
    def _cancel_ramp(self):
        """Cancel any running ramp thread"""
        self._ramp_cancel.set()
        if self._ramp_thread and self._ramp_thread.is_alive():
            self._ramp_thread.join(timeout=1.0)

    def _do_ramp(self, left_target, right_target, cancel_event=None):
        """Ramp both motors from current to target. 10µs/step, 10ms/step."""
        left_cur = self._current_left_us
        right_cur = self._current_right_us
        left_diff = left_target - left_cur
        right_diff = right_target - right_cur

        # Tiny change → apply directly
        if abs(left_diff) <= RAMP_STEP_US and abs(right_diff) <= RAMP_STEP_US:
            self._set_both_pwm(left_target, right_target)
            self._current_left_us = left_target
            self._current_right_us = right_target
            return

        max_steps = max(
            abs(left_diff) // RAMP_STEP_US,
            abs(right_diff) // RAMP_STEP_US,
            1
        )

        for i in range(1, max_steps + 1):
            if cancel_event and cancel_event.is_set():
                return
            frac = i / max_steps
            l = int(round(left_cur + left_diff * frac))
            r = int(round(right_cur + right_diff * frac))
            self._set_both_pwm(l, r)
            self._current_left_us = l
            self._current_right_us = r
            time.sleep(RAMP_STEP_MS / 1000.0)

        # Ensure exact final values
        self._set_both_pwm(left_target, right_target)
        self._current_left_us = left_target
        self._current_right_us = right_target

    def _ramp_to(self, left_target, right_target):
        """Start non-blocking ramp in background thread."""
        self._cancel_ramp()
        self._ensure_pwm_at_neutral()
        cancel = threading.Event()
        self._ramp_cancel = cancel
        t = threading.Thread(
            target=self._do_ramp,
            args=(left_target, right_target, cancel),
            daemon=True
        )
        t.start()
        self._ramp_thread = t

    def forward(self, speed=50):
        """Both wheels forward with drift compensation."""
        self.status = 'forward'
        left_bias_us = int(self.drift_bias * self.left_drive_offset * 0.3)
        right_bias_us = int(self.drift_bias * self.right_drive_offset * 0.3)
        self._ramp_to(
            self.left_neutral - self.left_drive_offset - left_bias_us,
            self.right_neutral + self.right_drive_offset - right_bias_us,
        )
        logger.info("Forward: ramping to drive speed (drift-compensated)")

    def backward(self, speed=50):
        """Both wheels backward."""
        self.status = 'backward'
        self._ramp_to(
            self.left_neutral + self.left_drive_offset,
            self.right_neutral - self.right_drive_offset,
        )
        logger.info("Backward: ramping to drive speed")

    def turn_left(self, speed=50):
        """Pivot left: left wheel backward, right wheel forward."""
        self.status = 'turning_left'
        self._ramp_to(
            self.left_neutral + self.left_turn_offset,    # left backward (above neutral)
            self.right_neutral + self.right_turn_offset,  # right forward (above neutral)
        )
        logger.info("Turn Left: ramping (pivot)")

    def turn_right(self, speed=50):
        """Pivot right: left wheel forward, right wheel backward."""
        self.status = 'turning_right'
        self._ramp_to(
            self.left_neutral - self.left_turn_offset,    # left forward (below neutral)
            self.right_neutral - self.right_turn_offset,  # right backward (below neutral)
        )
        logger.info("Turn Right: ramping (pivot)")

    def forward_steer(self, correction, speed_factor=1.0):
        """
        Drive forward with DIFFERENTIAL steering.

        Both servos always spin. The correction is split symmetrically:
          - Straight:    both wheels at full forward speed
          - Correction:  one wheel speeds up by half the delta,
                         the other slows down (or reverses) by half.
        This keeps the average speed constant and produces much
        smoother trajectory corrections than stopping one wheel.

        correction in [-1.0 … +1.0]:
          positive → car drifted LEFT  → need to steer RIGHT
          negative → car drifted RIGHT → need to steer LEFT
          ~0       → both servos drive forward (straight)

        Mirror-mounted servos:
          Left (GPIO12):  forward = neutral - offset  (below neutral)
          Right (GPIO13): forward = neutral + offset  (above neutral)
        """
        corrected = correction + self.drift_bias
        corrected = max(-1.0, min(1.0, corrected))

        # Apply steering inversion if configured
        if STEER_INVERT:
            corrected = -corrected

        abs_c = min(abs(corrected), 1.0)
        # Softer power curve for smooth feel
        shaped = abs_c ** 0.8

        # Steering rate limiter: max change per tick (prevents jerks)
        MAX_STEER_RATE = 0.10   # per call (~33Hz → 3.3/s max)
        if hasattr(self, '_last_corrected'):
            delta = corrected - self._last_corrected
            if abs(delta) > MAX_STEER_RATE:
                corrected = self._last_corrected + MAX_STEER_RATE * (1 if delta > 0 else -1)
                abs_c = min(abs(corrected), 1.0)
                shaped = abs_c ** 0.8
        self._last_corrected = corrected

        # Base forward speed for each motor
        base_left = self.left_drive_offset * speed_factor
        base_right = self.right_drive_offset * speed_factor

        # Dead zone: drive straight when correction is tiny
        STEER_DEADZONE = 0.03
        if abs_c < STEER_DEADZONE:
            left_pwm = self.left_neutral - base_left
            right_pwm = self.right_neutral + base_right
            steer_desc = 'straight'
        else:
            # Differential: split correction symmetrically
            # steer_amount 0..1 → how much to shift between wheels
            steer_amount = shaped

            if corrected > 0:
                # Steer RIGHT → left wheel faster, right wheel slower/reverse
                left_speed = base_left * (1.0 + 0.5 * steer_amount)
                right_speed = base_right * (1.0 - steer_amount)
                left_pwm = self.left_neutral - left_speed
                right_pwm = self.right_neutral + right_speed
                steer_desc = f'diff-R L={int(left_pwm)} R={int(right_pwm)}'
            else:
                # Steer LEFT → right wheel faster, left wheel slower/reverse
                right_speed = base_right * (1.0 + 0.5 * steer_amount)
                left_speed = base_left * (1.0 - steer_amount)
                left_pwm = self.left_neutral - left_speed
                right_pwm = self.right_neutral + right_speed
                steer_desc = f'diff-L L={int(left_pwm)} R={int(right_pwm)}'

        self.status = 'forward_steer'
        logger.debug(
            f"forward_steer: corr={correction:+.3f} inv={STEER_INVERT} "
            f"→ corrected={corrected:+.3f} → {steer_desc} "
            f"L={int(left_pwm)}us R={int(right_pwm)}us"
        )
        # Use EMA-filtered path to smooth jitter on high-frequency steering
        self._ema_set_both(int(left_pwm), int(right_pwm))

    def stop(self):
        """Soft-brake: ramp to stop point, then FORCE-KILL PWM with retries.
        This is the critical path — must always succeed to prevent runaway servo."""
        self.status = 'idle'
        self._cancel_ramp()

        # Phase 1: Soft deceleration — ramp down to stop point
        if self._pwm_active:
            self._do_ramp(self.left_stop_point, self.right_stop_point)
            time.sleep(0.03)

        # Phase 2: Force-kill PWM output with retries
        #   Even if ramp didn't finish or state is corrupted, ALWAYS stop PWM
        self._force_stop_pwm()

        # Reset EMA state to neutral (prevents stale values on next start)
        self._ema_left_us = float(self.left_neutral)
        self._ema_right_us = float(self.right_neutral)
        self._current_left_us = self.left_neutral
        self._current_right_us = self.right_neutral
        logger.info("Stop: ramp→stop_point → force-kill PWM (retried)")

    def _force_stop_pwm(self):
        """Aggressively stop all PWM output with retries.
        Prevents runaway servo by retrying .stop() multiple times."""
        for attempt in range(STOP_RETRIES):
            try:
                if self._left_pwm:
                    self._left_pwm.stop()
                if self._right_pwm:
                    self._right_pwm.stop()
            except Exception as e:
                logger.warning(f"PWM force-stop attempt {attempt+1}/{STOP_RETRIES}: {e}")
            if attempt < STOP_RETRIES - 1:
                time.sleep(STOP_RETRY_MS / 1000.0)
        self._pwm_active = False

    def cleanup(self):
        """Stop motors and release PWM resources."""
        self.stop()
        try:
            if self._left_pwm:
                self._left_pwm.stop()
            if self._right_pwm:
                self._right_pwm.stop()
        except Exception:
            pass
        self._left_pwm = None
        self._right_pwm = None
        self._hw_ready = False
        logger.info("Motor HW PWM cleanup done")

    def get_status(self):
        return {
            'driver': 'hardware_pwm',
            'type': 'dual_servo',
            'status': self.status,
            'left_pin': self.left_pin,
            'right_pin': self.right_pin,
            'left_chip_channel': f"chip{LEFT_PWM_CHIP}/ch{LEFT_PWM_CHAN}",
            'right_chip_channel': f"chip{RIGHT_PWM_CHIP}/ch{RIGHT_PWM_CHAN}",
            'hw_ready': self._hw_ready,
        }


def create_motor_controller(config):
    """Factory: create the appropriate motor controller"""
    motor_cfg = config.get('motor', {})
    driver_type = motor_cfg.get('driver_type', 'auto')

    logger.info(f"Motor config: driver={driver_type}, HW_PWM={HW_PWM_AVAILABLE}")

    # Try Hardware PWM first (Pi5 sysfs — jitter-free)
    if HW_PWM_AVAILABLE and driver_type in ('hardware_pwm', 'pwm', 'auto'):
        try:
            ctrl = HardwarePWMController(config)
            if ctrl._hw_ready:
                return ctrl
            else:
                logger.warning("HardwarePWM created but not ready")
        except Exception as e:
            logger.warning(f"HardwarePWM init failed: {e}")

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

    Calibration constants (all from env vars — tweak in hardware.env):
      NAV_DRIVE_TIME_PER_COORD  – seconds per map coordinate unit
      NAV_TURN_90_TIME          – seconds for a 90° pivot
      NAV_TURN_CORRECTION       – turn overshoot multiplier
      NAV_DRIFT_CORRECTION_TIME – counter-steer pulse after turn
      NAV_POSITION_REPORT_INTERVAL – progress report cadence
      NAV_LINE_FOLLOW_INTERVAL  – steering update rate (lower = more frequent)
    """

    # ── Calibration (ALL from env vars for live tuning) ──
    DRIVE_TIME_PER_COORD  = float(os.environ.get('NAV_DRIVE_TIME_PER_COORD', '0.002'))
    TURN_90_TIME          = float(os.environ.get('NAV_TURN_90_TIME', '1.60'))
    TURN_CORRECTION       = float(os.environ.get('NAV_TURN_CORRECTION', '1.05'))
    DRIFT_CORRECTION_TIME = float(os.environ.get('NAV_DRIFT_CORRECTION_TIME', '0.10'))
    POSITION_REPORT_INTERVAL = float(os.environ.get('NAV_POSITION_REPORT_INTERVAL', '0.40'))

    # ── Line-following with continuous single-servo steering ──
    LINE_FOLLOW_INTERVAL  = float(os.environ.get('NAV_LINE_FOLLOW_INTERVAL', '0.03'))
    LINE_FOLLOW_THRESHOLD = 0.0      # always steer — forward_steer handles deadzone internally

    # Stuck detection: if correction stays same sign for too many consecutive frames
    STUCK_CONSECUTIVE_LIMIT = 12     # ~1.2s at 0.10 interval
    STUCK_MIN_MAGNITUDE     = 0.4    # only count as "stuck" if correction > this

    def __init__(self, motor_controller, camera_getter=None, line_follower=None, odometry=None):
        self.motor = motor_controller
        self.heading = None          # current (dx, dy) unit-direction vector
        self.navigating = False
        self._nav_task = None
        self._get_camera_frame = camera_getter   # async () → bytes|None
        self._line_follower = line_follower       # LineFollower instance
        self._odometry = odometry                 # OdometryTracker instance

        # PD controller state
        self._prev_correction = 0.0
        self._stuck_counter = 0
        self._stuck_sign = 0

        logger.info("AutoNavigator initialised (line-follow=%s, odom=%s, drive_time_per_coord=%.4f, turn_90=%.2fs)",
                    'enabled' if line_follower and line_follower.is_ready else 'disabled',
                    'enabled' if odometry else 'disabled',
                    self.DRIVE_TIME_PER_COORD, self.TURN_90_TIME)

    # ── Public API ──────────────────────────────────────────

    def set_heading(self, heading):
        """Set the vehicle heading (e.g. restored from server after restart).
        heading: [dx, dy] list/tuple or None."""
        if heading and len(heading) == 2:
            self.heading = (int(heading[0]), int(heading[1]))
            logger.info(f"AutoNavigator: heading set to {self.heading}")
        else:
            logger.info(f"AutoNavigator: heading not set (value={heading})")

    async def navigate_path(self, path, emit_cb):
        """
        Navigate *path* (list of dicts with pointId, x, y).
        *emit_cb(event, data)* is an async callable used for real-time reports.
        """
        if len(path) < 2:
            logger.warning("Path too short, nothing to navigate")
            return

        self.navigating = True
        # NOTE: Do NOT reset self.heading here.
        # The heading persists between navigations so the vehicle
        # knows its current facing direction and can U-turn if the
        # next path starts in the opposite direction.
        start_time = time.time()
        point_ids = [p.get('pointId', '?') for p in path]

        logger.info(f"▶ AUTO-NAV START  path={point_ids}  initial_heading={self.heading}")
        await emit_cb('navigation-log', {
            'type': 'start',
            'pointId': path[0]['pointId'],
            'x': path[0]['x'], 'y': path[0]['y'],
            'heading': list(self.heading) if self.heading else None,
            'timestamp': start_time,
            'route': point_ids,
        })

        try:
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
                drive_sec = dist * self.DRIVE_TIME_PER_COORD
                logger.info(
                    f"  ➜ {cur['pointId']} → {nxt['pointId']}  "
                    f"dist={dist:.0f}px  drive={drive_sec:.2f}s  heading={self.heading}"
                )

                # Reset line-follower EMA state for new segment
                if self._line_follower and hasattr(self._line_follower, 'reset'):
                    self._line_follower.reset()
                if self._odometry and i == 0:
                    self._odometry.reset()
                self._prev_correction = 0.0
                self._stuck_counter = 0
                self._stuck_sign = 0

                # Drive with CONTINUOUS steering from tick 0.
                # Never call motor.forward() in the loop — always forward_steer()
                # so the car corrects every single servo rotation cycle.
                # forward_steer(0) drives both wheels straight (deadzone handles it).
                self.motor.forward_steer(0.0)

                segment_start = time.time()
                last_report = segment_start
                while (time.time() - segment_start) < drive_sec and self.navigating:
                    loop_start = time.time()

                    # ── Camera → CTE → PID → Steer every tick ──
                    correction, vision_cte, vision_conf = await self._get_line_correction_full()
                    steer = max(-1.0, min(1.0, correction))

                    # Apply single-servo steering EVERY tick
                    self.motor.forward_steer(steer)

                    # ── Odometry update ──
                    if self._odometry:
                        odom_entry = self._odometry.update(
                            steering=steer,
                            vision_cte=vision_cte,
                            vision_confidence=vision_conf,
                            is_driving=True,
                        )
                        # Emit odom log at report cadence
                        if (time.time() - last_report) >= self.POSITION_REPORT_INTERVAL:
                            await emit_cb('odom-log', odom_entry)

                    # Stuck detection
                    corr_sign = 1 if correction > 0 else -1
                    if (corr_sign == self._stuck_sign and
                            abs(correction) >= self.STUCK_MIN_MAGNITUDE):
                        self._stuck_counter += 1
                    else:
                        self._stuck_counter = 0
                        self._stuck_sign = corr_sign

                    if self._stuck_counter >= self.STUCK_CONSECUTIVE_LIMIT:
                        logger.warning(f"  ⚠ STUCK detected ({self._stuck_counter} frames), "
                                       f"aggressive reverse steer")
                        self.motor.stop()
                        await asyncio.sleep(0.1)
                        if correction > 0:
                            self.motor.turn_left()
                        else:
                            self.motor.turn_right()
                        await asyncio.sleep(0.25)
                        self.motor.forward_steer(0.0)
                        self._stuck_counter = 0
                        await emit_cb('navigation-log', {
                            'type': 'stuck-recovery',
                            'correction': round(correction, 3),
                            'timestamp': time.time(),
                        })

                    if abs(correction) > 0.03:
                        logger.debug(f"    🔧 steer {steer:+.3f} (correction={correction:+.3f})")

                    self._prev_correction = correction

                    # ── Position report at POSITION_REPORT_INTERVAL cadence ──
                    elapsed = time.time() - segment_start
                    progress = min(elapsed / drive_sec, 1.0)
                    if (time.time() - last_report) >= self.POSITION_REPORT_INTERVAL:
                        last_report = time.time()
                        ix = cur['x'] + dx * progress
                        iy = cur['y'] + dy * progress
                        await emit_cb('navigation-log', {
                            'type': 'moving',
                            'x': round(ix, 1),
                            'y': round(iy, 1),
                            'fromPoint': cur['pointId'],
                            'toPoint': nxt['pointId'],
                            'progress': round(progress * 100),
                            'steer': round(steer, 3),
                            'timestamp': time.time(),
                        })

                    # Sleep remainder of interval
                    loop_elapsed = time.time() - loop_start
                    sleep_time = self.LINE_FOLLOW_INTERVAL - loop_elapsed
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)

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
                logger.info(f"  ✔ Reached {nxt['pointId']} ({nxt['x']},{nxt['y']}) heading={self.heading}")
                await emit_cb('navigation-log', {
                    'type': 'waypoint',
                    'pointId': nxt['pointId'],
                    'x': nxt['x'], 'y': nxt['y'],
                    'heading': list(self.heading) if self.heading else None,
                    'timestamp': time.time(),
                })
                await emit_cb('vehicle-position-update', {'pointId': nxt['pointId']})

        finally:
            # ALWAYS stop motor — prevents servo spinning forever on any error
            self.motor.stop()
            self.navigating = False

        # ── Done ──
        end_time = time.time()
        duration = round(end_time - start_time, 2)
        heading_list = list(self.heading) if self.heading else None
        logger.info(f"■ AUTO-NAV COMPLETE  duration={duration}s  heading={self.heading}")
        await emit_cb('navigation-log', {
            'type': 'complete',
            'pointId': path[-1]['pointId'],
            'x': path[-1]['x'], 'y': path[-1]['y'],
            'heading': heading_list,
            'startTime': start_time,
            'endTime': end_time,
            'duration': duration,
            'timestamp': end_time,
        })

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

        # Update odometry heading after turn
        if self._odometry:
            self._odometry.handle_turn(direction)

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

    async def _get_line_correction_full(self):
        """
        Get steering + vision CTE + confidence for odometry fusion.
        Returns (steering, vision_cte, confidence).
        """
        if self._line_follower is None or not self._line_follower.is_ready:
            return 0.0, None, 0.0
        if self._get_camera_frame is None:
            return 0.0, None, 0.0
        try:
            frame = await self._get_camera_frame()
            if frame is None:
                return 0.0, None, 0.0
            result = self._line_follower.analyse_frame_full(frame)
            return result['steering'], result['vision_cte'], result['confidence']
        except Exception as e:
            logger.debug(f"Line-follow-full error: {e}")
            return 0.0, None, 0.0
