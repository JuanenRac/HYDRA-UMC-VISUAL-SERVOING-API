# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_main_commands.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The command line in this process (test_cli.py runs it in a child process, which a coverage run does not see): `correct`, `request`, `bench`
and the refusals of `serve` before the port is bound. The HTTP server class is replaced by a recorder, so nothing here opens a socket."""
from __future__ import annotations

import json
import sys

import pytest

from hydra_umc_visual_servoing_api import main as servo_main
from hydra_umc_visual_servoing_api import orientation

CURRENT = "0,0,0,0,0,0"
TARGET = "0.1,0.05,-0.02,0,0,0.3"
TOKEN = "k" * 24
BENCH = {
    "session": {"gain": 2.0, "max_linear_speed": 0.2, "max_angular_speed": 0.4, "period_s": 0.1, "max_linear_acceleration": 1.0, "linear_tolerance": 0.001, "angular_tolerance": 0.01},
    "plant": {"command_delay_s": 0.05, "lag_s": 0.05, "velocity_scale": 1.0},
    "scenario": {"start": [0, 0, 0, 0, 0, 0], "target": [0.1, 0.05, -0.02, 0, 0, 0.3], "duration_s": 20, "seed": 3},
    "limits": {"max_settle_s": 15, "max_overshoot_m": 0.01, "max_peak_linear_speed": 0.25, "max_peak_angular_speed": 0.5},
}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """The orientation policy is process-wide: put it back after each test, and keep the environment out of the way."""
    saved = orientation.active_policy()
    for name in ("HYDRA_UMC_VISUAL_SERVOING_CONFIG", "HYDRA_UMC_VISUAL_SERVOING_TOKEN", "HYDRA_UMC_SAFETY_ZONES_URL",
                 "HYDRA_UMC_SAFETY_ZONES_TOKEN", "HYDRA_UMC_VISUAL_SERVOING_API_IDENTITY_KEY_FILE", "HYDRA_UMC_VISUAL_SERVOING_API_HEALTH_OPEN"):
        monkeypatch.delenv(name, raising=False)
    yield
    orientation.set_active_policy(saved)


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["hydra-umc-visual-servoing-api", *argv])
    return servo_main.main()


def write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return str(path)


class RecordingServer:
    """Stands in for the HTTP server: records how it was built and ends the serving loop at once, like a Ctrl+C."""
    built: list = []

    def __init__(self, address, role, limits, token, **kwargs):
        self.address, self.role, self.limits, self.token, self.kwargs = address, role, limits, token, kwargs
        self.closed = False
        RecordingServer.built.append(self)

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


@pytest.fixture
def server(monkeypatch):
    RecordingServer.built = []
    monkeypatch.setattr(servo_main, "VisualServoingServer", RecordingServer)
    return RecordingServer.built


def test_version_text_falls_back_when_not_installed(monkeypatch):
    def missing(_name):
        raise servo_main.PackageNotFoundError()
    monkeypatch.setattr(servo_main, "version", missing)
    assert "not installed" in servo_main.get_version()


def test_bare_invocation_prints_the_role(monkeypatch, capsys):
    assert run(monkeypatch) == 0
    assert servo_main.ROLE in capsys.readouterr().out


# ---- correct ----

def test_correct_prints_the_error_and_the_command(monkeypatch, capsys):
    assert run(monkeypatch, "correct", "--current", CURRENT, "--target", TARGET) == 0
    out = capsys.readouterr().out
    assert "pose error" in out and "velocity cmd" in out and "converged    : False" in out
    assert run(monkeypatch, "correct", "--current", CURRENT, "--target", CURRENT) == 0
    assert "converged    : True" in capsys.readouterr().out


def test_correct_refuses_a_pose_that_does_not_parse_and_a_bad_gain(monkeypatch, capsys):
    assert run(monkeypatch, "correct", "--current", "1,2", "--target", TARGET) == 1
    assert "error" in capsys.readouterr().err
    assert run(monkeypatch, "correct", "--current", CURRENT, "--target", TARGET, "--gain", "-1") == 1
    assert "error" in capsys.readouterr().err


def test_a_bad_configuration_file_stops_every_command_with_2(monkeypatch, capsys, tmp_path):
    bad = write(tmp_path, "bad.json", '{"orientation": 3}')
    missing = str(tmp_path / "nope.json")
    for config in (bad, missing):
        assert run(monkeypatch, "correct", "--current", CURRENT, "--target", TARGET, "--config", config) == 2
        assert "error" in capsys.readouterr().err
    assert run(monkeypatch, "request", "--current", CURRENT, "--target", TARGET, "--frame-id", "f", "--confidence", "0.9",
               "--data-age-ms", "10", "--safety-state", "READY", "--config", bad) == 2
    assert run(monkeypatch, "bench", "--bench-config", missing, "--config", bad) == 2
    assert run(monkeypatch, "serve", "--config", bad) == 2


# ---- request ----

def request(monkeypatch, *extra, confidence="0.9", age="10", state="READY"):
    return run(monkeypatch, "request", "--current", CURRENT, "--target", TARGET, "--frame-id", "frame-1", "--confidence", confidence,
               "--data-age-ms", age, "--safety-state", state, *extra)


def test_request_accepted_prints_the_correction(monkeypatch, capsys):
    assert request(monkeypatch) == 0
    out = capsys.readouterr().out
    assert "ACCEPTED" in out and "velocity cmd" in out


def test_request_not_ready_is_inhibited_with_2_and_nothing_is_computed(monkeypatch, capsys):
    assert request(monkeypatch, state="INHIBITED") == 2
    out = capsys.readouterr().out
    assert "INHIBITED" in out and "velocity cmd" not in out


def test_request_with_a_weak_or_old_frame_is_rejected_with_1(monkeypatch, capsys):
    assert request(monkeypatch, confidence="0.1") == 1
    assert "velocity cmd" not in capsys.readouterr().out
    assert request(monkeypatch, age="5000") == 1
    assert "velocity cmd" not in capsys.readouterr().out


def test_request_refuses_values_the_request_cannot_hold(monkeypatch, capsys):
    assert run(monkeypatch, "request", "--current", "x", "--target", TARGET, "--frame-id", "f", "--confidence", "0.9",
               "--data-age-ms", "1", "--safety-state", "READY") == 1
    assert request(monkeypatch, confidence="7") == 1
    assert request(monkeypatch, "--min-confidence", "5") == 1
    assert request(monkeypatch, "--gain", "-2") == 1
    assert "error" in capsys.readouterr().err


# ---- bench ----

def test_bench_prints_json_and_leaves_the_trace_out_unless_asked(monkeypatch, capsys, tmp_path):
    config = write(tmp_path, "bench.json", BENCH)
    assert run(monkeypatch, "bench", "--bench-config", config) == 0
    plain = json.loads(capsys.readouterr().out)
    assert plain["ok"] is True and "trace" not in plain
    assert run(monkeypatch, "bench", "--bench-config", config, "--trace") == 0
    assert json.loads(capsys.readouterr().out)["trace"]


def test_bench_exits_1_when_the_limits_are_not_met_and_2_for_a_bad_file(monkeypatch, capsys, tmp_path):
    tight = {**BENCH, "limits": {**BENCH["limits"], "max_settle_s": 0.2}}
    assert run(monkeypatch, "bench", "--bench-config", write(tmp_path, "tight.json", tight)) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert run(monkeypatch, "bench", "--bench-config", write(tmp_path, "junk.json", "{not json")) == 2
    assert "error" in capsys.readouterr().err


def test_bench_sweep_reports_every_gain(monkeypatch, capsys, tmp_path):
    sweep = {**BENCH, "sweep": {"gains": [0.5, 1.0, 2.0]}}
    code = run(monkeypatch, "bench", "--bench-config", write(tmp_path, "sweep.json", sweep), "--sweep")
    result = json.loads(capsys.readouterr().out)
    assert len(result["runs"]) == 3
    assert code == (0 if any(r["ok"] for r in result["runs"]) else 1)


# ---- serve: the refusals ----

def test_serve_refuses_limits_that_cannot_hold(monkeypatch, capsys, server):
    assert run(monkeypatch, "serve", "--min-confidence", "9") == 1
    assert run(monkeypatch, "serve", "--max-gain", "-1") == 1
    assert "error" in capsys.readouterr().err
    assert server == []


def test_serve_refuses_a_bad_token_an_open_address_and_a_bad_identity_key(monkeypatch, capsys, tmp_path, server):
    assert run(monkeypatch, "serve", "--token-file", write(tmp_path, "short.txt", "abc")) == 2
    assert run(monkeypatch, "serve", "--token-file", str(tmp_path / "none.txt")) == 2
    assert run(monkeypatch, "serve", "--identity-key-file", str(tmp_path / "none.key")) == 2
    capsys.readouterr()
    assert run(monkeypatch, "serve", "--addr", "0.0.0.0") == 2
    assert "not loopback" in capsys.readouterr().err
    assert server == []


def test_serve_refuses_broken_policy_sections(monkeypatch, capsys, tmp_path, server):
    for section in ("ibvs", "servo_log"):
        assert run(monkeypatch, "serve", "--config", write(tmp_path, f"{section}.json", {section: 5})) == 2
        assert "error" in capsys.readouterr().err
    assert server == []


def test_serve_refuses_a_safety_zones_client_that_cannot_be_built(monkeypatch, capsys, tmp_path, server):
    assert run(monkeypatch, "serve", "--safety-zones-url", "ftp://nowhere") == 2
    assert run(monkeypatch, "serve", "--safety-zones-url", "http://127.0.0.1:1", "--safety-zones-token-file", str(tmp_path / "none.txt")) == 2
    assert "error" in capsys.readouterr().err
    assert server == []


def test_serve_refuses_a_verified_verdict_without_a_safety_zones_address(monkeypatch, capsys, tmp_path, server):
    config = write(tmp_path, "verify.json", {"safety_state_verification": {"enabled": True, "key_file": "key.txt"}})
    assert run(monkeypatch, "serve", "--config", config) == 2
    assert "SAFETY-ZONES" in capsys.readouterr().err
    assert server == []


# ---- serve: the start ----

def test_serve_starts_closed_by_default_and_shuts_down(monkeypatch, capsys, server):
    assert run(monkeypatch, "serve", "--port", "18999") == 0
    built = server[0]
    assert built.address == ("127.0.0.1", 18999) and built.token == "" and built.closed
    assert built.kwargs["safety_zones"] is None and built.kwargs["state_verifier"] is None and built.kwargs["require_contract"] is False
    assert built.health_open is False and built.identity_key is None
    out = capsys.readouterr().out
    assert "no token, loopback only" in out and "shutting down" in out and "limits fixed at start-up" in out


def test_serve_with_every_switch_on_says_what_it_trusts(monkeypatch, capsys, tmp_path, server):
    token = write(tmp_path, "token.txt", TOKEN)
    zones_token = write(tmp_path, "zones.txt", "z" * 20)
    key = write(tmp_path, "verdict.key", "v" * 40)
    config = write(tmp_path, "cfg.json", {"safety_state_verification": {"enabled": True, "key_file": key}})
    assert run(monkeypatch, "serve", "--addr", "0.0.0.0", "--token-file", token, "--require-contract", "--health-open",
               "--safety-zones-url", "http://127.0.0.1:9", "--safety-zones-token-file", zones_token, "--config", config) == 0
    built = server[0]
    assert built.token == TOKEN and built.kwargs["require_contract"] is True and built.health_open is True
    assert built.kwargs["safety_zones"] is not None and built.kwargs["state_verifier"] is not None
    out = capsys.readouterr().out
    assert "Bearer token required" in out and "VisualTargetRequest" in out and "must be signed" in out and "asked of SAFETY-ZONES" in out


def test_serve_takes_the_safety_zones_address_and_token_from_the_environment(monkeypatch, server):
    monkeypatch.setenv("HYDRA_UMC_SAFETY_ZONES_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HYDRA_UMC_SAFETY_ZONES_TOKEN", "e" * 20)
    assert run(monkeypatch, "serve") == 0
    assert server[0].kwargs["safety_zones"] is not None
