# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_servo_log.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The record of corrections for offline tuning: off by default, one line per decided frame, never in the way."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.servo_log import ServoLog, ServoLogError, ServoLogPolicy, load_servo_log_policy, make_record

BODY = {"current": "0,0,0,0,0,0", "target": "0.05,0,0,0,0,0", "frame_id": "f-1", "confidence": 0.9, "data_age_ms": 10.0, "safety_state": "READY"}


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _run(**kwargs: object) -> tuple[VisualServoingServer, threading.Thread]:
    server = VisualServoingServer(("127.0.0.1", 0), "test", **kwargs)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server: VisualServoingServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_off_by_default_and_loading(tmp_path: Path) -> None:
    assert ServoLogPolicy().enabled is False and ServoLog(ServoLogPolicy()).write({"a": 1}) is False
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"servo_log": {"file": "x.jsonl"}}), encoding="utf-8")
    assert load_servo_log_policy(cfg) == ServoLogPolicy("x.jsonl")
    cfg.write_text("{}", encoding="utf-8")
    assert load_servo_log_policy(cfg).enabled is False
    for bad in ('{"servo_log": 3}', '{"servo_log": {"file": 3}}', "nope"):
        cfg.write_text(bad, encoding="utf-8")
        with pytest.raises(ServoLogError):
            load_servo_log_policy(cfg)
    with pytest.raises(ServoLogError):
        load_servo_log_policy(tmp_path / "missing.json")


def test_shipped_configuration_writes_nothing() -> None:
    assert load_servo_log_policy(Path(__file__).resolve().parents[1] / "configs" / "visual-servoing.json").enabled is False


def test_record_keeps_only_numbers_and_ids() -> None:
    record = make_record(
        issued_at_ms=5, frame_id="f", outcome="accepted", reason=None, linear_error=0.1, angular_error=float("nan"),
        command={"vx": 1.0, "vy": 0.0, "vz": 0.0, "wroll": 0.0, "wpitch": 0.0, "wyaw": 0.0, "hold": False, "seq": 3, "extra": "x"},
        converged=False, decision_ms=1.23456,
    )
    assert record["angular_error"] is None and record["decision_ms"] == 1.235 and "extra" not in record["command"]


def test_a_file_that_cannot_be_written_never_stops_a_correction(tmp_path: Path) -> None:
    assert ServoLog(ServoLogPolicy(str(tmp_path / "missing-dir" / "log.jsonl"))).write({"a": 1}) is False


def test_request_writes_one_line_per_decided_frame(tmp_path: Path) -> None:
    log = tmp_path / "servo.jsonl"
    server, thread = _run(servo_log=ServoLogPolicy(str(log)))
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/request"
        status, body = _post(url, BODY)
        assert status == 200 and body["outcome"] == "accepted"
        status, body = _post(url, {**BODY, "frame_id": "f-2", "safety_state": "SAFE_STOP"})
        assert status == 200 and body["outcome"] != "accepted"
        status, _ = _post(url, {"current": "nope"})
        assert status == 400
        lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert [line["frame_id"] for line in lines] == ["f-1", "f-2"]
        assert lines[0]["outcome"] == "accepted" and lines[0]["command"]["hold"] is False and lines[0]["linear_error"] == pytest.approx(0.05)
        assert lines[1]["command"]["hold"] is True and lines[1]["command"]["vx"] == 0.0
    finally:
        _stop(server, thread)


def test_no_file_is_written_while_off(tmp_path: Path) -> None:
    server, thread = _run()
    try:
        status, _ = _post(f"http://127.0.0.1:{server.server_address[1]}/request", BODY)
        assert status == 200
        assert list(tmp_path.iterdir()) == []
    finally:
        _stop(server, thread)
