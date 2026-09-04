# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real end-to-end HTTP tests: a real VisualServoingServer (ThreadingHTTPServer)
hit with real urllib requests over a real socket, proving POST /correct and
POST /request reach the exact same servo.py/authorization.py functions the
CLI's `correct`/`request` subcommands already exercise - same convention as
HYDRA-UMC-PRODUCTION-REPORTS' own tests/test_api.py."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from hydra_umc_visual_servoing_api.api import VisualServoingServer


def _post(url: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@contextmanager
def running_server() -> Iterator[str]:
    server = VisualServoingServer(("127.0.0.1", 0), "test role")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_stats() -> None:
    with running_server() as base:
        status, body = _get(f"{base}/stats")
        assert status == 200
        assert body == {"role": "test role"}


def test_not_found() -> None:
    with running_server() as base:
        status, body = _get(f"{base}/nope")
        assert status == 404


def test_correct_computes_real_pose_error_and_command() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/correct", {
            "current": "0,0,0,0,0,0",
            "target": "1,0,0,0,0,0",
            "gain": 2.0,
        })
        assert status == 200
        assert body["error"]["dx"] == 1.0
        assert body["command"]["vx"] == 2.0
        assert body["converged"] is False


def test_correct_clamps_to_max_linear_speed() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/correct", {
            "current": "0,0,0,0,0,0",
            "target": "10,0,0,0,0,0",
            "gain": 1.0,
            "max_linear_speed": 0.5,
        })
        assert status == 200
        assert body["command"]["vx"] == 0.5


def test_correct_rejects_malformed_pose() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/correct", {"current": "not,a,pose", "target": "0,0,0,0,0,0"})
        assert status == 400
        assert "error" in body


def test_correct_rejects_missing_field() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/correct", {"current": "0,0,0,0,0,0"})
        assert status == 400


def test_request_accepted_with_ready_safety_state() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", {
            "current": "0,0,0,0,0,0",
            "target": "1,0,0,0,0,0",
            "frame_id": "cam1-42",
            "confidence": 0.9,
            "data_age_ms": 10.0,
            "safety_state": "READY",
        })
        assert status == 200
        assert body["outcome"] == "accepted"
        assert body["command"] is not None
        assert body["command"]["vx"] == 1.0


def test_request_inhibited_when_not_ready() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", {
            "current": "0,0,0,0,0,0",
            "target": "1,0,0,0,0,0",
            "frame_id": "cam1-42",
            "confidence": 0.9,
            "data_age_ms": 10.0,
            "safety_state": "FAULT",
        })
        assert status == 200
        assert body["outcome"] == "inhibited"
        assert body["command"] is None


def test_request_rejected_when_stale() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", {
            "current": "0,0,0,0,0,0",
            "target": "1,0,0,0,0,0",
            "frame_id": "cam1-42",
            "confidence": 0.9,
            "data_age_ms": 5000.0,
            "safety_state": "READY",
            "max_data_age_ms": 200.0,
        })
        assert status == 200
        assert body["outcome"] == "rejected"


def test_request_rejects_missing_field() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", {
            "current": "0,0,0,0,0,0",
            "target": "1,0,0,0,0,0",
        })
        assert status == 400


def test_request_rejects_non_numeric_gain() -> None:
    """Real bug regression: a malformed (non-numeric) gain/max_linear_speed/
    max_angular_speed used to be parsed AFTER the request-body try/except,
    so float() raising ValueError there crashed the handler thread with an
    unhandled traceback and no HTTP response at all, instead of the clean
    400 every other malformed field on this route already gets."""
    with running_server() as base:
        status, body = _post(f"{base}/request", {
            "current": "0,0,0,0,0,0",
            "target": "1,0,0,0,0,0",
            "frame_id": "cam1-42",
            "confidence": 0.9,
            "data_age_ms": 5.0,
            "safety_state": "READY",
            "gain": "not-a-number",
        })
        assert status == 400
        assert "float" in body["error"]


def test_malformed_json_body_rejected() -> None:
    with running_server() as base:
        req = urllib.request.Request(f"{base}/correct", data=b"{not json", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 400
