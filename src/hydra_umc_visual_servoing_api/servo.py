# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/servo.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Position-Based Visual Servoing (PBVS) correction law.

This is the "Error Calculation (Pose Delta)" step of the visual servoing
loop in README.md - it takes a current and a desired 6-DOF pose (which a
real deployment would source from the Hailo-8 pose estimator, out of
scope here since it needs the physical NPU) and turns their difference
into a bounded velocity command for the HYDRA-UMC core to actuate.
Pure control-theory math: no camera, no NPU, no serial link required to
compute or test it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .pose import Pose6D


def _require_positive_finite(value: float, label: str) -> None:
    """Reject a bound that could create a reversed or non-finite command."""
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{label} must be a finite positive value, got {value}")


def _require_all_finite(components: tuple[float, ...], label: str) -> None:
    """SERVO-02 (P1): checking that an INPUT is finite (isfinite on x/y/z/roll/pitch/
    yaw, already done in Pose6D.parse) does not guarantee a COMPUTED
    result stays finite - two representable-but-extreme finite poses can
    subtract to inf (float overflow, silent - unlike float ** 2, which
    Python raises OverflowError for instead), and inf * 0.0 from a
    subsequent clamp-scale division is NaN. Every real output this
    module hands back to a caller must be checked explicitly, not
    assumed."""
    if not all(math.isfinite(c) for c in components):
        raise ValueError(f"{label} is not finite ({components}) - inputs are too extreme to represent a real command")


def _wrap_angle_diff(a: float, b: float) -> float:
    """Shortest signed angular distance a - b, wrapped to (-pi, pi]."""
    return math.atan2(math.sin(a - b), math.cos(a - b))


@dataclass(frozen=True)
class PoseError:
    dx: float
    dy: float
    dz: float
    droll: float
    dpitch: float
    dyaw: float

    @property
    def linear_norm(self) -> float:
        # SERVO-02: math.hypot (not sqrt(sum(c**2))) - squaring a
        # perfectly finite but extreme component (e.g. ~1e200) overflows
        # a float and Python raises OverflowError for that, unlike most
        # float arithmetic, which silently returns inf. hypot computes
        # the same Euclidean norm without that intermediate overflow.
        return math.hypot(self.dx, self.dy, self.dz)

    @property
    def angular_norm(self) -> float:
        return math.hypot(self.droll, self.dpitch, self.dyaw)


def compute_pose_error(current: Pose6D, target: Pose6D) -> PoseError:
    """target - current, with angular components wrapped to the shortest turn."""
    error = PoseError(
        dx=target.x - current.x,
        dy=target.y - current.y,
        dz=target.z - current.z,
        droll=_wrap_angle_diff(target.roll, current.roll),
        dpitch=_wrap_angle_diff(target.pitch, current.pitch),
        dyaw=_wrap_angle_diff(target.yaw, current.yaw),
    )
    # SERVO-02: current/target are each individually guaranteed finite
    # (Pose6D.parse's own isfinite check), but subtracting two
    # representable, extreme values (e.g. 1e308 and -1e308) can still
    # silently overflow to inf - checked here, once, at the real source,
    # so every consumer of PoseError (compute_velocity_command AND
    # is_converged) inherits the guarantee instead of each needing its
    # own copy of this check.
    _require_all_finite((error.dx, error.dy, error.dz, error.droll, error.dpitch, error.dyaw), "pose error")
    return error


@dataclass(frozen=True)
class VelocityCommand:
    vx: float
    vy: float
    vz: float
    wroll: float
    wpitch: float
    wyaw: float


def _clamp_vector(components: tuple[float, ...], max_norm: float | None) -> tuple[float, ...]:
    """Scale components down (preserving direction) so their norm <= max_norm."""
    if max_norm is None:
        return components
    _require_positive_finite(max_norm, "maximum speed")
    # SERVO-02: hypot, not sqrt(sum(c**2)) - see PoseError.linear_norm's
    # own comment for why squaring directly can raise OverflowError on a
    # perfectly finite component.
    norm = math.hypot(*components)
    if norm <= max_norm or norm == 0.0:
        return components
    scale = max_norm / norm
    return tuple(c * scale for c in components)


def compute_velocity_command(
    error: PoseError,
    gain: float,
    max_linear_speed: float | None = None,
    max_angular_speed: float | None = None,
) -> VelocityCommand:
    """Proportional (P) control law: command = gain * error, then clamped.

    Clamping preserves direction (scales the whole linear/angular vector
    down together) rather than clamping each axis independently, so the
    tool head still moves in a straight line towards the target instead
    of skewing off-axis when one component saturates first.
    """
    _require_positive_finite(gain, "gain")
    linear = _clamp_vector(
        (gain * error.dx, gain * error.dy, gain * error.dz), max_linear_speed
    )
    angular = _clamp_vector(
        (gain * error.droll, gain * error.dpitch, gain * error.dyaw), max_angular_speed
    )
    # SERVO-02: the real, final safety net. gain * error.dN can silently
    # overflow to inf even with both operands finite (huge error x huge
    # gain), and when no max_*_speed is given at all, _clamp_vector
    # short-circuits without ever computing or checking a norm -
    # inf/NaN would otherwise reach the client inside a 200 response as
    # a "successfully computed" motor command instead of a real 400.
    _require_all_finite(linear, "linear velocity command")
    _require_all_finite(angular, "angular velocity command")
    return VelocityCommand(
        vx=linear[0], vy=linear[1], vz=linear[2],
        wroll=angular[0], wpitch=angular[1], wyaw=angular[2],
    )


def is_converged(error: PoseError, linear_tol: float, angular_tol: float) -> bool:
    """Whether the pose error is small enough to stop the closed loop."""
    _require_positive_finite(linear_tol, "linear tolerance")
    _require_positive_finite(angular_tol, "angular tolerance")
    return error.linear_norm <= linear_tol and error.angular_norm <= angular_tol
