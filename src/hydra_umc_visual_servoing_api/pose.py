# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/pose.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""6-DOF pose representation.

A pose is (x, y, z, roll, pitch, yaw) as the estimator hands it, and may also
carry its orientation as a unit quaternion (`orientation`). The servo law works
on the quaternion (see servo.py): when none is given it is made from the angles.
A pose built from a quaternion gets its roll, pitch and yaw derived from it (the
angles are the earlier, deprecated way to read the orientation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

from .rotation import Quaternion


class PoseParseError(ValueError):
    """Raised when a pose (a "x,y,z,roll,pitch,yaw" string, a list of six numbers or an object) is malformed."""


POSE_FIELDS = ("x", "y", "z", "roll", "pitch", "yaw")
QUATERNION_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Pose6D:
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float
    # The orientation as given, when it was given as a quaternion; the angles above are then derived from it.
    orientation: Quaternion | None = field(default=None, compare=False, repr=False)

    @property
    def quaternion(self) -> Quaternion:
        """The orientation as a unit quaternion (made from the angles when the pose carries none)."""
        if self.orientation is not None:
            return self.orientation
        return Quaternion.from_euler(self.roll, self.pitch, self.yaw)

    @classmethod
    def from_quaternion(cls, x: float, y: float, z: float, orientation: Quaternion) -> "Pose6D":
        unit = orientation.normalized()
        roll, pitch, yaw = unit.to_euler()
        return cls(x, y, z, roll, pitch, yaw, unit)

    @classmethod
    def _from_seven(cls, values: list[float], text: str) -> "Pose6D":
        if not all(math.isfinite(value) for value in values):
            raise PoseParseError(f"pose values must be finite: {text!r}")
        q = Quaternion(values[3], values[4], values[5], values[6])
        if abs(q.norm - 1.0) > QUATERNION_TOLERANCE:
            raise PoseParseError(f"the quaternion of a pose must have length 1, its length is {q.norm:.9g}: {text!r}")
        return cls.from_quaternion(values[0], values[1], values[2], q)

    @classmethod
    def parse(cls, text: str) -> "Pose6D":
        """Parse "x,y,z,roll,pitch,yaw" (meters, radians), or "x,y,z,qw,qx,qy,qz" (meters, unit quaternion)."""
        parts = text.split(",")
        if len(parts) == 7:
            try:
                return cls._from_seven([float(p) for p in parts], text)
            except ValueError as exc:
                if isinstance(exc, PoseParseError):
                    raise
                raise PoseParseError(f"non-numeric value in pose {text!r}: {exc}") from exc
        if len(parts) != 6:
            raise PoseParseError(
                f"expected 6 comma-separated values (x,y,z,roll,pitch,yaw) or 7 (x,y,z,qw,qx,qy,qz), got {len(parts)}: {text!r}"
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
            if len(value) == 7:
                for number in value:
                    if isinstance(number, bool) or not isinstance(number, (int, float)):
                        raise PoseParseError("a pose list holds numbers only")
                return cls._from_seven([float(number) for number in value], repr(list(value)))
            if len(value) != 6:
                raise PoseParseError(
                    f"a pose list needs 6 numbers (x,y,z,roll,pitch,yaw) or 7 (x,y,z,qw,qx,qy,qz), got {len(value)}"
                )
            return cls._from_numbers(dict(zip(POSE_FIELDS, value)))
        if isinstance(value, dict) and "orientation" in value:
            return cls._from_contract(value)
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
    def _from_contract(cls, value: dict) -> "Pose6D":
        """A pose in the shape of the SDK `Pose` contract: `position` {x, y, z} and `orientation` {w, x, y, z}
        (`frame` and `schema_version` may be present; the angles `euler` of the earlier form are ignored)."""
        allowed = {"schema_version", "frame", "position", "orientation", "euler"}
        extra = sorted(str(key) for key in value if key not in allowed)
        if extra:
            raise PoseParseError(f"unknown pose field(s): {', '.join(extra)}")
        position, orientation = value.get("position"), value.get("orientation")
        if not isinstance(position, dict) or set(position) != {"x", "y", "z"}:
            raise PoseParseError("position must be an object with exactly x, y and z")
        if not isinstance(orientation, dict) or set(orientation) != {"w", "x", "y", "z"}:
            raise PoseParseError("orientation must be an object with exactly w, x, y and z")
        numbers = []
        for source, names in ((position, "xyz"), (orientation, "wxyz")):
            for name in names:
                number = source[name]
                if isinstance(number, bool) or not isinstance(number, (int, float)):
                    raise PoseParseError(f"pose field {name} must be a number, not {type(number).__name__}")
                numbers.append(float(number))
        return cls._from_seven(numbers, "pose object")

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
