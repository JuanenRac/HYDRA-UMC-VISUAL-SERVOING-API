# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/pose.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""6-DOF pose representation.

Deliberately plain (x, y, z, roll, pitch, yaw) rather than a rotation
matrix/quaternion - v0 only needs to represent and difference the poses
the (future, hardware-bound) Hailo-8 estimator would hand this API, not
to compose or interpolate rotations.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


class PoseParseError(ValueError):
    """Raised when a "x,y,z,roll,pitch,yaw" string is malformed."""


@dataclass(frozen=True)
class Pose6D:
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float

    @classmethod
    def parse(cls, text: str) -> "Pose6D":
        """Parse "x,y,z,roll,pitch,yaw" (meters, radians)."""
        parts = text.split(",")
        if len(parts) != 6:
            raise PoseParseError(
                f"expected 6 comma-separated values (x,y,z,roll,pitch,yaw), got {len(parts)}: {text!r}"
            )
        try:
            values = [float(p) for p in parts]
        except ValueError as exc:
            raise PoseParseError(f"non-numeric value in pose {text!r}: {exc}") from exc
        if not all(math.isfinite(value) for value in values):
            raise PoseParseError(f"pose values must be finite: {text!r}")
        return cls(*values)
