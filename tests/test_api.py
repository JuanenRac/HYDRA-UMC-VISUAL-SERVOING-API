# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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

import pytest

from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.limits import ServoLimits

# Limits loose enough for the tests that check the arithmetic of the control law.
GENEROUS = ServoLimits(max_gain=100.0, max_linear_speed=100.0, max_angular_speed=100.0)


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
def running_server(limits: ServoLimits | None = None) -> Iterator[str]:
    server = VisualServoingServer(("127.0.0.1", 0), "test role", limits)
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
    with running_server(GENEROUS) as base:
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
    with running_server(GENEROUS) as base:
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
    with running_server(GENEROUS) as base:
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
        # never "no command": an explicit zero that tells the consumer to hold
        assert body["command"]["hold"] is True
        assert {body["command"][k] for k in ("vx", "vy", "vz", "wroll", "wpitch", "wyaw")} == {0.0}


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
        assert "gain" in body["error"] and "number" in body["error"]


def test_malformed_json_body_rejected() -> None:
    with running_server() as base:
        req = urllib.request.Request(f"{base}/correct", data=b"{not json", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400


def _post_raw(url: str, raw: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=raw, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# (P1):
# a syntactically valid JSON body whose TOP LEVEL isn't an object ([],
# null, a bare string or number) used to crash the handler thread with
# an uncaught TypeError from body["current"] - no HTTP response reached
# the client at all, instead of the clean 400 every other malformed body
# already got. Real, live requests against a real running server, not
# just the parsing helper in isolation.
@pytest.mark.parametrize("raw", [b"[]", b"null", b'"just a string"', b"42"])
def test_non_object_json_body_is_rejected_with_a_real_400(raw) -> None:
    with running_server() as base:
        status, body = _post_raw(f"{base}/correct", raw)
        assert status == 400
        assert "error" in body


def test_server_still_serves_a_valid_request_after_a_non_object_body() -> None:
    # The real regression this closes: a crashed handler thread used to
    # leave the connection in a state where the NEXT, perfectly valid
    # request could also fail - confirm the server keeps working.
    with running_server(GENEROUS) as base:
        _post_raw(f"{base}/correct", b"[]")
        status, body = _post(f"{base}/correct", {"current": "0,0,0,0,0,0", "target": "1,0,0,0,0,0"})
        assert status == 200
        assert body["command"]["vx"] == 1.0


def test_oversized_json_request_is_rejected_before_parsing() -> None:
    # Found while auditing the code: this
    # endpoint used to read Content-Length bytes with no upper bound
    # before parsing - an oversized/malformed header let a caller force
    # unbounded memory buffering. Confirmed real against a live server,
    # not just the helper function in isolation.
    from hydra_umc_visual_servoing_api.api import MAX_BODY_BYTES

    with running_server() as base:
        raw = b"{" + b"x" * MAX_BODY_BYTES
        request = urllib.request.Request(
            f"{base}/correct",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("expected an HTTPError for an oversized body")
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert str(MAX_BODY_BYTES) in json.loads(error.read())["error"]


def test_a_client_that_sends_nothing_is_dropped_instead_of_holding_a_thread(monkeypatch) -> None:
    import socket
    import time

    from hydra_umc_visual_servoing_api.api import Handler

    monkeypatch.setattr(Handler, "timeout", 0.3)
    with running_server() as base:
        port = int(base.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            started = time.monotonic()
            assert client.recv(1) == b""  # sends nothing; the server closes the connection by itself
            assert time.monotonic() - started < 3


def _request_body(**overrides):
    body = {
        "current": "0,0,0,0,0,0", "target": "5,0,0,0,0,0", "frame_id": "f-1",
        "confidence": 0.9, "data_age_ms": 10.0, "safety_state": "READY",
    }
    body.update(overrides)
    return body


def test_the_caller_cannot_relax_the_servers_safety_policy() -> None:
    with running_server() as base:
        for relaxed in (
            {"min_confidence": 0.0},
            {"max_data_age_ms": 1e300},
            {"gain": 50.0},
            {"max_linear_speed": 1000.0},
            {"max_angular_speed": 1000.0},
        ):
            status, body = _post(f"{base}/request", _request_body(**relaxed))
            assert status == 400, relaxed
            assert "looser" in body["error"]


def test_a_request_that_relaxes_every_limit_at_once_is_refused() -> None:
    with running_server() as base:
        status, _ = _post(f"{base}/request", _request_body(
            confidence=0.01, data_age_ms=60000, min_confidence=0, max_data_age_ms=1e300, gain=50))
        assert status == 400


def test_a_caller_may_tighten_the_policy() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", _request_body(confidence=0.7, min_confidence=0.8))
        assert status == 200 and body["outcome"] == "rejected"
        status, body = _post(f"{base}/request", _request_body(frame_id="f-2", max_linear_speed=0.1))
        assert status == 200 and body["outcome"] == "accepted"
        assert abs(body["command"]["vx"]) <= 0.1 + 1e-9


def test_the_default_limits_saturate_the_command() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", _request_body())
        assert status == 200 and body["outcome"] == "accepted"
        assert body["command"]["vx"] == pytest.approx(0.25)


def test_a_rejected_request_returns_an_explicit_zero_command_that_expires() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/request", _request_body(confidence=0.1))
        assert body["outcome"] == "rejected"
        command = body["command"]
        assert command["hold"] is True and command["vx"] == 0.0
        assert command["valid_for_ms"] > 0 and command["issued_at_ms"] > 0 and command["seq"] >= 1
        assert command["frame_id"] == "f-1"


def test_commands_carry_an_increasing_sequence_number() -> None:
    with running_server() as base:
        first = _post(f"{base}/request", _request_body(frame_id="a"))[1]["command"]["seq"]
        second = _post(f"{base}/request", _request_body(frame_id="b"))[1]["command"]["seq"]
        assert second > first


def test_the_same_frame_cannot_produce_two_commands() -> None:
    with running_server() as base:
        assert _post(f"{base}/request", _request_body(frame_id="same"))[1]["outcome"] == "accepted"
        again = _post(f"{base}/request", _request_body(frame_id="same"))[1]
        assert again["outcome"] == "rejected" and "already used" in again["reason"]
        assert again["command"]["hold"] is True


def test_a_converged_pose_commands_zero() -> None:
    with running_server() as base:
        status, body = _post(f"{base}/correct", {"current": "0,0,0,0,0,0", "target": "0.0001,0,0,0,0,0"})
        assert status == 200 and body["converged"] is True
        assert body["command"]["vx"] == 0.0 and body["command"]["hold"] is True


def test_booleans_and_strings_are_not_numbers() -> None:
    with running_server() as base:
        for field, value in (("max_linear_speed", True), ("gain", "1.0"), ("min_confidence", False)):
            status, _ = _post(f"{base}/request", _request_body(**{field: value}))
            assert status == 400, field


def test_a_server_started_with_stricter_limits_enforces_them() -> None:
    limits = ServoLimits(min_confidence=0.95, max_linear_speed=0.05)
    with running_server(limits) as base:
        status, body = _post(f"{base}/request", _request_body(confidence=0.9))
        assert body["outcome"] == "rejected"
        status, body = _post(f"{base}/request", _request_body(frame_id="g", confidence=0.99))
        assert body["command"]["vx"] == pytest.approx(0.05)


def test_correct_accepts_poses_as_lists_and_objects_as_well_as_strings() -> None:
    string = {"current": "0,0,0,0,0,0", "target": "0.1,0,0,0,0,0", "gain": 0.5, "dt": 0.1}
    as_list = {**string, "current": [0, 0, 0, 0, 0, 0], "target": [0.1, 0, 0, 0, 0, 0]}
    as_object = {
        **string,
        "current": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": 0, "yaw": 0},
        "target": {"x": 0.1, "y": 0, "z": 0, "roll": 0, "pitch": 0, "yaw": 0},
    }
    with running_server(GENEROUS) as base:
        answers = [_post(f"{base}/correct", body) for body in (string, as_list, as_object)]
    assert [status for status, _ in answers] == [200, 200, 200]
    def same_control(answer: dict) -> dict:
        # the command also carries the time it was issued and a sequence number
        command = {k: v for k, v in answer["command"].items() if k not in ("issued_at_ms", "seq")}
        return {"error": answer["error"], "command": command, "converged": answer["converged"]}

    assert same_control(answers[0][1]) == same_control(answers[1][1]) == same_control(answers[2][1])


def test_a_bad_pose_object_is_a_400_that_names_the_key() -> None:
    body = {"current": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": 0}, "target": "0,0,0,0,0,0", "gain": 0.5, "dt": 0.1}
    with running_server(GENEROUS) as base:
        status, answer = _post(f"{base}/correct", body)
    assert status == 400
    assert "yaw" in json.dumps(answer)


# ---- sessions (the loop with memory, see session.py) ----


def _delete(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


SESSION_LIMITS = ServoLimits(max_gain=5.0, max_linear_speed=1.0, max_angular_speed=1.0)
READY_REQUEST = {
    "current": "0,0,0,0,0,0",
    "target": "0.05,0,0,0,0,0",
    "frame_id": "f-1",
    "confidence": 0.95,
    "data_age_ms": 20,
    "safety_state": "READY",
}


def test_a_session_is_opened_stepped_and_closed() -> None:
    with running_server(SESSION_LIMITS) as base:
        status, opened = _post(f"{base}/sessions", {"gain": 1.0, "period_ms": 100})
        assert status == 201
        sid = opened["session_id"]
        assert opened["config"]["period_s"] == 0.1 and opened["config"]["gain"] == 1.0
        status, first = _post(f"{base}/sessions/{sid}/correct", {"current": "0,0,0,0,0,0", "target": "0.05,0,0,0,0,0", "frame_id": "a"})
        assert status == 200
        assert first["state"] == "restarted"
        assert 0 < first["command"]["vx"] <= 0.05 + 1e-9, "starts from rest, limited by the acceleration"
        assert first["command"]["valid_for_ms"] > 0 and first["command"]["seq"] >= 1 and first["command"]["hold"] is False
        status, second = _post(f"{base}/sessions/{sid}/correct", {"current": "0.01,0,0,0,0,0", "target": "0.05,0,0,0,0,0"})
        assert second["state"] == "tracking" and second["updates"] == 2
        assert _delete(f"{base}/sessions/{sid}")[0] == 200
        assert _post(f"{base}/sessions/{sid}/correct", {"current": "0,0,0,0,0,0", "target": "1,0,0,0,0,0"})[0] == 404
        assert _delete(f"{base}/sessions/{sid}")[0] == 404


def test_a_session_cannot_ask_for_more_than_the_service_allows_nor_for_a_loop_that_diverges() -> None:
    with running_server(SESSION_LIMITS) as base:
        assert _post(f"{base}/sessions", {"gain": 9.0})[0] == 400  # above the service's max_gain
        assert _post(f"{base}/sessions", {"max_linear_speed": 5.0})[0] == 400
        status, body = _post(f"{base}/sessions", {"gain": 5.0, "period_ms": 1000})  # K*T = 5
        assert status == 400 and "K*T" in body["error"]
        assert _post(f"{base}/sessions", {"gain": 5.0, "period_ms": 100, "max_gain_period": 5.0})[0] == 400  # a looser bound than the default is not a thing the caller moves
        assert _post(f"{base}/sessions", {"filter_alpha": 0})[0] == 400
        assert _post(f"{base}/sessions", {"period_ms": "fast"})[0] == 400


def test_a_session_step_through_request_goes_through_the_safety_gate() -> None:
    with running_server(SESSION_LIMITS) as base:
        sid = _post(f"{base}/sessions", {})[1]["session_id"]
        status, ok = _post(f"{base}/sessions/{sid}/request", READY_REQUEST)
        assert status == 200 and ok["outcome"] == "accepted" and ok["state"] in ("restarted", "tracking")
        assert ok["command"]["vx"] > 0
        # the same frame twice is not a second correction
        status, again = _post(f"{base}/sessions/{sid}/request", READY_REQUEST)
        assert again["outcome"] == "rejected" and again["command"]["vx"] == 0.0 and again["state"] == "vision_lost"
        # a stale detection, a low confidence and a safety state that is not READY each stop the loop with an explicit zero
        for change in ({"data_age_ms": 5000}, {"confidence": 0.1}, {"safety_state": "FAULT"}):
            status, body = _post(f"{base}/sessions/{sid}/request", {**READY_REQUEST, "frame_id": f"x-{len(change)}-{list(change)[0]}", **change})
            assert status == 200
            assert body["outcome"] in ("rejected", "inhibited"), change
            assert body["command"]["vx"] == 0.0 and body["command"]["hold"] is True
        # and after that the loop restarts from rest
        status, back = _post(f"{base}/sessions/{sid}/request", {**READY_REQUEST, "frame_id": "after"})
        assert back["outcome"] == "accepted" and back["state"] == "restarted"


def test_vision_lost_gives_an_explicit_zero_command() -> None:
    with running_server(SESSION_LIMITS) as base:
        sid = _post(f"{base}/sessions", {})[1]["session_id"]
        _post(f"{base}/sessions/{sid}/correct", {"current": "0,0,0,0,0,0", "target": "1,0,0,0,0,0"})
        status, lost = _post(f"{base}/sessions/{sid}/vision-lost", {})
        assert status == 200
        assert lost["state"] == "vision_lost" and lost["command"]["vx"] == 0.0 and lost["command"]["hold"] is True
        assert _post(f"{base}/sessions/{sid}/nonsense", {})[0] == 404


def test_the_number_of_sessions_is_bounded() -> None:
    from hydra_umc_visual_servoing_api.api import MAX_SESSIONS

    with running_server(SESSION_LIMITS) as base:
        ids = [_post(f"{base}/sessions", {})[1]["session_id"] for _ in range(MAX_SESSIONS)]
        status, body = _post(f"{base}/sessions", {})
        assert status == 429 and "close one" in body["error"]
        _delete(f"{base}/sessions/{ids[0]}")
        assert _post(f"{base}/sessions", {})[0] == 201


def test_sessions_need_the_token_when_there_is_one() -> None:
    server = VisualServoingServer(("127.0.0.1", 0), "test role", SESSION_LIMITS, token="0123456789abcdef")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _post(f"{base}/sessions", {})[0] == 401
        assert _delete(f"{base}/sessions/abc")[0] == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
