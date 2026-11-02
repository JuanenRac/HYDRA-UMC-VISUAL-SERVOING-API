# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_contract_and_bench.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The SDK contracts of the request and of the command, and the bench that closes the loop against a simulated tool."""
from __future__ import annotations

import importlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hydra_umc_visual_servoing_api import contract
from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.bench import BenchError, load_bench_config, parse_bench_config, run_bench, sweep_gains
from hydra_umc_visual_servoing_api.contract import (
    ContractError,
    check_request_body,
    command_is_current,
    validate_velocity_command,
    validate_visual_target_request,
)
from hydra_umc_visual_servoing_api.main import main

POSE = {"x": 0.0, "y": 0.0, "z": 0.1, "roll": 0.0, "pitch": 0.0, "yaw": 0.0}
TARGET = {"x": 0.1, "y": 0.0, "z": 0.1, "roll": 0.0, "pitch": 0.0, "yaw": 0.0}
REQUEST = {"schema_version": "1.0", "frame_id": "f-1", "current": POSE, "target": TARGET, "confidence": 0.9, "safety_state": "READY", "data_age_ms": 20}
COMMAND = {"schema_version": "1.0", "vx": 0.1, "vy": 0.0, "vz": 0.0, "wroll": 0.0, "wpitch": 0.0, "wyaw": 0.0, "hold": False,
           "issued_at_ms": 1000, "valid_for_ms": 250, "seq": 3, "frame_id": "f-1"}


# ------------------------------------------------------------------------------------------------------------- contract


def test_a_good_request_and_a_good_command_pass() -> None:
    validate_visual_target_request(REQUEST)
    validate_velocity_command(COMMAND)
    validate_visual_target_request({**REQUEST, "captured_at_utc": "2026-01-02T03:04:05Z", "calibration_version": "c1", "calibration_age_days": 3})
    validate_velocity_command({k: v for k, v in COMMAND.items() if k != "frame_id"})
    validate_velocity_command({**COMMAND, "frame_id": None})


@pytest.mark.parametrize("change", [
    {"schema_version": "2.0"}, {"frame_id": ""}, {"frame_id": "a b"}, {"confidence": 1.5}, {"confidence": True}, {"safety_state": "ready"}, {"safety_state": {"state": "READY"}},
    {"data_age_ms": -1}, {"captured_at_utc": "yesterday"}, {"current": {**POSE, "extra": 1}}, {"current": {"x": 0}}, {"target": [0, 0, 0, 0, 0, 0]},
    {"calibration_version": ""}, {"calibration_age_days": -1}, {"extra": 1},
])
def test_a_bad_request_is_refused(change: dict) -> None:
    with pytest.raises(ContractError):
        validate_visual_target_request({**REQUEST, **change})


def test_a_request_must_say_how_old_the_estimate_is() -> None:
    with pytest.raises(ContractError, match="how old"):
        validate_visual_target_request({k: v for k, v in REQUEST.items() if k != "data_age_ms"})
    with pytest.raises(ContractError, match="missing required field: current"):
        validate_visual_target_request({k: v for k, v in REQUEST.items() if k != "current"})
    with pytest.raises(ContractError):
        validate_visual_target_request([])


@pytest.mark.parametrize("change", [
    {"vx": float("nan")}, {"vx": "1"}, {"hold": 1}, {"issued_at_ms": -1}, {"issued_at_ms": 1.5}, {"valid_for_ms": 0}, {"valid_for_ms": 60001}, {"seq": -1}, {"seq": True},
    {"frame_id": "a b"}, {"extra": 1}, {"schema_version": "1"},
])
def test_a_bad_command_is_refused(change: dict) -> None:
    with pytest.raises(ContractError):
        validate_velocity_command({**COMMAND, **change})


def test_a_command_is_current_only_inside_its_validity() -> None:
    assert command_is_current(COMMAND, 1000) is True and command_is_current(COMMAND, 1250) is True
    assert command_is_current(COMMAND, 1251) is False and command_is_current(COMMAND, 999) is False  # from the future: not current either


def test_the_body_check_lets_the_limits_of_the_service_through_and_reads_the_state_object() -> None:
    assert check_request_body({"frame_id": "x"}) is False  # no schema_version: not checked
    assert check_request_body({**REQUEST, "gain": 0.5, "linear_tol": 0.01, "max_linear_speed": 0.1}) is True
    assert check_request_body({**REQUEST, "safety_state": {"state": "READY", "timestamp_utc": "2026-01-02T03:04:05Z"}}) is True
    with pytest.raises(ContractError):
        check_request_body({**REQUEST, "safety_state": {"state": "GO"}})
    with pytest.raises(ContractError, match="unsupported"):
        check_request_body({**REQUEST, "unknown": 1})


def test_the_constants_and_the_rules_are_those_of_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(__file__).resolve().parents[2] / "HYDRA-UMC-SDK" / "clients" / "python" / "src"
    if not (source / "hydra_umc_sdk" / "servoing.py").is_file():
        pytest.skip("the SDK checkout is not next to this repository")
    monkeypatch.setattr("sys.path", [str(source), *__import__("sys").path])
    sdk = importlib.import_module("hydra_umc_sdk.servoing")
    for name in ("SAFETY_STATES", "POSE_FIELDS", "MAX_VALID_FOR_MS"):
        assert getattr(contract, name) == getattr(sdk, name), name
    assert contract.REQUEST_REQUIRED == sdk._REQUEST_REQUIRED and contract.REQUEST_OPTIONAL == sdk._REQUEST_OPTIONAL
    assert contract.COMMAND_REQUIRED == sdk._COMMAND_REQUIRED and contract.COMMAND_OPTIONAL == sdk._COMMAND_OPTIONAL
    sdk.validate_visual_target_request(REQUEST)
    sdk.validate_velocity_command(COMMAND)
    assert sdk.command_is_current(COMMAND, 1100) == command_is_current(COMMAND, 1100)


# ------------------------------------------------------------------------------------------------- the service on the wire


@pytest.fixture
def served() -> Iterator[tuple[str, VisualServoingServer]]:
    server = VisualServoingServer(("127.0.0.1", 0), "service", accept_legacy_safety_string=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", server
    server.shutdown()
    server.server_close()


def _post(url: str, body: object) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_every_command_the_service_sends_follows_the_contract(served: tuple[str, VisualServoingServer]) -> None:
    base, _ = served
    status, answer = _post(base + "/correct", {"current": [0, 0, 0.1, 0, 0, 0], "target": [0.1, 0, 0.1, 0, 0, 0], "frame_id": "f-1"})
    assert status == 200 and answer["command"]["schema_version"] == "1.0"
    validate_velocity_command(answer["command"])
    status, answer = _post(base + "/request", {**REQUEST, "frame_id": "f-2"})
    assert status == 200 and answer["outcome"] == "accepted"
    validate_velocity_command(answer["command"])
    assert command_is_current(answer["command"], answer["command"]["issued_at_ms"] + 100)
    status, opened = _post(base + "/sessions", {"gain": 1.0, "max_linear_speed": 0.1, "max_angular_speed": 0.2})
    assert status == 201
    status, step = _post(base + f"/sessions/{opened['session_id']}/correct", {"current": [0, 0, 0, 0, 0, 0], "target": [0.05, 0, 0, 0, 0, 0], "frame_id": "f-3"})
    assert status == 200
    validate_velocity_command(step["command"])
    status, hold = _post(base + f"/sessions/{opened['session_id']}/vision-lost", {})
    assert status == 200 and hold["command"]["hold"] is True
    validate_velocity_command(hold["command"])


def test_a_frame_id_of_an_older_caller_is_still_answered(served: tuple[str, VisualServoingServer]) -> None:
    base, _ = served
    status, answer = _post(base + "/correct", {"current": [0, 0, 0, 0, 0, 0], "target": [0.1, 0, 0, 0, 0, 0], "frame_id": "frame 7 (left)"})
    assert status == 200 and answer["command"]["frame_id"] == "frame 7 (left)"


def test_a_request_that_declares_the_schema_is_checked_and_one_that_does_not_is_handled_as_before(served: tuple[str, VisualServoingServer]) -> None:
    base, _ = served
    status, answer = _post(base + "/request", {**REQUEST, "confidence": 5})
    assert status == 400 and "VisualTargetRequest contract" in answer["error"]
    status, answer = _post(base + "/request", {**REQUEST, "surprise": 1})
    assert status == 400 and "unsupported" in answer["error"]
    legacy = {k: v for k, v in REQUEST.items() if k != "schema_version"}
    legacy["current"] = [0, 0, 0.1, 0, 0, 0]
    legacy["frame_id"] = "legacy 1"
    assert _post(base + "/request", legacy)[0] == 200


def test_with_require_contract_a_request_without_the_schema_is_refused() -> None:
    server = VisualServoingServer(("127.0.0.1", 0), "service", accept_legacy_safety_string=True, require_contract=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        legacy = {k: v for k, v in REQUEST.items() if k != "schema_version"}
        status, answer = _post(base + "/request", legacy)
        assert status == 400 and "schema_version is required" in answer["error"]
        assert _post(base + "/request", REQUEST)[0] == 200
        status, opened = _post(base + "/sessions", {"gain": 1.0, "max_linear_speed": 0.1, "max_angular_speed": 0.2})
        assert _post(base + f"/sessions/{opened['session_id']}/request", {**legacy, "frame_id": "f-9"})[0] == 400
        assert _post(base + "/correct", {"current": [0, 0, 0, 0, 0, 0], "target": [0.1, 0, 0, 0, 0, 0]})[0] == 200  # /correct is not a VisualTargetRequest
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------------------------------------------- the bench


def _config(**change: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "session": {"gain": 2.0, "max_linear_speed": 0.2, "max_angular_speed": 0.4, "period_s": 0.1, "max_linear_acceleration": 1.0, "linear_tolerance": 0.001, "angular_tolerance": 0.01},
        "plant": {"command_delay_s": 0.05, "lag_s": 0.05, "velocity_scale": 1.0},
        "scenario": {"start": [0, 0, 0, 0, 0, 0], "target": [0.1, 0.05, -0.02, 0, 0, 0.3], "duration_s": 20, "seed": 3},
        "limits": {"max_settle_s": 15, "max_overshoot_m": 0.01, "max_peak_linear_speed": 0.25, "max_peak_angular_speed": 0.5},
    }
    for key, value in change.items():
        config[key] = {**config.get(key, {}), **value} if isinstance(value, dict) else value
    return config


def test_a_clean_approach_settles_without_overshoot_and_within_the_limits() -> None:
    result = run_bench(parse_bench_config(_config()))
    assert result["simulated"] is True and result["ok"] is True, result["problems"]
    assert result["settled"] and result["settle_time_s"] < 15
    assert result["overshoot_m"] <= 0.01 and result["final_linear_error_m"] < 0.002 and result["final_angular_error_rad"] < 0.02
    assert result["peak_linear_speed"] <= 0.2 + 1e-9 and result["states"]["tracking"] > 5 and result["trace"][0]["t"] == 0.0


def test_a_run_gives_the_same_numbers_every_time_and_the_noise_depends_on_the_seed() -> None:
    noisy = _config(plant={"noise_linear_m": 0.0005, "noise_angular_rad": 0.002, "dropout_probability": 0.1})
    a, b = run_bench(parse_bench_config(noisy)), run_bench(parse_bench_config(noisy))
    assert a == b
    other = run_bench(parse_bench_config(_config(plant=noisy["plant"], scenario={"seed": 4})))
    assert other["states"] != a["states"] or other["final_linear_error_m"] != a["final_linear_error_m"]
    assert a["states"].get("vision_lost", 0) > 0


def test_a_tool_that_moves_less_than_told_still_gets_there_and_a_dead_tool_does_not() -> None:
    slow = run_bench(parse_bench_config(_config(plant={"velocity_scale": 0.5})))
    assert slow["ok"] is True
    dead = run_bench(parse_bench_config(_config(plant={"velocity_scale": 0.0001}, scenario={"duration_s": 5})))
    assert dead["ok"] is False and dead["settled"] is False and "did not settle" in dead["problems"][0]


def test_a_big_delay_makes_the_loop_overshoot_and_the_verdict_says_so() -> None:
    sluggish = _config(session={"gain": 4.5, "period_s": 0.1, "max_gain_period": 0.5, "max_linear_acceleration": 5.0, "max_linear_speed": 1.0},
                       plant={"command_delay_s": 0.4, "lag_s": 0.3, "valid_for_ms": 5000}, limits={"max_overshoot_m": 0.001, "max_peak_linear_speed": 1.0},
                       scenario={"start": [0, 0, 0, 0, 0, 0], "target": [0.1, 0, 0, 0, 0, 0]})
    result = run_bench(parse_bench_config(sluggish))
    assert result["overshoot_m"] > 0.001 and result["ok"] is False and any("overshot" in p for p in result["problems"])


def test_a_command_that_arrives_after_its_validity_is_not_acted_on() -> None:
    config = _config(plant={"command_delay_s": 0.5, "valid_for_ms": 100}, scenario={"duration_s": 3})
    result = run_bench(parse_bench_config(config))
    assert result["peak_linear_speed"] == 0.0 and result["ok"] is False


def test_the_sweep_reports_each_gain_and_refuses_the_ones_the_session_refuses() -> None:
    sweep = sweep_gains(parse_bench_config(_config(sweep={"gains": [0.5, 2.0, 40.0]})))
    assert [r["gain"] for r in sweep["runs"]] == [0.5, 2.0, 40.0]
    assert "K*T" in sweep["runs"][2]["refused"] and sweep["runs"][2]["ok"] is False
    assert sweep["best_gain"] in (0.5, 2.0) and all("trace" not in r for r in sweep["runs"])
    assert sweep_gains(parse_bench_config(_config()))["runs"][0]["gain"] == 2.0  # without a list, the gain of the session


@pytest.mark.parametrize("config", [
    [], {"unknown": 1}, {"session": {"gain": 1.0}}, {"session": "x"}, _config(session={"gain": 0}), _config(session={"gain": 9.0}), _config(plant={"x": 1}),
    _config(plant={"velocity_scale": 0}), _config(plant={"dropout_probability": 2}), _config(plant={"noise_linear_m": -1}),
    _config(scenario={"start": [0, 0, 0]}), _config(scenario={"target": [True, 0, 0, 0, 0, 0]}), _config(scenario={"seed": 1.5}), _config(scenario={"duration_s": 0}),
    _config(scenario={"integration_step_s": 0.5}), _config(scenario={"duration_s": 3600, "integration_step_s": 0.001}),
    _config(limits={"max_settle_s": 0}), _config(sweep={"gains": "all"}), _config(sweep={"gains": [1] * 51}), _config(sweep={"gains": [True]}),
])
def test_a_bad_bench_file_is_refused(config: object) -> None:
    with pytest.raises(BenchError):
        parse_bench_config(config)


def test_the_command_runs_a_file_and_judges_it(tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    good = tmp_path / "bench.json"
    good.write_text(json.dumps(_config(sweep={"gains": [1.0, 2.0]})), encoding="utf-8")

    def run(*argv: str) -> int:
        monkeypatch.setattr("sys.argv", ["x", "bench", "--bench-config", *argv])
        return main()

    assert run(str(good)) == 0
    single = json.loads(capsys.readouterr().out)
    assert single["ok"] is True and "trace" not in single
    assert run(str(good), "--trace") == 0 and "trace" in json.loads(capsys.readouterr().out)
    assert run(str(good), "--sweep") == 0 and len(json.loads(capsys.readouterr().out)["runs"]) == 2
    failing = tmp_path / "fail.json"
    failing.write_text(json.dumps(_config(plant={"velocity_scale": 0.0001}, scenario={"duration_s": 3})), encoding="utf-8")
    assert run(str(failing)) == 1 and json.loads(capsys.readouterr().out)["ok"] is False
    bad = tmp_path / "bad.json"
    bad.write_text('{"session": NaN}', encoding="utf-8")
    assert run(str(bad)) == 2 and "not a valid JSON number" in capsys.readouterr().err
    assert run(str(tmp_path / "missing.json")) == 2
    with pytest.raises(BenchError):
        load_bench_config(tmp_path / "missing.json")


def test_the_bench_does_not_take_real_time() -> None:
    started = time.monotonic()
    run_bench(parse_bench_config(_config(scenario={"duration_s": 60})))
    assert time.monotonic() - started < 5
