# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/contract.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The `VisualTargetRequest` and `VelocityCommand` contracts of the SDK, checked here with the standard library.

`VisualTargetRequest` is what asks this service for one correction (where the tool is, where the image says it should be, which frame, how sure the detector is,
how old the estimate is, what the safety state was). `VelocityCommand` is the answer (the velocity in m/s and rad/s, whether it is a hold, when it was issued, how
long it is valid, and a sequence number). Both are closed objects with `schema_version` "1.0". This module checks the same rules the SDK clients check, without
depending on them; a test compares the constants and runs both validators over the same messages when the SDK checkout is next to this repository.

The service answers every command with `schema_version` and checks the command it is about to send against the contract (a command that does not fit is a bug of
the service and is never sent). A request that carries `schema_version` is checked against the contract before anything else; one that does not is handled as
before, unless the service was started with `--require-contract`.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "1.0"
SAFETY_STATES: tuple[str, ...] = ("READY", "INHIBITED", "FAULT", "SAFE_STOP")
POSE_FIELDS: tuple[str, ...] = ("x", "y", "z", "roll", "pitch", "yaw")
MAX_VALID_FOR_MS = 60000

REQUEST_REQUIRED = ("schema_version", "frame_id", "current", "target", "confidence", "safety_state")
REQUEST_OPTIONAL = ("data_age_ms", "captured_at_utc", "calibration_version", "calibration_age_days")
COMMAND_REQUIRED = ("schema_version", "vx", "vy", "vz", "wroll", "wpitch", "wyaw", "hold", "issued_at_ms", "valid_for_ms", "seq")
COMMAND_OPTIONAL = ("frame_id",)
# What this service reads from a request besides the contract: the limits a caller may ask to tighten and the tolerances of the loop.
SERVICE_EXTRAS = ("gain", "min_confidence", "max_data_age_ms", "max_calibration_age_days", "max_linear_speed", "max_angular_speed", "linear_tol", "angular_tol")

IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$")


class ContractError(ValueError):
    """A message does not follow the contract."""


def _fields(payload: Any, required: tuple[str, ...], optional: tuple[str, ...], label: str) -> None:
    if not isinstance(payload, dict):
        raise ContractError(f"{label} must be an object")
    for name in required:
        if name not in payload:
            raise ContractError(f"missing required field: {name}")
    unknown = sorted(str(k) for k in set(payload) - set(required) - set(optional))
    if unknown:
        raise ContractError(f"unsupported field(s): {', '.join(unknown)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(f"schema_version must be '{SCHEMA_VERSION}'")


def _real(payload: dict, name: str, low: float | None = None, high: float | None = None, *, above: bool = False) -> None:
    value = payload[name]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ContractError(f"{name} must be a finite number")
    if low is not None and (value <= low if above else value < low):
        raise ContractError(f"{name} must be {'greater than' if above else 'at least'} {low}")
    if high is not None and value > high:
        raise ContractError(f"{name} must be at most {high}")


def _whole(payload: dict, name: str, low: int) -> None:
    value = payload[name]
    if type(value) is not int or value < low:
        raise ContractError(f"{name} must be an integer from {low}")


def _pose(payload: dict, name: str) -> None:
    pose = payload[name]
    if not isinstance(pose, dict):
        raise ContractError(f"{name} must be an object")
    missing = [f for f in POSE_FIELDS if f not in pose]
    extra = sorted(str(k) for k in set(pose) - set(POSE_FIELDS))
    if missing or extra:
        raise ContractError(f"{name}: " + (f"missing {', '.join(missing)}" if missing else f"unsupported field(s): {', '.join(extra)}"))
    for field in POSE_FIELDS:
        _real(pose, field)


def validate_visual_target_request(payload: Any) -> None:
    """The checks of the VisualTargetRequest contract."""
    _fields(payload, REQUEST_REQUIRED, REQUEST_OPTIONAL, "VisualTargetRequest")
    frame_id = payload["frame_id"]
    if not (isinstance(frame_id, str) and IDENTIFIER.fullmatch(frame_id)):
        raise ContractError("frame_id must be 1 to 64 letters, digits, dots, colons, dashes or underscores")
    _pose(payload, "current")
    _pose(payload, "target")
    _real(payload, "confidence", 0, 1)
    if payload["safety_state"] not in SAFETY_STATES:
        raise ContractError(f"safety_state must be one of {', '.join(SAFETY_STATES)}")
    if "data_age_ms" not in payload and "captured_at_utc" not in payload:
        raise ContractError("data_age_ms or captured_at_utc is required: a request must say how old the estimate is")
    if "data_age_ms" in payload:
        _real(payload, "data_age_ms", 0)
    if "captured_at_utc" in payload:
        stamp = payload["captured_at_utc"]
        if not (isinstance(stamp, str) and _DATE_TIME.fullmatch(stamp)):
            raise ContractError("captured_at_utc must be an RFC 3339 date-time")
        try:
            datetime.fromisoformat(stamp.upper().replace("Z", "+00:00"))
        except ValueError:
            raise ContractError("captured_at_utc must be a valid RFC 3339 date-time") from None
    if "calibration_version" in payload:
        version = payload["calibration_version"]
        if not isinstance(version, str) or not 1 <= len(version) <= 64:
            raise ContractError("calibration_version must be a text of 1 to 64 characters")
    if "calibration_age_days" in payload:
        _real(payload, "calibration_age_days", 0)


def validate_velocity_command(payload: Any) -> None:
    """The checks of the VelocityCommand contract."""
    _fields(payload, COMMAND_REQUIRED, COMMAND_OPTIONAL, "VelocityCommand")
    for name in ("vx", "vy", "vz", "wroll", "wpitch", "wyaw"):
        _real(payload, name)
    if type(payload["hold"]) is not bool:
        raise ContractError("hold must be true or false")
    _whole(payload, "issued_at_ms", 0)
    _real(payload, "valid_for_ms", 0, MAX_VALID_FOR_MS, above=True)
    _whole(payload, "seq", 0)
    frame_id = payload.get("frame_id")
    if frame_id is not None and not (isinstance(frame_id, str) and IDENTIFIER.fullmatch(frame_id)):
        raise ContractError("frame_id must be 1 to 64 letters, digits, dots, colons, dashes or underscores")


def command_is_current(payload: dict[str, Any], now_ms: int) -> bool:
    """Whether a valid command can still be acted on at `now_ms`: issued no later than now and not past its validity. A consumer that gets False must stop."""
    validate_velocity_command(payload)
    age_ms = now_ms - payload["issued_at_ms"]
    return 0 <= age_ms <= payload["valid_for_ms"]


def check_request_body(body: dict[str, Any]) -> bool:
    """Check a request that declares `schema_version`. Returns True when it did (and passed); raises ContractError when it did not pass.
    False means the body declares no schema_version and nothing was checked.

    Keys the service itself reads (`SERVICE_EXTRAS`) may sit next to the contract fields. A `safety_state` that is an object (the state with its
    timestamp, which the service accepts) is checked by its `state`."""
    if "schema_version" not in body:
        return False
    contract = {k: v for k, v in body.items() if k not in SERVICE_EXTRAS}
    state = contract.get("safety_state")
    if isinstance(state, dict):
        contract["safety_state"] = state.get("state")
    validate_visual_target_request(contract)
    return True
