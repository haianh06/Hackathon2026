#!/usr/bin/env python3
"""
Odometry Tracker — Bicycle Kinematic Model + Sensor Fusion

Builds a 2D coordinate system (Oxy) starting from the car's initial position.
The car starts at (0, 0) with heading θ=0 (forward = +Y axis).
The initial lane center detected by camera becomes the Oy axis.

Core equations (Bicycle Kinematic Model):
    x_new = x_old + Δs · sin(θ)
    y_new = y_old + Δs · cos(θ)
    θ_new = θ_old + (Δs / L) · tan(δ)

Where:
    Δs = distance traveled (from time × speed estimate)
    L  = wheelbase (axle-to-axle distance)
    δ  = steering angle (derived from PID correction)

CTE on 2D Plane:
    Straight: CTE = x_car  (distance from Oy axis)
    After turn: CTE = (A·x + B·y + C) / sqrt(A² + B²)
                where Ax + By + C = 0 is the target line equation

Sensor Fusion:
    Camera sees line → use vision CTE, correct odometry pose
    Camera lost line → use odometry-predicted CTE for up to N frames

All parameters are read from ODO_* environment variables.
"""

import logging
import math
import os
import time

import numpy as np

logger = logging.getLogger('odometry')


def _env_float(key, default):
    return float(os.environ.get(key, default))


def _env_int(key, default):
    return int(os.environ.get(key, default))


class TargetLine:
    """Represents a target line as Ax + By + C = 0 and a direction angle."""

    __slots__ = ('A', 'B', 'C', 'angle')

    def __init__(self, angle_rad):
        """Create target line through origin with given direction.
        angle_rad: heading angle in radians (0 = +Y axis)."""
        self.angle = angle_rad
        # Line perpendicular to direction through (0,0):
        # Normal vector to the line = (cos(angle), -sin(angle)) in our coord system
        # But target line IS the direction of travel, so we need the
        # equation of the line ALONG the direction.
        # Direction vector: (sin(θ), cos(θ))
        # Normal to this line: (cos(θ), -sin(θ))
        # Line through (0,0): cos(θ)·x - sin(θ)·y = 0
        self.A = math.cos(angle_rad)
        self.B = -math.sin(angle_rad)
        self.C = 0.0

    @classmethod
    def from_point_and_angle(cls, px, py, angle_rad):
        """Create target line through (px, py) with given direction."""
        obj = cls.__new__(cls)
        obj.angle = angle_rad
        obj.A = math.cos(angle_rad)
        obj.B = -math.sin(angle_rad)
        obj.C = -(obj.A * px + obj.B * py)
        return obj

    def signed_distance(self, x, y):
        """Signed distance from point to line.
        Positive = point is to the RIGHT of the line direction.
        Negative = point is to the LEFT."""
        return (self.A * x + self.B * y + self.C) / math.sqrt(self.A ** 2 + self.B ** 2)


class OdometryTracker:
    """
    2D pose tracker with sensor fusion between vision and dead reckoning.

    State: (x, y, θ) in world coordinates.
    x = lateral position (positive = right of start)
    y = forward position (positive = ahead of start)
    θ = heading angle in radians (0 = +Y, π/2 = +X, i.e. standard navigation)

    The car starts at (0, 0, 0) — on the Oy axis, facing forward.
    """

    def __init__(self):
        # ── Pose state ──
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0  # radians, 0 = +Y direction

        # ── Vehicle geometry ──
        self.wheelbase = _env_float('ODO_WHEELBASE', 0.12)  # metres
        self.speed_estimate = _env_float('ODO_SPEED_MPS', 0.15)  # m/s when driving
        self.steer_to_angle_gain = _env_float('ODO_STEER_TO_ANGLE', 0.45)  # correction→radians

        # ── Sensor fusion weights ──
        self.vision_weight = _env_float('ODO_VISION_WEIGHT', 0.7)
        self.odom_weight = _env_float('ODO_ODOM_WEIGHT', 0.3)
        self.correction_alpha = _env_float('ODO_CORRECTION_ALPHA', 0.3)  # how much vision corrects pose

        # ── Target line ──
        self._target_line = TargetLine(0.0)  # initially: Oy axis (x = 0)

        # ── History log (ring buffer) ──
        self._max_history = _env_int('ODO_MAX_HISTORY', 500)
        self._history = []

        # ── Timing ──
        self._last_update_time = None
        self._total_distance = 0.0

        # ── Source tracking ──
        self._vision_frames = 0
        self._odom_frames = 0
        self._fused_cte = 0.0

        # ── Session counter ──
        self._session_id = 0

        logger.info(
            "OdometryTracker ready (wheelbase=%.3fm speed=%.3fm/s "
            "steer_gain=%.3f vision_w=%.2f)",
            self.wheelbase, self.speed_estimate,
            self.steer_to_angle_gain, self.vision_weight,
        )

    # ─────────────────────────────────────────────────────────
    # Session management
    # ─────────────────────────────────────────────────────────
    def reset(self):
        """Reset to origin — call at start of new navigation segment."""
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self._target_line = TargetLine(0.0)
        self._history.clear()
        self._last_update_time = None
        self._total_distance = 0.0
        self._vision_frames = 0
        self._odom_frames = 0
        self._fused_cte = 0.0
        self._session_id += 1
        logger.info("Odometry reset — session #%d", self._session_id)

    # ─────────────────────────────────────────────────────────
    # Core update — called every servo tick
    # ─────────────────────────────────────────────────────────
    def update(self, steering, vision_cte=None, vision_confidence=0.0, is_driving=True):
        """
        Update pose estimate.

        Args:
            steering: current PID output [-1, +1]
            vision_cte: CTE from camera (None if camera lost line)
            vision_confidence: 0.0–1.0 how much to trust vision
            is_driving: True if car is moving forward

        Returns:
            dict with full state snapshot for logging
        """
        now = time.time()
        dt = 0.0
        if self._last_update_time is not None:
            dt = min(now - self._last_update_time, 0.5)  # cap at 500ms
        self._last_update_time = now

        # ── Step 1: Dead reckoning (Bicycle Kinematic Model) ──
        ds = 0.0
        if is_driving and dt > 0:
            ds = self.speed_estimate * dt
            self._total_distance += ds

            # Steering angle from PID correction
            delta = steering * self.steer_to_angle_gain

            # Update heading
            if abs(delta) > 0.001:
                d_theta = (ds / self.wheelbase) * math.tan(delta)
            else:
                d_theta = 0.0

            # Update position
            # Use mid-point heading for better accuracy
            mid_theta = self.theta + d_theta / 2.0
            self.x += ds * math.sin(mid_theta)
            self.y += ds * math.cos(mid_theta)
            self.theta += d_theta

            # Normalize theta to [-π, π]
            self.theta = math.atan2(math.sin(self.theta), math.cos(self.theta))

        # ── Step 2: Compute odometry-based CTE ──
        odom_cte = self._target_line.signed_distance(self.x, self.y)

        # ── Step 3: Sensor fusion ──
        source = 'ODOM'
        if vision_cte is not None and vision_confidence > 0.1:
            # Vision available — fuse CTE estimates
            # Convert vision CTE (normalized) to metres for comparison
            # Vision CTE is already in [-1.5, 1.5] normalized range
            # We keep everything in the same normalized scale

            alpha = self.vision_weight * vision_confidence
            beta = 1.0 - alpha
            self._fused_cte = alpha * vision_cte + beta * odom_cte

            # ── Correct odometry pose using vision ──
            # Vision tells us the true lateral offset — use it to fix drift
            correction = self.correction_alpha * vision_confidence
            # Adjust x to reduce accumulated error
            error = vision_cte - odom_cte
            self.x += correction * error * 0.05  # small pose correction in metres

            self._vision_frames += 1
            source = 'VISION'
        else:
            # No vision — pure odometry prediction
            self._fused_cte = odom_cte
            self._odom_frames += 1
            source = 'ODOM'

        # ── Step 4: Record history ──
        entry = {
            't': round(now, 3),
            'x': round(self.x, 4),
            'y': round(self.y, 4),
            'theta': round(math.degrees(self.theta), 1),
            'cte': round(self._fused_cte, 4),
            'odom_cte': round(odom_cte, 4),
            'vision_cte': round(vision_cte, 4) if vision_cte is not None else None,
            'steering': round(steering, 4),
            'ds': round(ds, 4),
            'dist': round(self._total_distance, 3),
            'source': source,
            'confidence': round(vision_confidence, 2),
            'session': self._session_id,
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return entry

    # ─────────────────────────────────────────────────────────
    # Turn handling — update target line after a turn
    # ─────────────────────────────────────────────────────────
    def handle_turn(self, direction):
        """
        Called when the car executes a 90° turn.
        Updates θ and target line to reflect new heading.

        direction: 'left' or 'right'
        """
        if direction == 'left':
            turn_angle = -math.pi / 2  # -90°
        elif direction == 'right':
            turn_angle = math.pi / 2   # +90°
        elif direction == 'uturn':
            turn_angle = math.pi       # 180°
        else:
            return

        self.theta += turn_angle
        self.theta = math.atan2(math.sin(self.theta), math.cos(self.theta))

        # New target line through current position with new heading
        self._target_line = TargetLine.from_point_and_angle(
            self.x, self.y, self.theta
        )

        logger.info(
            "Turn %s: θ=%.1f° new target line through (%.3f, %.3f)",
            direction, math.degrees(self.theta), self.x, self.y,
        )

    # ─────────────────────────────────────────────────────────
    # Accessors
    # ─────────────────────────────────────────────────────────
    @property
    def fused_cte(self):
        return self._fused_cte

    @property
    def pose(self):
        return {
            'x': round(self.x, 4),
            'y': round(self.y, 4),
            'theta': round(math.degrees(self.theta), 1),
        }

    @property
    def total_distance(self):
        return self._total_distance

    @property
    def stats(self):
        return {
            'vision_frames': self._vision_frames,
            'odom_frames': self._odom_frames,
            'total_distance': round(self._total_distance, 3),
            'session': self._session_id,
        }

    def get_recent_history(self, n=50):
        """Return last N history entries."""
        return self._history[-n:]
