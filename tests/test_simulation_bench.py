# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_simulation_bench.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Simulation bench: the real servoing session closed against a simulated tool, over a grid of faults drawn from a fixed seed.

The repository already has the bench of the loop (`bench.py`: a kinematic tool with delay, lag, noise, missed vision and a tool that moves less than told). This
file runs it over many cells at once and states what must hold in every one of them, whatever the faults: the speed of the tool never passes the
limits of the session, nothing is ever not a number, a total loss of vision leaves the tool still, the same seed gives the same run, and a gain the session refuses
is reported as refused. Nothing here says how a real robot, a real camera or a real network behave: that needs the cell."""
from __future__ import annotations

import math
import random
from typing import Any

import pytest

from hydra_umc_visual_servoing_api.bench import parse_bench_config, run_bench, sweep_gains

SEED = 20260101


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


def _faults(rng: random.Random) -> dict[str, Any]:
    return {
        "command_delay_s": round(rng.uniform(0.0, 0.3), 3),
        "lag_s": round(rng.uniform(0.0, 0.3), 3),
        "velocity_scale": round(rng.uniform(0.3, 1.0), 3),
        "noise_linear_m": round(rng.uniform(0.0, 0.002), 5),
        "noise_angular_rad": round(rng.uniform(0.0, 0.01), 5),
        "dropout_probability": round(rng.uniform(0.0, 0.5), 3),
        "valid_for_ms": rng.choice([250, 500, 2000]),
    }


def _targets(rng: random.Random) -> dict[str, Any]:
    return {"target": [round(rng.uniform(-0.15, 0.15), 3), round(rng.uniform(-0.15, 0.15), 3), round(rng.uniform(-0.1, 0.1), 3), 0, 0, round(rng.uniform(-0.5, 0.5), 3)],
            "seed": rng.randrange(1000), "duration_s": 15}


def test_whatever_faults_the_cell_has_the_tool_never_passes_the_limits_of_the_session_and_nothing_is_not_a_number() -> None:
    rng = random.Random(SEED)
    for run in range(40):
        config = _config(plant=_faults(rng), scenario=_targets(rng))
        result = run_bench(parse_bench_config(config))
        session = config["session"]
        label = (run, config["plant"])
        assert result["simulated"] is True
        assert result["peak_linear_speed"] <= session["max_linear_speed"] + 1e-9, label
        assert result["peak_angular_speed"] <= session["max_angular_speed"] + 1e-9, label
        for key in ("overshoot_m", "final_linear_error_m", "final_angular_error_rad", "peak_linear_speed", "peak_angular_speed"):
            assert math.isfinite(result[key]), (key, label)
        assert all(math.isfinite(point["linear_error"]) for point in result["trace"])
        assert result["ticks"] == sum(result["states"].values())


def test_the_same_cell_gives_the_same_run_and_another_seed_a_different_noise() -> None:
    config = _config(plant=_faults(random.Random(SEED)), scenario=_targets(random.Random(SEED)))
    assert run_bench(parse_bench_config(config)) == run_bench(parse_bench_config(config))
    other = _config(plant=config["plant"], scenario={**config["scenario"], "seed": config["scenario"]["seed"] + 1})
    a, b = run_bench(parse_bench_config(config)), run_bench(parse_bench_config(other))
    assert (a["states"], a["final_linear_error_m"]) != (b["states"], b["final_linear_error_m"])


def test_a_camera_that_never_sees_the_target_leaves_the_tool_where_it_was() -> None:
    result = run_bench(parse_bench_config(_config(plant={"dropout_probability": 1.0}, scenario={"duration_s": 5})))
    assert result["peak_linear_speed"] == 0.0 and result["peak_angular_speed"] == 0.0
    assert set(result["states"]) == {"vision_lost"} and result["settled"] is False and result["ok"] is False


def test_vision_that_comes_back_after_a_loss_still_gets_the_tool_there() -> None:
    result = run_bench(parse_bench_config(_config(plant={"dropout_probability": 0.3}, scenario={"duration_s": 25})))
    assert result["states"].get("vision_lost", 0) > 0 and result["settled"] is True


def test_more_noise_never_makes_the_final_position_better_on_average() -> None:
    def mean_error(noise: float) -> float:
        errors = [run_bench(parse_bench_config(_config(plant={"noise_linear_m": noise}, scenario={"seed": seed, "duration_s": 12})))["final_linear_error_m"] for seed in range(8)]
        return sum(errors) / len(errors)

    quiet, loud = mean_error(0.0), mean_error(0.004)
    assert quiet <= loud and quiet < 0.002


def test_a_tool_that_moves_less_than_it_is_told_is_never_faster_than_told() -> None:
    for scale in (0.3, 0.6, 1.0):
        result = run_bench(parse_bench_config(_config(plant={"velocity_scale": scale})))
        assert result["peak_linear_speed"] <= 0.2 * scale + 1e-6


def test_a_sweep_over_the_gains_reports_each_one_and_the_best_among_the_ones_that_pass() -> None:
    sweep = sweep_gains(parse_bench_config(_config(sweep={"gains": [0.25, 0.5, 1.0, 2.0, 3.0, 40.0, 400.0]})))
    assert [run["gain"] for run in sweep["runs"]] == [0.25, 0.5, 1.0, 2.0, 3.0, 40.0, 400.0]
    refused = [run for run in sweep["runs"] if "refused" in run]
    assert [run["gain"] for run in refused] == [40.0, 400.0] and all("K*T" in run["refused"] for run in refused)
    passing = [run for run in sweep["runs"] if run["ok"]]
    assert passing and sweep["best_gain"] == min(passing, key=lambda run: run["settle_time_s"])["gain"]
    assert all("trace" not in run for run in sweep["runs"])


@pytest.mark.parametrize("delay", [0.0, 0.1, 0.3, 0.6])
def test_a_longer_delay_never_makes_the_loop_settle_sooner(delay: float) -> None:
    base = run_bench(parse_bench_config(_config(plant={"command_delay_s": 0.0, "valid_for_ms": 5000})))
    delayed = run_bench(parse_bench_config(_config(plant={"command_delay_s": delay, "valid_for_ms": 5000})))
    if base["settled"] and delayed["settled"]:
        assert delayed["settle_time_s"] >= base["settle_time_s"] - 1e-9
