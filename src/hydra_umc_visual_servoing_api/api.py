# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Plain JSON/HTTP surface (stdlib http.server) - same convention as
HYDRA-UMC-DATALAKE's/HYDRA-UMC-PRODUCTION-REPORTS' own api.py in this
family. Real gap this closes: main.py's `correct`/`request` subcommands
already run this project's real PBVS correction law and its real
authorization gate (servo.py/authorization.py) - but only as a one-shot
CLI, unreachable from HYDRA-UMC-SERVER or any other real caller. Both
routes below call the exact same functions the CLI does, with the exact
same "x,y,z,roll,pitch,yaw" pose string format (Pose6D.parse) rather than
inventing a second, parallel pose encoding to keep in sync.

Outcome is a POST-body concept here, not an HTTP status concept:
INHIBITED/REJECTED are real, correctly-computed decisions, not server
errors, so /request answers 200 for all three outcomes - 400 is reserved
for a genuinely malformed request body.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .authorization import AuthorizationPolicy, RequestOutcome, VisualTargetRequest, authorize_correction
from .limits import ServoLimits
from .pose import Pose6D, PoseParseError
from .servo import VelocityCommand, compute_pose_error, compute_velocity_command, is_converged

ZERO_COMMAND = VelocityCommand(vx=0.0, vy=0.0, vz=0.0, wroll=0.0, wpitch=0.0, wyaw=0.0)
REQUIRED = object()  # marks a field without a default: the request must carry it


def _number(body: dict, key: str, default: object) -> float | None:
    """`body[key]` as a float; a boolean or a non-number is an error, never converted."""
    if key not in body:
        if default is REQUIRED:
            raise KeyError(key)
        return default  # type: ignore[return-value]
    value = body[key]
    if value is None and default is not REQUIRED:
        return default  # type: ignore[return-value]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite, got {value!r}")
    return float(value)


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _write_json(handler, status, {"error": message})


MAX_BODY_BYTES = 1024 * 1024
# How much of an oversized body this drains before responding - a real,
# reproducible race already found and fixed for this exact class of gap
# in HYDRA-UMC-ANOMALY-DETECTOR: rejecting an over-limit request without
# reading any of it left the client's own send() still in flight when the
# handler closed the connection, so on a body bigger than the OS socket
# buffer the client
# saw a raw ConnectionAbortedError instead of this clean 400. Draining up
# to this many bytes lets the client finish sending before the response
# goes out, without ever holding more than one bounded read in memory.
DRAIN_CAP_BYTES = MAX_BODY_BYTES * 16


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError as error:
        raise ValueError("Content-Length must be an integer") from error
    if length < 0 or length > MAX_BODY_BYTES:
        if 0 <= length <= DRAIN_CAP_BYTES:
            handler.rfile.read(length)
        raise ValueError(f"request body must contain 0-{MAX_BODY_BYTES} bytes")
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw)


class Handler(BaseHTTPRequestHandler):
    server: "VisualServoingServer"

    # Seconds a client may stay silent on its connection before the server
    # drops it, so a client that connects and sends nothing cannot hold a
    # handler thread forever. Socket I/O only; never the time a correction takes.
    timeout = 15.0

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/stats":
            _write_json(self, 200, {"role": self.server.role})
        else:
            _write_error(self, 404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = _read_json_body(self)
        except (json.JSONDecodeError, ValueError) as e:
            _write_error(self, 400, f"malformed JSON body: {e}")
            return
        # `_read_json_body`'s own return type annotation says
        # `dict`, but json.loads() happily returns whatever the body's
        # top-level JSON value actually was - a list ([]), null, a bare
        # string or number are all syntactically valid JSON that parses
        # without error. Every route below immediately does
        # body["current"]/body.get(...), which raises an uncaught
        # TypeError (not KeyError - _parse_poses' own except clause never
        # catches it) for any of those - crashing the handler thread with
        # no HTTP response at all instead of a clean 400. Checked once,
        # centrally, for both routes rather than duplicating this guard.
        if not isinstance(body, dict):
            _write_error(self, 400, f"request body must be a JSON object, got {type(body).__name__}")
            return
        if path == "/correct":
            self._handle_correct(body)
        elif path == "/request":
            self._handle_request(body)
        else:
            _write_error(self, 404, "not found")

    def _parse_poses(self, body: dict) -> tuple[Pose6D, Pose6D] | None:
        """Shared real parse for both routes - writes the real 400 itself
        and returns None on failure, same pattern as this family's other
        api.py files' _build_*_report helpers."""
        try:
            current = Pose6D.parse(str(body["current"]))
            target = Pose6D.parse(str(body["target"]))
        except KeyError as e:
            _write_error(self, 400, f"missing required field: {e}")
            return None
        except PoseParseError as e:
            _write_error(self, 400, str(e))
            return None
        return current, target

    def _effective_limits(self, body: dict) -> dict[str, float | None]:
        """The limits for this request: the server's own, optionally tightened
        by the caller. Asking for something looser than the server allows is an
        error (400), never silently honoured and never silently ignored."""
        limits = self.server.limits

        def at_least(key: str, floor: float) -> float:
            value = _number(body, key, floor)
            if value < floor:
                raise ValueError(f"{key} {value} is looser than this service's minimum {floor}")
            return value

        def at_most(key: str, ceiling: float, default: float | None = None) -> float:
            value = _number(body, key, ceiling if default is None else default)
            if value > ceiling:
                raise ValueError(f"{key} {value} is looser than this service's maximum {ceiling}")
            return value

        calibration = _number(body, "max_calibration_age_days", None)
        if limits.max_calibration_age_days is not None:
            if calibration is None:
                calibration = limits.max_calibration_age_days
            elif calibration > limits.max_calibration_age_days:
                raise ValueError(
                    f"max_calibration_age_days {calibration} is looser than this service's maximum "
                    f"{limits.max_calibration_age_days}"
                )
        return {
            "min_confidence": at_least("min_confidence", limits.min_confidence),
            "max_data_age_ms": at_most("max_data_age_ms", limits.max_data_age_ms),
            "max_calibration_age_days": calibration,
            "gain": at_most("gain", limits.max_gain, default=min(1.0, limits.max_gain)),
            "max_linear_speed": at_most("max_linear_speed", limits.max_linear_speed),
            "max_angular_speed": at_most("max_angular_speed", limits.max_angular_speed),
        }

    def _command_payload(self, command: VelocityCommand, frame_id: str | None, hold: bool) -> dict:
        """A command always carries when it was issued, how long it is valid
        and a sequence number: a consumer must stop when it does not receive a
        newer valid command within `valid_for_ms`."""
        payload = asdict(command)
        payload.update({
            "hold": hold,
            "issued_at_ms": int(time.time() * 1000),
            "valid_for_ms": self.server.limits.valid_for_ms,
            "seq": self.server.next_sequence(),
            "frame_id": frame_id,
        })
        return payload

    def _handle_correct(self, body: dict) -> None:
        poses = self._parse_poses(body)
        if poses is None:
            return
        current, target = poses
        try:
            effective = self._effective_limits(body)
            frame_id = body.get("frame_id")
            if frame_id is not None and not isinstance(frame_id, str):
                raise ValueError("frame_id must be a string")
            error = compute_pose_error(current, target)
            command = compute_velocity_command(
                error, gain=effective["gain"],
                max_linear_speed=effective["max_linear_speed"],
                max_angular_speed=effective["max_angular_speed"],
            )
            converged = is_converged(
                error,
                _number(body, "linear_tol", 0.001),
                _number(body, "angular_tol", 0.01),
            )
        except (ValueError, TypeError) as e:
            _write_error(self, 400, str(e))
            return
        # Inside the tolerance there is nothing left to correct: command zero.
        payload = self._command_payload(ZERO_COMMAND if converged else command, frame_id, hold=converged)
        _write_json(self, 200, {"error": asdict(error), "command": payload, "converged": converged})

    def _handle_request(self, body: dict) -> None:
        poses = self._parse_poses(body)
        if poses is None:
            return
        current, target = poses
        try:
            effective = self._effective_limits(body)
            request = VisualTargetRequest(
                current=current,
                target=target,
                frame_id=str(body["frame_id"]),
                confidence=_number(body, "confidence", REQUIRED),
                data_age_ms=_number(body, "data_age_ms", REQUIRED),
                safety_state=str(body["safety_state"]),
                calibration_version=(str(body["calibration_version"]) if body.get("calibration_version") is not None else None),
                calibration_age_days=_number(body, "calibration_age_days", None),
            )
            policy = AuthorizationPolicy(
                min_confidence=effective["min_confidence"],
                max_data_age_ms=effective["max_data_age_ms"],
                max_calibration_age_days=effective["max_calibration_age_days"],
            )
            decision = authorize_correction(
                request, policy,
                gain=effective["gain"],
                max_linear_speed=effective["max_linear_speed"],
                max_angular_speed=effective["max_angular_speed"],
            )
            converged = (
                decision.error is not None
                and is_converged(decision.error, _number(body, "linear_tol", 0.001), _number(body, "angular_tol", 0.01))
            )
        except KeyError as e:
            _write_error(self, 400, f"missing required field: {e}")
            return
        except (ValueError, TypeError) as e:
            _write_error(self, 400, str(e))
            return

        outcome, reason = decision.outcome, decision.reason
        if outcome is RequestOutcome.ACCEPTED and not self.server.claim_frame(request.frame_id):
            # the same visual frame must never be turned into a second command
            outcome = RequestOutcome.REJECTED
            reason = f"frame '{request.frame_id}' was already used for a correction"
        accepted = outcome is RequestOutcome.ACCEPTED
        if accepted and not converged and decision.command is not None:
            command = self._command_payload(decision.command, request.frame_id, hold=False)
        else:
            # not authorised, or nothing left to correct: an explicit zero
            # command, never "no command", so the consumer stops
            command = self._command_payload(ZERO_COMMAND, request.frame_id, hold=True)
        _write_json(self, 200, {
            "outcome": outcome.value,
            "reason": reason,
            "error": asdict(decision.error) if decision.error is not None else None,
            "command": command,
            "converged": bool(converged) if accepted else False,
        })


class VisualServoingServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], role: str, limits: ServoLimits | None = None) -> None:
        super().__init__(address, Handler)
        self.role = role
        self.limits = limits or ServoLimits()
        self._state_lock = threading.Lock()
        self._sequence = 0
        self._last_frame_id: str | None = None

    def next_sequence(self) -> int:
        with self._state_lock:
            self._sequence += 1
            return self._sequence

    def claim_frame(self, frame_id: str) -> bool:
        """False when this frame already produced the previous accepted command."""
        with self._state_lock:
            if frame_id == self._last_frame_id:
                return False
            self._last_frame_id = frame_id
            return True
