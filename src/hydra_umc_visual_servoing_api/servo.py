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
        return math.sqrt(self.dx**2 + self.dy**2 + self.dz**2)

    @property
    def angular_norm(self) -> float:
        return math.sqrt(self.droll**2 + self.dpitch**2 + self.dyaw**2)


def compute_pose_error(current: Pose6D, target: Pose6D) -> PoseError:
    """target - current, with angular components wrapped to the shortest turn."""
    return PoseError(
        dx=target.x - current.x,
        dy=target.y - current.y,
        dz=target.z - current.z,
        droll=_wrap_angle_diff(target.roll, current.roll),
        dpitch=_wrap_angle_diff(target.pitch, current.pitch),
        dyaw=_wrap_angle_diff(target.yaw, current.yaw),
    )


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
    norm = math.sqrt(sum(c**2 for c in components))
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
    if gain <= 0:
        raise ValueError(f"gain must be positive, got {gain}")
    linear = _clamp_vector(
        (gain * error.dx, gain * error.dy, gain * error.dz), max_linear_speed
    )
    angular = _clamp_vector(
        (gain * error.droll, gain * error.dpitch, gain * error.dyaw), max_angular_speed
    )
    return VelocityCommand(
        vx=linear[0], vy=linear[1], vz=linear[2],
        wroll=angular[0], wpitch=angular[1], wyaw=angular[2],
    )


def is_converged(error: PoseError, linear_tol: float, angular_tol: float) -> bool:
    """Whether the pose error is small enough to stop the closed loop."""
    return error.linear_norm <= linear_tol and error.angular_norm <= angular_tol
