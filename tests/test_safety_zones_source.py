# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_safety_zones_source.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""With a SAFETY-ZONES address configured, the service judges the verdict it fetches and ignores what a caller says."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.limits import ServoLimits
from hydra_umc_visual_servoing_api.safety_source import SafetyZonesClient, normalize_url

pytestmark = pytest.mark.strict_safety

GENEROUS = ServoLimits(max_gain=100.0, max_linear_speed=100.0, max_angular_speed=100.0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state(state: str = "READY", age_ms: float = 10.0) -> dict:
    return {
        "schema_version": "1.0",
        "state": state,
        "source": "hydra-umc-safety-zones",
        "timestamp_utc": (_now() - timedelta(milliseconds=age_ms)).isoformat(),
    }


@contextmanager
def fake_zones(status: int = 200, body: object | None = None, token: str = ""):
    seen: list[str] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("Authorization", ""))
            payload = json.dumps(body).encode() if body is not None else b"{}"
            code = status
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                code, payload = 401, b"{}"
            self.send_response(code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def service(client: SafetyZonesClient | None):
    server = VisualServoingServer(("127.0.0.1", 0), "test", GENEROUS, "", safety_zones=client)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


_frame = iter(range(10_000))


def _request(base: str, **extra: object) -> dict:
    body = {
        "current": "0,0,0,0,0,0",
        "target": "0.1,0,0,0,0,0",
        "frame_id": f"f{next(_frame)}",
        "confidence": 0.9,
        # a caller that claims the best possible state
        "safety_state": {"state": "READY", "timestamp_utc": _now().isoformat()},
    }
    body.update(extra)
    req = urllib.request.Request(base + "/request", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"http": e.code, **json.loads(e.read())}


def _captured(age_ms: float = 20.0) -> str:
    return (_now() - timedelta(milliseconds=age_ms)).isoformat()


def test_a_recent_ready_verdict_from_safety_zones_authorizes() -> None:
    with fake_zones(body={"sdkSafetyState": _state("READY")}) as (url, _), service(SafetyZonesClient(url)) as base:
        answer = _request(base, captured_at_utc=_captured())
        assert answer["outcome"] == "accepted", answer


def test_the_callers_ready_is_ignored_when_safety_zones_says_danger() -> None:
    with fake_zones(body={"sdkSafetyState": _state("DANGER")}) as (url, _), service(SafetyZonesClient(url)) as base:
        answer = _request(base, captured_at_utc=_captured())
        assert answer["outcome"] != "accepted"


def test_an_old_verdict_is_inhibited() -> None:
    with fake_zones(body={"sdkSafetyState": _state("READY", age_ms=60_000)}) as (url, _), service(SafetyZonesClient(url)) as base:
        answer = _request(base, captured_at_utc=_captured())
        assert answer["outcome"] == "inhibited" and "old" in answer["reason"]


@pytest.mark.parametrize("status, body", [(404, {}), (500, {}), (200, {}), (200, {"sdkSafetyState": "READY"}), (200, [1])])
def test_no_usable_answer_is_inhibited(status: int, body: object) -> None:
    with fake_zones(status=status, body=body) as (url, _), service(SafetyZonesClient(url)) as base:
        answer = _request(base, captured_at_utc=_captured())
        assert answer["outcome"] == "inhibited"


def test_an_unreachable_safety_zones_is_inhibited() -> None:
    with service(SafetyZonesClient("http://127.0.0.1:1", timeout_ms=200)) as base:
        answer = _request(base, captured_at_utc=_captured())
        assert answer["outcome"] == "inhibited" and "not reachable" in answer["reason"]


def test_the_token_is_presented_to_safety_zones() -> None:
    with fake_zones(body={"sdkSafetyState": _state()}, token="zones-token-123456") as (url, seen):
        with service(SafetyZonesClient(url, "zones-token-123456")) as base:
            assert _request(base, captured_at_utc=_captured())["outcome"] == "accepted"
        with service(SafetyZonesClient(url, "")) as base:
            assert _request(base, captured_at_utc=_captured())["outcome"] == "inhibited"
        assert "Bearer zones-token-123456" in seen


def test_the_capture_time_is_required_and_an_old_capture_is_refused() -> None:
    with fake_zones(body={"sdkSafetyState": _state()}) as (url, _), service(SafetyZonesClient(url)) as base:
        assert _request(base)["http"] == 400
        assert _request(base, captured_at_utc=_captured(5_000))["outcome"] != "accepted"


def test_a_declared_age_cannot_hide_an_old_capture() -> None:
    with fake_zones(body={"sdkSafetyState": _state()}) as (url, _), service(SafetyZonesClient(url)) as base:
        assert _request(base, captured_at_utc=_captured(5_000), data_age_ms=1.0)["outcome"] != "accepted"


def test_without_safety_zones_the_caller_object_is_still_used() -> None:
    with service(None) as base:
        assert _request(base, data_age_ms=10.0)["outcome"] == "accepted"


def test_the_address_must_be_http() -> None:
    with pytest.raises(ValueError):
        normalize_url("ftp://x")
    with pytest.raises(ValueError):
        SafetyZonesClient("http://x", timeout_ms=0)
