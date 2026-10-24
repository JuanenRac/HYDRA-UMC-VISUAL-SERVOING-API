# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/session.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A servoing session: the stateful loop around the correction law of servo.py.

`compute_velocity_command` is a function of one error. A closed loop needs memory:

  * a nominal period `T` and a gain checked against it. The loop is a discrete integrator, so `K*T` has to stay under 2 to converge at all
    and under 1 to converge without overshoot; a session refuses a gain whose `K*T` is above `max_gain_period` (0.5 by default);
  * a filter on the measured pose (an exponential average; angles are averaged along the shortest turn), so one noisy detection does not
    become a jerk of the tool;
  * a dead band with hysteresis: inside `tolerance` the session commands zero and stays at zero until the error grows past
    `tolerance * hysteresis`, so a pose that hovers around the edge does not make the tool chatter;
  * a limit on acceleration: the command may not change by more than `max_acceleration * dt` between two updates, and the first command
    after a stop starts from rest;
  * a vision timeout: when no update has come for `vision_timeout_s`, the command is zero, whatever the last one was.

The clock is a parameter (`now_s`), so a test can run a whole approach in no time. Everything here is bounded by the limits the service
was started with (`ServoLimits`): a session can ask for less than they allow, never for more.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .pose import Pose6D
from .servo import PoseError, VelocityCommand, compute_pose_error, compute_velocity_command

ZERO = VelocityCommand(vx=0.0, vy=0.0, vz=0.0, wroll=0.0, wpitch=0.0, wyaw=0.0)

DEFAULT_PERIOD_S = 0.1
DEFAULT_MAX_GAIN_PERIOD = 0.5  # K*T; 1 is the edge of overshoot, 2 the edge of divergence
DEFAULT_FILTER_ALPHA = 0.5
DEFAULT_HYSTERESIS = 2.0
DEFAULT_VISION_TIMEOUT_S = 0.5
DEFAULT_MAX_LINEAR_ACCELERATION = 0.5  # m/s^2
DEFAULT_MAX_ANGULAR_ACCELERATION = 1.0  # rad/s^2
DEFAULT_LINEAR_TOLERANCE = 0.001  # m
DEFAULT_ANGULAR_TOLERANCE = 0.01  # rad


def _positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class SessionConfig:
    gain: float
    max_linear_speed: float
    max_angular_speed: float
    period_s: float = DEFAULT_PERIOD_S
    max_gain_period: float = DEFAULT_MAX_GAIN_PERIOD
    filter_alpha: float = DEFAULT_FILTER_ALPHA
    hysteresis: float = DEFAULT_HYSTERESIS
    vision_timeout_s: float = DEFAULT_VISION_TIMEOUT_S
    max_linear_acceleration: float = DEFAULT_MAX_LINEAR_ACCELERATION
    max_angular_acceleration: float = DEFAULT_MAX_ANGULAR_ACCELERATION
    linear_tolerance: float = DEFAULT_LINEAR_TOLERANCE
    angular_tolerance: float = DEFAULT_ANGULAR_TOLERANCE

    def __post_init__(self) -> None:
        for name in ("gain", "max_linear_speed", "max_angular_speed", "period_s", "max_gain_period", "vision_timeout_s",
                     "max_linear_acceleration", "max_angular_acceleration", "linear_tolerance", "angular_tolerance"):
            _positive(getattr(self, name), name)
        if not (isinstance(self.filter_alpha, (int, float)) and not isinstance(self.filter_alpha, bool) and 0.0 < self.filter_alpha <= 1.0):
            raise ValueError(f"filter_alpha must be in (0, 1], got {self.filter_alpha!r}")
        if not (isinstance(self.hysteresis, (int, float)) and not isinstance(self.hysteresis, bool) and math.isfinite(self.hysteresis) and self.hysteresis >= 1.0):
            raise ValueError(f"hysteresis must be at least 1, got {self.hysteresis!r}")
        if self.gain * self.period_s > self.max_gain_period:
            raise ValueError(
                f"gain {self.gain} with a period of {self.period_s} s makes K*T = {self.gain * self.period_s:.3f}, above {self.max_gain_period}: "
                "the loop would overshoot or diverge (it needs K*T below 1 to converge without overshoot and below 2 to converge at all)"
            )
        if self.vision_timeout_s < self.period_s:
            raise ValueError("vision_timeout_s must not be shorter than one period")


@dataclass(frozen=True)
class SessionStep:
    command: VelocityCommand
    error: PoseError | None
    converged: bool
    # "tracking", "holding" (inside the dead band), "restarted" (first update, or after a pause: starts from rest) or "vision_lost"
    state: str
    filtered: Pose6D | None


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _limit_change(previous: tuple[float, ...], wanted: tuple[float, ...], max_delta: float) -> tuple[float, ...]:
    """The vector closest to `wanted` that is no further than `max_delta` from `previous` (the whole change scaled together)."""
    delta = tuple(w - p for w, p in zip(wanted, previous, strict=True))
    size = math.hypot(*delta)
    if size <= max_delta or size == 0.0:
        return wanted
    scale = max_delta / size
    return tuple(p + d * scale for p, d in zip(previous, delta, strict=True))


class ServoSession:
    def __init__(self, config: SessionConfig) -> None:
        self.config = config
        self._filtered: Pose6D | None = None
        self._last_time: float | None = None
        self._last: VelocityCommand = ZERO
        self._holding = False
        self.updates = 0

    # -- the measured pose, filtered -------------------------------------------------------------------------------------------------
    def _filter(self, measured: Pose6D) -> Pose6D:
        previous = self._filtered
        if previous is None:
            return measured
        a = self.config.filter_alpha
        return Pose6D(
            x=previous.x + a * (measured.x - previous.x),
            y=previous.y + a * (measured.y - previous.y),
            z=previous.z + a * (measured.z - previous.z),
            roll=_wrap(previous.roll + a * _wrap(measured.roll - previous.roll)),
            pitch=_wrap(previous.pitch + a * _wrap(measured.pitch - previous.pitch)),
            yaw=_wrap(previous.yaw + a * _wrap(measured.yaw - previous.yaw)),
        )

    def reset(self) -> None:
        """Forget the filter and the last command: the next update starts from rest."""
        self._filtered = None
        self._last_time = None
        self._last = ZERO
        self._holding = False

    # -- the loop --------------------------------------------------------------------------------------------------------------------
    def tick(self, now_s: float) -> VelocityCommand:
        """The command to apply right now, without a new measurement: the last one while it is still fresh, zero when vision was lost.
        A consumer calls this between detections."""
        if self._last_time is None or now_s - self._last_time > self.config.vision_timeout_s:
            return ZERO
        return self._last

    def update(self, current: Pose6D, target: Pose6D, now_s: float) -> SessionStep:
        cfg = self.config
        if not math.isfinite(now_s):
            raise ValueError("now_s must be finite")
        if self._last_time is not None and now_s < self._last_time:
            raise ValueError("time went backwards")
        restarted = self._last_time is None or now_s - self._last_time > cfg.vision_timeout_s
        if restarted:
            # a pause longer than the timeout: the old filter and the old command say nothing about the tool now
            self._filtered = None
            self._last = ZERO
            self._holding = False
        dt = cfg.period_s if restarted else max(now_s - self._last_time, 1e-6)  # type: ignore[operator]

        self._filtered = self._filter(current)
        error = compute_pose_error(self._filtered, target)
        linear, angular = error.linear_norm, error.angular_norm

        inside = linear <= cfg.linear_tolerance and angular <= cfg.angular_tolerance
        outside = linear > cfg.linear_tolerance * cfg.hysteresis or angular > cfg.angular_tolerance * cfg.hysteresis
        if inside:
            self._holding = True
        elif outside:
            self._holding = False
        self._last_time = now_s
        self.updates += 1

        if self._holding:
            self._last = ZERO
            return SessionStep(ZERO, error, inside, "holding", self._filtered)

        wanted = compute_velocity_command(error, cfg.gain, cfg.max_linear_speed, cfg.max_angular_speed)
        linear_cmd = _limit_change(
            (self._last.vx, self._last.vy, self._last.vz), (wanted.vx, wanted.vy, wanted.vz), cfg.max_linear_acceleration * dt
        )
        angular_cmd = _limit_change(
            (self._last.wroll, self._last.wpitch, self._last.wyaw), (wanted.wroll, wanted.wpitch, wanted.wyaw), cfg.max_angular_acceleration * dt
        )
        command = VelocityCommand(*linear_cmd, *angular_cmd)
        self._last = command
        return SessionStep(command, error, False, "restarted" if restarted else "tracking", self._filtered)

    def vision_lost(self, now_s: float) -> SessionStep:
        """Nothing was detected: the command is zero, and the next detection starts from rest."""
        self._last = ZERO
        self._last_time = None
        self._filtered = None
        self._holding = False
        return SessionStep(ZERO, None, False, "vision_lost", None)
