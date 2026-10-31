# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/ibvs.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Image-Based Visual Servoing (IBVS): the loop is closed on features in the image, not on a 6-DOF pose.

`configs/visual-servoing.json`:

    {"ibvs": {"enabled": false, "damping": 0.0001}}

* `enabled`: turns on `POST /ibvs/correct` (off by default; the route answers 404 while off).
* `damping`: the Levenberg-Marquardt term that keeps the solution bounded near a singular
  interaction matrix (default 0.0001, not negative).

A feature is a point in normalized image coordinates (x, y) = ((u - cx) / fx, (v - cy) / fy),
with an estimated depth Z in metres along the optical axis. For N points the interaction matrix
stacks, for each point, the two rows

    [ -1/Z    0    x/Z    x*y     -(1 + x^2)    y ]
    [  0    -1/Z   y/Z    1 + y^2   -x*y       -x ]

and the camera velocity that drives the features to the wanted ones is the damped least-squares
solution of  L v = -gain * (s - s*),  v = (vx, vy, vz, wx, wy, wz) in the camera frame. The
result is clamped like the pose-based law: direction kept, norm limited. At least 3 points are
needed for the six degrees of freedom to be determined; with fewer the answer is refused.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from .servo import VelocityCommand, _clamp_vector, _require_positive_finite

CONFIG_ENV = "HYDRA_UMC_VISUAL_SERVOING_CONFIG"
MIN_POINTS = 3
MAX_POINTS = 32
MAX_COORDINATE = 5.0
MIN_DEPTH = 0.01
MAX_DEPTH = 1000.0


class IbvsError(ValueError):
    """The features, the depths or the configuration cannot be used."""


@dataclass(frozen=True)
class IbvsPolicy:
    enabled: bool = False
    damping: float = 1e-4

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise IbvsError(f"ibvs.enabled must be true or false, got {self.enabled!r}")
        if isinstance(self.damping, bool) or not isinstance(self.damping, (int, float)) or not math.isfinite(self.damping) or self.damping < 0:
            raise IbvsError(f"ibvs.damping must be a number that is not negative, got {self.damping!r}")


def load_ibvs_policy(path: str | os.PathLike[str] | None = None) -> IbvsPolicy:
    chosen = str(path) if path else os.environ.get(CONFIG_ENV, "")
    if not chosen:
        return IbvsPolicy()
    try:
        data = json.loads(Path(chosen).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IbvsError(f"cannot read the configuration file {chosen}: {error}") from error
    section = data.get("ibvs", {}) if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise IbvsError(f"{chosen} must hold a JSON object whose ibvs entry is an object")
    return IbvsPolicy(**{key: section[key] for key in ("enabled", "damping") if key in section})


def _points(value: object, label: str) -> list[tuple[float, float]]:
    if not isinstance(value, list) or not MIN_POINTS <= len(value) <= MAX_POINTS:
        raise IbvsError(f"{label}: expected {MIN_POINTS} to {MAX_POINTS} points")
    out: list[tuple[float, float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise IbvsError(f"{label}[{index}]: expected [x, y]")
        for number in point:
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or abs(number) > MAX_COORDINATE:
                raise IbvsError(f"{label}[{index}]: coordinates must be finite numbers within +-{MAX_COORDINATE:g} (normalized image coordinates)")
        out.append((float(point[0]), float(point[1])))
    return out


def _depths(value: object, count: int) -> list[float]:
    values = [value] * count if not isinstance(value, list) else value
    if len(values) != count:
        raise IbvsError(f"depth: expected one value or {count} values")
    out: list[float] = []
    for number in values:
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not MIN_DEPTH <= number <= MAX_DEPTH:
            raise IbvsError(f"depth must be finite and between {MIN_DEPTH:g} and {MAX_DEPTH:g} metres")
        out.append(float(number))
    return out


def interaction_matrix(points: list[tuple[float, float]], depths: list[float]) -> list[list[float]]:
    rows: list[list[float]] = []
    for (x, y), z in zip(points, depths):
        rows.append([-1.0 / z, 0.0, x / z, x * y, -(1.0 + x * x), y])
        rows.append([0.0, -1.0 / z, y / z, 1.0 + y * y, -x * y, -x])
    return rows


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for a small square system."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-300:
            raise IbvsError("the features do not determine a camera motion (singular system); raise ibvs.damping or add points")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(col + 1, n):
            factor = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def feature_error(points: list[tuple[float, float]], desired: list[tuple[float, float]]) -> list[float]:
    return [v for (x, y), (xd, yd) in zip(points, desired) for v in (x - xd, y - yd)]


def rms_error(error: list[float]) -> float:
    return math.sqrt(sum(e * e for e in error) / len(error))


def compute_ibvs_command(
    features: object,
    desired: object,
    depth: object,
    *,
    gain: float,
    damping: float,
    max_linear_speed: float | None = None,
    max_angular_speed: float | None = None,
) -> tuple[VelocityCommand, float]:
    """(camera-frame velocity command, rms feature error) that drives `features` towards `desired`."""
    _require_positive_finite(gain, "gain")
    current = _points(features, "features")
    wanted = _points(desired, "desired")
    if len(current) != len(wanted):
        raise IbvsError("features and desired must have the same number of points")
    z = _depths(depth, len(current))
    rows = interaction_matrix(current, z)
    error = feature_error(current, wanted)
    normal = [[sum(rows[k][i] * rows[k][j] for k in range(len(rows))) + (damping if i == j else 0.0) for j in range(6)] for i in range(6)]
    rhs = [-gain * sum(rows[k][i] * error[k] for k in range(len(rows))) for i in range(6)]
    velocity = _solve(normal, rhs)
    if not all(math.isfinite(v) for v in velocity):
        raise IbvsError("the velocity command is not finite; the inputs are too extreme")
    linear = _clamp_vector(tuple(velocity[:3]), max_linear_speed)
    angular = _clamp_vector(tuple(velocity[3:]), max_angular_speed)
    return VelocityCommand(linear[0], linear[1], linear[2], angular[0], angular[1], angular[2]), rms_error(error)
