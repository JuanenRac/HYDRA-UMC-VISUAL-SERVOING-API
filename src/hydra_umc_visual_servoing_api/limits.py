# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/limits.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The limits the service enforces, fixed when it starts.

A caller (the HTTP request body or the CLI flags) may ask for something
stricter than these limits, never for something looser: the safety gate is
only a gate if the party it protects against cannot move it. Defaults are
deliberately conservative; a cell that needs more says so when the service
is started (`serve --max-linear-speed ...`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_MAX_DATA_AGE_MS = 200.0
DEFAULT_MAX_GAIN = 2.0
DEFAULT_MAX_LINEAR_SPEED = 0.25  # m/s
DEFAULT_MAX_ANGULAR_SPEED = 0.5  # rad/s
DEFAULT_VALID_FOR_MS = 250.0
# Oldest SafetyState (by its own timestamp) a correction may rely on.
DEFAULT_MAX_SAFETY_AGE_MS = 1000.0


def _finite_positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than 0, got {value!r}")


@dataclass(frozen=True)
class ServoLimits:
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    max_data_age_ms: float = DEFAULT_MAX_DATA_AGE_MS
    max_calibration_age_days: float | None = None
    max_gain: float = DEFAULT_MAX_GAIN
    max_linear_speed: float = DEFAULT_MAX_LINEAR_SPEED
    max_angular_speed: float = DEFAULT_MAX_ANGULAR_SPEED
    # How long a consumer may keep applying a command before it must stop
    # unless a newer one arrived.
    valid_for_ms: float = DEFAULT_VALID_FOR_MS
    max_safety_age_ms: float = DEFAULT_MAX_SAFETY_AGE_MS

    def __post_init__(self) -> None:
        if isinstance(self.min_confidence, bool) or not isinstance(self.min_confidence, (int, float)) or not (
            math.isfinite(self.min_confidence) and 0.0 <= self.min_confidence <= 1.0
        ):
            raise ValueError(f"min_confidence must be within [0.0, 1.0], got {self.min_confidence!r}")
        _finite_positive(self.max_data_age_ms, "max_data_age_ms")
        if self.max_calibration_age_days is not None:
            _finite_positive(self.max_calibration_age_days, "max_calibration_age_days")
        _finite_positive(self.max_gain, "max_gain")
        _finite_positive(self.max_linear_speed, "max_linear_speed")
        _finite_positive(self.max_angular_speed, "max_angular_speed")
        _finite_positive(self.valid_for_ms, "valid_for_ms")
        _finite_positive(self.max_safety_age_ms, "max_safety_age_ms")
