# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_safety_state_age.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Fail-closed handling of the safety state: only a recent SafetyState object can authorize a correction."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from hydra_umc_visual_servoing_api.api import VisualServoingServer, resolve_safety_state
from hydra_umc_visual_servoing_api.limits import ServoLimits

pytestmark = pytest.mark.strict_safety

GENEROUS = ServoLimits(max_gain=100.0, max_linear_speed=100.0, max_angular_speed=100.0)
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _state(age_ms: float = 10.0, state: str = "READY", now: datetime = NOW) -> dict:
    return {
        "schema_version": "1.0",
        "state": state,
        "source": "safety-zones",
        "timestamp_utc": (now - timedelta(milliseconds=age_ms)).isoformat(),
    }


def test_a_recent_ready_object_is_believed() -> None:
    assert resolve_safety_state(_state(10), max_age_ms=1000, accept_legacy_string=False, now=NOW) == ("READY", None)


def test_an_old_ready_object_is_inhibited() -> None:
    state, note = resolve_safety_state(_state(3_600_000), max_age_ms=1000, accept_legacy_string=False, now=NOW)
    assert state == "INHIBITED" and "old" in note


@pytest.mark.parametrize(
    "raw",
    [
        None,
        42,
        [],
        {},
        {"state": "READY"},
        {"state": "READY", "timestamp_utc": 12345},
        {"state": "READY", "timestamp_utc": "yesterday"},
        {"state": "READY", "timestamp_utc": "2026-01-01T12:00:00"},
        {"state": "GO", "timestamp_utc": NOW.isoformat()},
        {"state": "READY", "timestamp_utc": (NOW + timedelta(hours=1)).isoformat()},
    ],
)
def test_anything_unusable_is_inhibited_never_ready(raw: object) -> None:
    state, note = resolve_safety_state(raw, max_age_ms=1000, accept_legacy_string=True, now=NOW)
    assert state == "INHIBITED" and note


def test_a_bare_string_is_refused_unless_the_legacy_switch_is_on() -> None:
    assert resolve_safety_state("READY", max_age_ms=1000, accept_legacy_string=False, now=NOW)[0] == "INHIBITED"
    assert resolve_safety_state("READY", max_age_ms=1000, accept_legacy_string=True, now=NOW) == ("READY", None)


def test_a_not_ready_object_stays_not_ready_even_when_fresh() -> None:
    assert resolve_safety_state(_state(1, "SAFE_STOP"), max_age_ms=1000, accept_legacy_string=False, now=NOW)[0] == "SAFE_STOP"


@contextmanager
def _server(**kwargs):
    server = VisualServoingServer(("127.0.0.1", 0), "test role", GENEROUS, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(base: str, safety_state: object, frame: str) -> dict:
    body = {
        "current": "0,0,0,0,0,0", "target": "1,0,0,0,0,0", "frame_id": frame,
        "confidence": 0.9, "data_age_ms": 10.0, "safety_state": safety_state,
    }
    req = urllib.request.Request(f"{base}/request", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def test_the_http_default_refuses_a_bare_string_and_a_stale_object_and_accepts_a_fresh_one() -> None:
    with _server() as base:
        bare = _request(base, "READY", "f1")
        assert bare["outcome"] == "inhibited" and "bare string" in bare["reason"]
        assert bare["command"]["hold"] is True
        stale = _request(base, _state(60_000, now=datetime.now(timezone.utc)), "f2")
        assert stale["outcome"] == "inhibited" and "old" in stale["reason"]
        fresh = _request(base, _state(5, now=datetime.now(timezone.utc)), "f3")
        assert fresh["outcome"] == "accepted"


def test_the_legacy_switch_restores_the_bare_string() -> None:
    with _server(accept_legacy_safety_string=True) as base:
        assert _request(base, "READY", "f1")["outcome"] == "accepted"


def test_the_age_limit_is_configurable_and_a_missing_field_is_still_a_400() -> None:
    limits = ServoLimits(max_gain=100.0, max_linear_speed=100.0, max_angular_speed=100.0, max_safety_age_ms=30_000.0)
    server = VisualServoingServer(("127.0.0.1", 0), "t", limits)
    try:
        assert resolve_safety_state(
            _state(10_000), max_age_ms=server.limits.max_safety_age_ms, accept_legacy_string=False, now=NOW
        )[0] == "READY"
    finally:
        server.server_close()
    with _server() as base:
        req = urllib.request.Request(f"{base}/request", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as info:
            urllib.request.urlopen(req, timeout=5)
        assert info.value.code == 400


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def test_a_session_step_obeys_the_same_rule() -> None:
    with _server() as base:
        sid = _post(f"{base}/sessions", {"gain": 1.0})["session_id"]
        step = {"current": "0,0,0,0,0,0", "target": "0.05,0,0,0,0,0", "confidence": 0.9, "data_age_ms": 10.0}
        refused = _post(f"{base}/sessions/{sid}/request", {**step, "frame_id": "a", "safety_state": "READY"})
        assert refused["outcome"] == "inhibited" and "bare string" in refused["reason"]
        assert refused["command"]["hold"] is True
        stale = _post(f"{base}/sessions/{sid}/request", {**step, "frame_id": "b", "safety_state": _state(60_000, now=datetime.now(timezone.utc))})
        assert stale["outcome"] == "inhibited"
        ok = _post(f"{base}/sessions/{sid}/request", {**step, "frame_id": "c", "safety_state": _state(5, now=datetime.now(timezone.utc))})
        assert ok["outcome"] == "accepted"
