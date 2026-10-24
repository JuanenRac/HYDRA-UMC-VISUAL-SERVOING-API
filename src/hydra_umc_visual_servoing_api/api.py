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
import secrets
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .access import authorized, reject
from .authorization import AuthorizationPolicy, RequestOutcome, VisualTargetRequest, authorize_correction
from .limits import ServoLimits
from .pose import Pose6D, PoseParseError
from .servo import VelocityCommand, compute_pose_error, compute_velocity_command, is_converged
from .session import (
    DEFAULT_FILTER_ALPHA,
    DEFAULT_HYSTERESIS,
    DEFAULT_MAX_ANGULAR_ACCELERATION,
    DEFAULT_MAX_GAIN_PERIOD,
    DEFAULT_MAX_LINEAR_ACCELERATION,
    DEFAULT_PERIOD_S,
    DEFAULT_VISION_TIMEOUT_S,
    ServoSession,
    SessionConfig,
)

# Servoing sessions the service keeps at the same time, and how long one may sit unused before it is dropped.
MAX_SESSIONS = 8
SESSION_IDLE_SECONDS = 300.0

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


def _ceiling(body: dict, key: str, ceiling: float) -> float:
    """`body[key]`, which may be smaller than `ceiling` and never larger."""
    value = _number(body, key, ceiling)
    if value > ceiling:
        raise ValueError(f"{key} {value} is looser than this service's maximum {ceiling}")
    return value


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


# GET path the project manifest declares as the health probe; it carries no token
HEALTH_PATH = "/stats"


class Handler(BaseHTTPRequestHandler):
    server: "VisualServoingServer"

    # Seconds a client may stay silent on its connection before the server
    # drops it, so a client that connects and sends nothing cannot hold a
    # handler thread forever. Socket I/O only; never the time a correction takes.
    timeout = 15.0

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        # the health path of the manifest stays open so the service registry can probe the service without the token
        if urlparse(self.path).path != HEALTH_PATH and not authorized(self, self.server.token):
            reject(self)
            return
        path = urlparse(self.path).path
        if path == "/stats":
            _write_json(self, 200, {"role": self.server.role})
        else:
            _write_error(self, 404, "not found")

    def do_DELETE(self) -> None:  # noqa: N802
        if not authorized(self, self.server.token):
            reject(self)
            return
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "sessions":
            if self.server.drop_session(parts[1]):
                _write_json(self, 200, {"closed": parts[1]})
            else:
                _write_error(self, 404, "no such session")
        else:
            _write_error(self, 404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not authorized(self, self.server.token):
            reject(self)
            return
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
        elif path == "/sessions":
            self._handle_session_open(body)
        elif path.startswith("/sessions/") and path.count("/") == 3:
            _, _, session_id, action = path.split("/")
            self._handle_session_step(session_id, action, body)
        else:
            _write_error(self, 404, "not found")

    def _parse_poses(self, body: dict) -> tuple[Pose6D, Pose6D] | None:
        """Shared real parse for both routes - writes the real 400 itself
        and returns None on failure, same pattern as this family's other
        api.py files' _build_*_report helpers."""
        try:
            current = Pose6D.coerce(body["current"])
            target = Pose6D.coerce(body["target"])
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

    # ---- sessions: the loop with memory (see session.py) ----

    def _handle_session_open(self, body: dict) -> None:
        try:
            effective = self._effective_limits(body)
            period = _number(body, "period_ms", DEFAULT_PERIOD_S * 1000.0) / 1000.0
            config = SessionConfig(
                gain=effective["gain"],
                max_linear_speed=effective["max_linear_speed"],
                max_angular_speed=effective["max_angular_speed"],
                period_s=period,
                max_gain_period=_ceiling(body, "max_gain_period", DEFAULT_MAX_GAIN_PERIOD),
                filter_alpha=_number(body, "filter_alpha", DEFAULT_FILTER_ALPHA),
                hysteresis=_number(body, "hysteresis", DEFAULT_HYSTERESIS),
                vision_timeout_s=_number(body, "vision_timeout_ms", DEFAULT_VISION_TIMEOUT_S * 1000.0) / 1000.0,
                max_linear_acceleration=_number(body, "max_linear_acceleration", DEFAULT_MAX_LINEAR_ACCELERATION),
                max_angular_acceleration=_number(body, "max_angular_acceleration", DEFAULT_MAX_ANGULAR_ACCELERATION),
                linear_tolerance=_number(body, "linear_tol", 0.001),
                angular_tolerance=_number(body, "angular_tol", 0.01),
            )
        except (ValueError, TypeError) as e:
            _write_error(self, 400, str(e))
            return
        session_id = self.server.open_session(config)
        if session_id is None:
            _write_error(self, 429, f"the service already keeps {MAX_SESSIONS} sessions in use: close one first")
            return
        _write_json(self, 201, {"session_id": session_id, "config": asdict(config)})

    def _handle_session_step(self, session_id: str, action: str, body: dict) -> None:
        session = self.server.get_session(session_id)
        if session is None:
            _write_error(self, 404, "no such session")
            return
        now = time.monotonic()
        if action == "vision-lost":
            with self.server.session_lock(session_id):
                step = session.vision_lost(now)
            _write_json(self, 200, self._session_payload(step, None, session))
            return
        if action not in ("correct", "request"):
            _write_error(self, 404, "not found")
            return
        poses = self._parse_poses(body)
        if poses is None:
            return
        current, target = poses
        frame_id = body.get("frame_id")
        try:
            if frame_id is not None and not isinstance(frame_id, str):
                raise ValueError("frame_id must be a string")
            outcome_info = None
            if action == "request":
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
                decision = authorize_correction(
                    request,
                    AuthorizationPolicy(
                        min_confidence=effective["min_confidence"],
                        max_data_age_ms=effective["max_data_age_ms"],
                        max_calibration_age_days=effective["max_calibration_age_days"],
                    ),
                    gain=session.config.gain,
                    max_linear_speed=session.config.max_linear_speed,
                    max_angular_speed=session.config.max_angular_speed,
                )
                accepted = decision.outcome is RequestOutcome.ACCEPTED
                reason = decision.reason
                if accepted and not self.server.claim_frame(request.frame_id):
                    accepted, reason = False, f"frame '{request.frame_id}' was already used for a correction"
                # a decision that was acceptable but lost the claim on its frame is a rejection
                final = RequestOutcome.ACCEPTED if accepted else (decision.outcome if decision.outcome is not RequestOutcome.ACCEPTED else RequestOutcome.REJECTED)
                outcome_info = {"outcome": final.value, "reason": reason}
                if not accepted:
                    # a correction that is not authorised is a detection that cannot be used: the loop stops and restarts from rest
                    with self.server.session_lock(session_id):
                        step = session.vision_lost(now)
                    _write_json(self, 200, self._session_payload(step, frame_id, session, outcome_info))
                    return
            with self.server.session_lock(session_id):
                step = session.update(current, target, now)
        except KeyError as e:
            _write_error(self, 400, f"missing required field: {e}")
            return
        except (ValueError, TypeError) as e:
            _write_error(self, 400, str(e))
            return
        _write_json(self, 200, self._session_payload(step, frame_id, session, outcome_info))

    def _session_payload(self, step, frame_id, session: ServoSession, outcome_info: dict | None = None) -> dict:
        payload = {
            "state": step.state,
            "converged": step.converged,
            "error": asdict(step.error) if step.error is not None else None,
            "filtered_pose": asdict(step.filtered) if step.filtered is not None else None,
            "command": self._command_payload(step.command, frame_id, hold=step.state != "tracking" and step.state != "restarted"),
            "updates": session.updates,
        }
        if outcome_info is not None:
            payload.update(outcome_info)
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
    def __init__(self, address: tuple[str, int], role: str, limits: ServoLimits | None = None, token: str = "") -> None:
        super().__init__(address, Handler)
        # empty means no token (loopback only; see access.py)
        self.token = token
        self.role = role
        self.limits = limits or ServoLimits()
        self._state_lock = threading.Lock()
        self._sequence = 0
        self._last_frame_id: str | None = None
        self._sessions: dict[str, tuple[ServoSession, threading.Lock, list[float]]] = {}

    # ---- sessions ----
    def open_session(self, config: SessionConfig) -> str | None:
        """A new session id, or None when the limit is reached with every session still in use."""
        now = time.monotonic()
        with self._state_lock:
            for stale in [sid for sid, (_, _, used) in self._sessions.items() if now - used[0] > SESSION_IDLE_SECONDS]:
                del self._sessions[stale]
            if len(self._sessions) >= MAX_SESSIONS:
                return None
            session_id = secrets.token_hex(8)
            self._sessions[session_id] = (ServoSession(config), threading.Lock(), [now])
            return session_id

    def get_session(self, session_id: str) -> ServoSession | None:
        with self._state_lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            entry[2][0] = time.monotonic()
            return entry[0]

    def session_lock(self, session_id: str) -> threading.Lock:
        with self._state_lock:
            entry = self._sessions.get(session_id)
            return entry[1] if entry is not None else threading.Lock()

    def drop_session(self, session_id: str) -> bool:
        with self._state_lock:
            return self._sessions.pop(session_id, None) is not None

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
