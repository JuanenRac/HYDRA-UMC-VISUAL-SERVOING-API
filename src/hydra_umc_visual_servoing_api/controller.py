# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/controller.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A servoing session with state: the closed loop around the proportional law of servo.py.

`compute_velocity_command` is a function of one error. A loop needs more than that to be safe: a nominal period and a
gain checked against it, a filter on the measured error, a dead band with hysteresis so the command does not chatter
around the target, a limit on how fast the command may change, and a vision-loss timeout that forces zero. The clock is
passed in (`now_s`), so the controller has no hidden time source and every behaviour is testable step by step.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .servo import PoseError, VelocityCommand, compute_velocity_command

MAX_GAIN_TIMES_PERIOD = 0.5  # K * T above this makes the discrete loop overshoot or oscillate


def _finite_positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class ControllerConfig:
    gain: float
    period_s: float
    max_linear_speed: float
    max_angular_speed: float
    # Largest change of the command per second: linear in m/s^2, angular in rad/s^2.
    max_linear_accel: float
    max_angular_accel: float
    # 1.0 = no filter; smaller = smoother (exponential moving average of the pose error).
    filter_alpha: float = 0.5
    # Dead band with hysteresis: the command is zero once the error is within *_in, and the loop restarts only when
    # the error grows past *_out (which must be at least *_in).
    linear_dead_in: float = 0.0
    linear_dead_out: float = 0.0
    angular_dead_in: float = 0.0
    angular_dead_out: float = 0.0
    # No valid measurement for this long forces a zero command.
    vision_timeout_s: float = 0.5
    # K * period_s must not exceed this (see MAX_GAIN_TIMES_PERIOD).
    max_gain_times_period: float = MAX_GAIN_TIMES_PERIOD

    def __post_init__(self) -> None:
        for name in ("gain", "period_s", "max_linear_speed", "max_angular_speed", "max_linear_accel",
                     "max_angular_accel", "vision_timeout_s", "max_gain_times_period"):
            _finite_positive(getattr(self, name), name)
        if isinstance(self.filter_alpha, bool) or not isinstance(self.filter_alpha, (int, float)) or not (
            math.isfinite(self.filter_alpha) and 0.0 < self.filter_alpha <= 1.0
        ):
            raise ValueError(f"filter_alpha must be in (0, 1], got {self.filter_alpha!r}")
        for name in ("linear_dead_in", "linear_dead_out", "angular_dead_in", "angular_dead_out"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number of at least 0, got {value!r}")
        if self.linear_dead_out < self.linear_dead_in or self.angular_dead_out < self.angular_dead_in:
            raise ValueError("a dead band must leave at a value at least as large as the one it enters at")
        if self.gain * self.period_s > self.max_gain_times_period:
            raise ValueError(
                f"gain x period = {self.gain * self.period_s:.3f} is above {self.max_gain_times_period}: "
                "lower the gain or lengthen the period"
            )


_ZERO = VelocityCommand(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _limit_step(previous: tuple[float, ...], wanted: tuple[float, ...], max_delta: float) -> tuple[float, ...]:
    """Move from `previous` towards `wanted` by at most `max_delta` (Euclidean), keeping the direction of the change."""
    delta = tuple(w - p for w, p in zip(wanted, previous))
    norm = math.hypot(*delta)
    if norm <= max_delta or norm == 0.0:
        return wanted
    scale = max_delta / norm
    return tuple(p + d * scale for p, d in zip(previous, delta))


class ServoController:
    """One servoing session. Call `step` once per control period with the newest error (or None when vision gave
    nothing) and the current time in seconds; apply the returned command for at most the period."""

    def __init__(self, config: ControllerConfig) -> None:
        self.config = config
        self._filtered: tuple[float, ...] | None = None
        self._last_seen_s: float | None = None
        self._last_step_s: float | None = None
        self._last = _ZERO
        self._in_dead_band = False

    @property
    def last_command(self) -> VelocityCommand:
        return self._last

    def reset(self) -> None:
        """Forget the filter, the ramp and the dead band state (a new session, or after an operator stop)."""
        self._filtered = None
        self._last_seen_s = None
        self._last_step_s = None
        self._last = _ZERO
        self._in_dead_band = False

    def _force_zero(self) -> VelocityCommand:
        self._last = _ZERO
        self._filtered = None
        self._in_dead_band = False
        return _ZERO

    def step(self, error: PoseError | None, now_s: float) -> VelocityCommand:
        cfg = self.config
        if isinstance(now_s, bool) or not isinstance(now_s, (int, float)) or not math.isfinite(now_s):
            raise ValueError("now_s must be a finite number of seconds")
        if self._last_step_s is not None and now_s < self._last_step_s:
            # a clock that went back cannot be trusted to time the ramp or the timeout
            self._last_step_s = now_s
            self._last_seen_s = None
            return self._force_zero()
        dt = cfg.period_s if self._last_step_s is None else min(max(now_s - self._last_step_s, 0.0), cfg.period_s * 4)
        self._last_step_s = now_s

        if error is not None:
            raw = (error.dx, error.dy, error.dz, error.droll, error.dpitch, error.dyaw)
            if not all(math.isfinite(v) for v in raw):
                self._last_seen_s = None
                return self._force_zero()
            self._last_seen_s = now_s
            if self._filtered is None:
                self._filtered = raw
            else:
                a = cfg.filter_alpha
                self._filtered = tuple(a * r + (1.0 - a) * f for r, f in zip(raw, self._filtered))
        if self._last_seen_s is None or now_s - self._last_seen_s > cfg.vision_timeout_s or self._filtered is None:
            return self._force_zero()

        filtered_error = PoseError(*self._filtered)
        lin, ang = filtered_error.linear_norm, filtered_error.angular_norm
        if self._in_dead_band:
            if lin > cfg.linear_dead_out or ang > cfg.angular_dead_out:
                self._in_dead_band = False
        elif lin <= cfg.linear_dead_in and ang <= cfg.angular_dead_in:
            self._in_dead_band = True

        if self._in_dead_band:
            target = _ZERO
        else:
            target = compute_velocity_command(filtered_error, cfg.gain, cfg.max_linear_speed, cfg.max_angular_speed)

        prev = self._last
        linear = _limit_step((prev.vx, prev.vy, prev.vz), (target.vx, target.vy, target.vz), cfg.max_linear_accel * dt)
        angular = _limit_step(
            (prev.wroll, prev.wpitch, prev.wyaw), (target.wroll, target.wpitch, target.wyaw), cfg.max_angular_accel * dt
        )
        self._last = VelocityCommand(*linear, *angular)
        return self._last
