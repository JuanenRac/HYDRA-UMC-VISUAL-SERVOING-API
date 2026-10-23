# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/pose.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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
    """Raised when a pose (a "x,y,z,roll,pitch,yaw" string, a list of six numbers or an object) is malformed."""


POSE_FIELDS = ("x", "y", "z", "roll", "pitch", "yaw")


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

    @classmethod
    def coerce(cls, value: object) -> "Pose6D":
        """A pose from what a JSON request carries: the "x,y,z,roll,pitch,yaw" string, a list of six numbers in that
        order, or an object with exactly the keys x, y, z, roll, pitch and yaw. Numbers must be numbers (not text,
        not booleans) and finite; an object with a missing, extra or repeated key is refused by name."""
        if isinstance(value, str):
            return cls.parse(value)
        if isinstance(value, (list, tuple)):
            if len(value) != 6:
                raise PoseParseError(f"a pose list needs 6 numbers (x,y,z,roll,pitch,yaw), got {len(value)}")
            return cls._from_numbers(dict(zip(POSE_FIELDS, value)))
        if isinstance(value, dict):
            missing = [name for name in POSE_FIELDS if name not in value]
            extra = sorted(str(key) for key in value if key not in POSE_FIELDS)
            if missing or extra:
                raise PoseParseError(
                    "a pose object needs exactly x, y, z, roll, pitch and yaw"
                    + (f"; missing {', '.join(missing)}" if missing else "")
                    + (f"; unknown {', '.join(extra)}" if extra else "")
                )
            return cls._from_numbers(value)
        raise PoseParseError(f"a pose is a string, a list of six numbers or an object, not {type(value).__name__}")

    @classmethod
    def _from_numbers(cls, named: dict) -> "Pose6D":
        values = []
        for name in POSE_FIELDS:
            number = named[name]
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise PoseParseError(f"pose field {name} must be a number, not {type(number).__name__}")
            if not math.isfinite(number):
                raise PoseParseError(f"pose field {name} must be finite")
            values.append(float(number))
        return cls(*values)
