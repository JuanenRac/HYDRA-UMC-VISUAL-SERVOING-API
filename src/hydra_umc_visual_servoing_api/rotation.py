# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/rotation.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Unit quaternions: the orientation error of the servo law is computed on them.

Euler angles do not commute, so the difference of two sets of angles is not the
rotation between the two poses. A quaternion `(w, x, y, z)` (Hamilton convention)
composes exactly. The conventions are the ones of the `Pose` contract of the
ecosystem SDK: roll, pitch, yaw are radians in the intrinsic Z-Y-X order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Quaternion:
    w: float
    x: float
    y: float
    z: float

    @classmethod
    def identity(cls) -> "Quaternion":
        return cls(1.0, 0.0, 0.0, 0.0)

    @property
    def norm(self) -> float:
        return math.sqrt(self.w * self.w + self.x * self.x + self.y * self.y + self.z * self.z)

    def normalized(self) -> "Quaternion":
        n = self.norm
        if not math.isfinite(n) or n == 0.0:
            raise ValueError("a quaternion with no length has no orientation")
        return Quaternion(self.w / n, self.x / n, self.y / n, self.z / n)

    def conjugate(self) -> "Quaternion":
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def __mul__(self, other: "Quaternion") -> "Quaternion":
        """Hamilton product: the rotation `other` first, then `self`."""
        return Quaternion(
            self.w * other.w - self.x * other.x - self.y * other.y - self.z * other.z,
            self.w * other.x + self.x * other.w + self.y * other.z - self.z * other.y,
            self.w * other.y - self.x * other.z + self.y * other.w + self.z * other.x,
            self.w * other.z + self.x * other.y - self.y * other.x + self.z * other.w,
        )

    def canonical(self) -> "Quaternion":
        """The same rotation with w >= 0: the shortest way round."""
        return self if self.w >= 0.0 else Quaternion(-self.w, -self.x, -self.y, -self.z)

    def to_rotation_vector(self) -> tuple[float, float, float]:
        """Axis times angle in radians, the angle within [0, pi]: the shape of an angular velocity."""
        q = self.normalized().canonical()
        sin_half = math.sqrt(q.x * q.x + q.y * q.y + q.z * q.z)
        if sin_half < 1e-12:
            return (2.0 * q.x, 2.0 * q.y, 2.0 * q.z)
        scale = 2.0 * math.atan2(sin_half, q.w) / sin_half
        return (q.x * scale, q.y * scale, q.z * scale)

    def angle_to(self, other: "Quaternion") -> float:
        p, q = self.normalized(), other.normalized()
        return 2.0 * math.acos(min(1.0, abs(p.w * q.w + p.x * q.x + p.y * q.y + p.z * q.z)))

    @classmethod
    def from_euler(cls, roll: float, pitch: float, yaw: float) -> "Quaternion":
        cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
        cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
        cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
        return cls(
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
        )

    def to_euler(self) -> tuple[float, float, float]:
        """(roll, pitch, yaw); at pitch +-90 degrees the roll is folded into the yaw."""
        q = self.normalized()
        sinp = 2.0 * (q.w * q.y - q.z * q.x)
        if abs(sinp) >= 1.0 - 1e-12:
            return (0.0, math.copysign(math.pi / 2.0, sinp),
                    math.atan2(2.0 * (q.w * q.z - q.x * q.y), 1.0 - 2.0 * (q.x * q.x + q.z * q.z)))
        roll = math.atan2(2.0 * (q.w * q.x + q.y * q.z), 1.0 - 2.0 * (q.x * q.x + q.y * q.y))
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        return (roll, math.asin(sinp), yaw)

    def blend(self, other: "Quaternion", amount: float) -> "Quaternion":
        """Normalized linear interpolation along the shorter arc: `amount` 0 gives `self`, 1 gives `other`."""
        sign = 1.0 if (self.w * other.w + self.x * other.x + self.y * other.y + self.z * other.z) >= 0.0 else -1.0
        return Quaternion(
            self.w + amount * (sign * other.w - self.w),
            self.x + amount * (sign * other.x - self.x),
            self.y + amount * (sign * other.y - self.y),
            self.z + amount * (sign * other.z - self.z),
        ).normalized()
