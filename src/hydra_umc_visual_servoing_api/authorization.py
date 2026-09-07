# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/authorization.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real request authorization for the PBVS correction law in servo.py.

servo.py's `compute_velocity_command()` will happily turn ANY current/
target pose pair into a velocity command - it has no concept of whether
the visual estimate behind that pair is trustworthy, or whether the rest
of the cell is even in a state where actuating a correction is safe. This
module is that missing gate: a `VisualTargetRequest` is only ever turned
into an actuatable `VelocityCommand` when the upstream safety state is
`READY` AND the visual data backing it is fresh and confident enough -
everything else resolves to `INHIBITED` or `REJECTED`, never a command.

Two distinct block outcomes, not one generic "no":
- `INHIBITED` - the rest of the cell has not confirmed it is safe to move
  at all right now (no valid `SafetyState`). This is checked first and
  wins over everything else, mirroring the real cascade: a request from
  a perfectly fresh, confident camera frame is still not actionable if
  the cell itself is not in a ready state.
- `REJECTED` - the cell is ready, but *this specific* visual estimate is
  too old or not confident enough to trust for the correction being
  requested. A REJECTED request can succeed a moment later once a fresher,
  more confident frame arrives; an INHIBITED one cannot, no matter how
  good the frame is, until the cell's own safety state changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .pose import Pose6D
from .servo import PoseError, VelocityCommand, compute_pose_error, compute_velocity_command

# The only SafetyState value that authorizes a correction - matches the
# HYDRA-UMC-SDK SafetyState contract's own enum (READY/INHIBITED/FAULT/
# SAFE_STOP). This module does not import the SDK itself (no runtime
# dependency added for one string comparison), but intentionally reuses
# its exact vocabulary so a caller wiring a real SafetyState feed in
# later doesn't have to translate between two different naming schemes.
READY_SAFETY_STATE = "READY"


class RequestOutcome(str, Enum):
    ACCEPTED = "accepted"
    INHIBITED = "inhibited"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VisualTargetRequest:
    """Everything a caller must supply for one correction request - the
    formal contract this module didn't have before: not just a pose pair,
    but the provenance and freshness of the visual estimate behind it."""

    current: Pose6D
    target: Pose6D
    frame_id: str
    confidence: float
    data_age_ms: float
    safety_state: str

    def __post_init__(self) -> None:
        if not self.frame_id:
            raise ValueError("frame_id must be a non-empty string")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0.0, 1.0], got {self.confidence}")
        if self.data_age_ms < 0:
            raise ValueError(f"data_age_ms must be non-negative, got {self.data_age_ms}")
        if not self.safety_state:
            raise ValueError("safety_state must be a non-empty string")


@dataclass(frozen=True)
class AuthorizationPolicy:
    """The two freshness/confidence gates a request must clear. Both
    bounds are inclusive - a request exactly at the threshold is still
    trusted, matching this ecosystem's existing convention of treating an
    exact boundary as the safe side, not the unsafe one."""

    min_confidence: float = 0.6
    max_data_age_ms: float = 200.0

    def __post_init__(self) -> None:
        if not (0.0 <= self.min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be within [0.0, 1.0], got {self.min_confidence}")
        if self.max_data_age_ms <= 0:
            raise ValueError(f"max_data_age_ms must be positive, got {self.max_data_age_ms}")


@dataclass(frozen=True)
class CorrectionDecision:
    """The one real decision this module makes, plus the human-readable
    reason and (only when ACCEPTED) the actual error/command computed."""

    outcome: RequestOutcome
    reason: str
    error: PoseError | None = None
    command: VelocityCommand | None = None


def authorize_correction(
    request: VisualTargetRequest,
    policy: AuthorizationPolicy,
    gain: float,
    max_linear_speed: float | None = None,
    max_angular_speed: float | None = None,
) -> CorrectionDecision:
    """The one real entry point that decides ACCEPTED/INHIBITED/REJECTED.

    Safety state is checked FIRST, before data freshness/confidence - an
    unready cell blocks a correction regardless of how good the frame
    behind it is, never the other way around.
    """
    if request.safety_state != READY_SAFETY_STATE:
        return CorrectionDecision(
            RequestOutcome.INHIBITED,
            f"safety_state is '{request.safety_state}', not '{READY_SAFETY_STATE}'",
        )

    if request.confidence < policy.min_confidence:
        return CorrectionDecision(
            RequestOutcome.REJECTED,
            f"confidence {request.confidence} is below the required minimum "
            f"{policy.min_confidence} for frame '{request.frame_id}'",
        )

    if request.data_age_ms > policy.max_data_age_ms:
        return CorrectionDecision(
            RequestOutcome.REJECTED,
            f"visual data for frame '{request.frame_id}' is {request.data_age_ms}ms "
            f"old, exceeds the maximum {policy.max_data_age_ms}ms",
        )

    error = compute_pose_error(request.current, request.target)
    command = compute_velocity_command(error, gain, max_linear_speed, max_angular_speed)
    return CorrectionDecision(
        RequestOutcome.ACCEPTED,
        f"frame '{request.frame_id}' authorized (confidence={request.confidence}, "
        f"data_age_ms={request.data_age_ms})",
        error=error,
        command=command,
    )
