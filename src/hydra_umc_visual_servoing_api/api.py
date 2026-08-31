# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
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
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .authorization import AuthorizationPolicy, VisualTargetRequest, authorize_correction
from .pose import Pose6D, PoseParseError
from .servo import compute_pose_error, compute_velocity_command, is_converged


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _write_json(handler, status, {"error": message})


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    return json.loads(raw)


class Handler(BaseHTTPRequestHandler):
    server: "VisualServoingServer"

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
        except json.JSONDecodeError as e:
            _write_error(self, 400, f"malformed JSON body: {e}")
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

    def _handle_correct(self, body: dict) -> None:
        poses = self._parse_poses(body)
        if poses is None:
            return
        current, target = poses
        try:
            gain = float(body.get("gain", 1.0))
            error = compute_pose_error(current, target)
            command = compute_velocity_command(
                error, gain=gain,
                max_linear_speed=body.get("max_linear_speed"),
                max_angular_speed=body.get("max_angular_speed"),
            )
            converged = is_converged(
                error,
                float(body.get("linear_tol", 0.001)),
                float(body.get("angular_tol", 0.01)),
            )
        except (ValueError, TypeError) as e:
            _write_error(self, 400, str(e))
            return
        _write_json(self, 200, {"error": asdict(error), "command": asdict(command), "converged": converged})

    def _handle_request(self, body: dict) -> None:
        poses = self._parse_poses(body)
        if poses is None:
            return
        current, target = poses
        try:
            request = VisualTargetRequest(
                current=current,
                target=target,
                frame_id=str(body["frame_id"]),
                confidence=float(body["confidence"]),
                data_age_ms=float(body["data_age_ms"]),
                safety_state=str(body["safety_state"]),
            )
            policy = AuthorizationPolicy(
                min_confidence=float(body.get("min_confidence", 0.6)),
                max_data_age_ms=float(body.get("max_data_age_ms", 200.0)),
            )
        except KeyError as e:
            _write_error(self, 400, f"missing required field: {e}")
            return
        except (ValueError, TypeError) as e:
            _write_error(self, 400, str(e))
            return

        decision = authorize_correction(
            request, policy,
            gain=float(body.get("gain", 1.0)),
            max_linear_speed=body.get("max_linear_speed"),
            max_angular_speed=body.get("max_angular_speed"),
        )
        _write_json(self, 200, {
            "outcome": decision.outcome.value,
            "reason": decision.reason,
            "error": asdict(decision.error) if decision.error is not None else None,
            "command": asdict(decision.command) if decision.command is not None else None,
        })


class VisualServoingServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], role: str) -> None:
        super().__init__(address, Handler)
        self.role = role
