# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/bench.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A test bench that closes the visual servoing loop against a simulated tool, to try gains before a real robot.

The loop is the one of a servoing session (session.py): a measured pose goes in, a bounded velocity command comes out. Here the measured pose does not come from a
camera: it comes from a simulated tool (the "plant") that moves with the commands it receives, with the faults a real cell has and a first test should not
leave out:

* the vision arrives every `vision_period_s` (the period of the session) and may be missed (`dropout_probability`: the session is told that vision was lost);
* the measurement has noise (`noise_linear_m`, `noise_angular_rad`, Gaussian, from a seeded generator, so a run is repeatable);
* a command takes `command_delay_s` to reach the tool, and the tool follows it with a first-order lag (`lag_s`);
* the tool does not move exactly as commanded (`velocity_scale`: 0.8 is a tool that moves 20 % less than it is told);
* a command is applied only while it is valid (`valid_for_ms` of the command): a late one is not acted on, the tool stops, as a real consumer does.

All the numbers come from a configuration file (`bench --config FILE`); nothing here is read from the code. The run reports how the approach went (time to settle,
overshoot, peak speed and acceleration, the states of the session) and a verdict against the limits the file states. `sweep` repeats the run for a list of gains,
and a gain that the session refuses (K*T too high) is reported as refused with the reason.

This is a kinematic model of the tool in Cartesian space: it knows nothing of joints, collisions or the dynamics of a real robot. It does not replace the digital
twin (HYDRA-UMC-TWIN, PHYSICS-REPLICA, with HIL-BRIDGE as interlock): the plant here is a stand-in with the same interface a twin would have (`Plant.apply` and
`Plant.measure`), and coupling it to the twin needs a Cartesian command interface of the twin that does not exist yet.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .orientation import active_policy
from .pose import Pose6D
from .rotation import Quaternion
from .session import ServoSession, SessionConfig
from .servo import VelocityCommand, compute_pose_error

MAX_STEPS = 200_000
MAX_SWEEP = 50
MAX_CONFIG_BYTES = 64 * 1024
_TOP = {"session", "plant", "scenario", "limits", "sweep"}
_SESSION = {"gain", "max_linear_speed", "max_angular_speed", "period_s", "max_gain_period", "filter_alpha", "hysteresis", "vision_timeout_s",
            "max_linear_acceleration", "max_angular_acceleration", "linear_tolerance", "angular_tolerance"}
_PLANT = {"command_delay_s", "lag_s", "velocity_scale", "noise_linear_m", "noise_angular_rad", "dropout_probability", "valid_for_ms"}
_SCENARIO = {"start", "target", "duration_s", "seed", "integration_step_s"}
_LIMITS = {"max_settle_s", "max_overshoot_m", "max_overshoot_rad", "max_peak_linear_speed", "max_peak_angular_speed"}
_SWEEP = {"gains"}


class BenchError(ValueError):
    """The configuration of the bench or a run of it cannot be used."""


def _section(config: dict, key: str, allowed: set[str]) -> dict:
    section = config.get(key, {})
    if not isinstance(section, dict) or set(section) - allowed:
        raise BenchError(f"{key} is an object with the keys {sorted(allowed)}")
    return section


def _number(section: dict, key: str, default: float, low: float, high: float, *, low_open: bool = False) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value > high or (value <= low if low_open else value < low):
        raise BenchError(f"{key} must be a number {'above' if low_open else 'from'} {low:g} and at most {high:g}")
    return float(value)


def _pose(value: object, what: str) -> Pose6D:
    if not isinstance(value, list) or len(value) != 6 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise BenchError(f"{what} is a list of six finite numbers (x, y, z in meters; roll, pitch, yaw in radians)")
    return Pose6D(*[float(v) for v in value])  # type: ignore[arg-type]


@dataclass(frozen=True)
class PlantConfig:
    command_delay_s: float = 0.05
    lag_s: float = 0.05
    velocity_scale: float = 1.0
    noise_linear_m: float = 0.0
    noise_angular_rad: float = 0.0
    dropout_probability: float = 0.0
    valid_for_ms: float = 250.0


@dataclass(frozen=True)
class Scenario:
    start: Pose6D
    target: Pose6D
    duration_s: float = 10.0
    seed: int = 1
    integration_step_s: float = 0.01


@dataclass(frozen=True)
class Limits:
    max_settle_s: float = 8.0
    max_overshoot_m: float = 0.005
    max_overshoot_rad: float = 0.05
    max_peak_linear_speed: float = 0.25
    max_peak_angular_speed: float = 0.5


@dataclass(frozen=True)
class BenchConfig:
    session: SessionConfig
    plant: PlantConfig
    scenario: Scenario
    limits: Limits
    gains: tuple[float, ...] = field(default=())


def parse_bench_config(config: object) -> BenchConfig:
    if not isinstance(config, dict) or set(config) - _TOP:
        raise BenchError(f"the configuration is an object with the keys {sorted(_TOP)}")
    s = _section(config, "session", _SESSION)
    if "gain" not in s or "max_linear_speed" not in s or "max_angular_speed" not in s:
        raise BenchError("session needs gain, max_linear_speed and max_angular_speed")
    try:
        session = SessionConfig(**{k: float(v) if not isinstance(v, bool) and isinstance(v, (int, float)) else v for k, v in s.items()})
    except (TypeError, ValueError) as error:
        raise BenchError(f"session: {error}") from None
    p = _section(config, "plant", _PLANT)
    plant = PlantConfig(
        command_delay_s=_number(p, "command_delay_s", 0.05, 0, 5), lag_s=_number(p, "lag_s", 0.05, 0, 5), velocity_scale=_number(p, "velocity_scale", 1.0, 0, 10, low_open=True),
        noise_linear_m=_number(p, "noise_linear_m", 0.0, 0, 1), noise_angular_rad=_number(p, "noise_angular_rad", 0.0, 0, 1),
        dropout_probability=_number(p, "dropout_probability", 0.0, 0, 1), valid_for_ms=_number(p, "valid_for_ms", 250.0, 0, 60000, low_open=True),
    )
    sc = _section(config, "scenario", _SCENARIO)
    if "start" not in sc or "target" not in sc:
        raise BenchError("scenario needs start and target")
    seed = sc.get("seed", 1)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise BenchError("seed must be an integer")
    scenario = Scenario(_pose(sc["start"], "start"), _pose(sc["target"], "target"), _number(sc, "duration_s", 10.0, 0, 3600, low_open=True), seed,
                        _number(sc, "integration_step_s", 0.01, 0, 0.1, low_open=True))
    if scenario.duration_s / scenario.integration_step_s > MAX_STEPS:
        raise BenchError(f"the run would take more than {MAX_STEPS} integration steps: shorten duration_s or enlarge integration_step_s")
    if scenario.integration_step_s > session.period_s:
        raise BenchError("integration_step_s must not be longer than the period of the session")
    lim = _section(config, "limits", _LIMITS)
    limits = Limits(_number(lim, "max_settle_s", 8.0, 0, 3600, low_open=True), _number(lim, "max_overshoot_m", 0.005, 0, 10), _number(lim, "max_overshoot_rad", 0.05, 0, 7),
                    _number(lim, "max_peak_linear_speed", 0.25, 0, 100, low_open=True), _number(lim, "max_peak_angular_speed", 0.5, 0, 100, low_open=True))
    sw = _section(config, "sweep", _SWEEP)
    gains = sw.get("gains", [])
    if not isinstance(gains, list) or len(gains) > MAX_SWEEP or any(isinstance(g, bool) or not isinstance(g, (int, float)) for g in gains):
        raise BenchError(f"sweep.gains is a list of at most {MAX_SWEEP} numbers")
    return BenchConfig(session, plant, scenario, limits, tuple(float(g) for g in gains))


def load_bench_config(path: str | Path) -> BenchConfig:
    file = Path(path)
    try:
        if file.stat().st_size > MAX_CONFIG_BYTES:
            raise BenchError(f"the configuration file is larger than {MAX_CONFIG_BYTES} bytes")

        def refuse(name: str) -> float:
            raise ValueError(f"{name} is not a valid JSON number")

        data = json.loads(file.read_text(encoding="utf-8"), parse_constant=refuse)
    except OSError as error:
        raise BenchError(f"the configuration file cannot be read: {error.strerror}") from None
    except ValueError as error:
        if isinstance(error, BenchError):
            raise
        raise BenchError(f"the configuration file is not valid JSON: {error}") from None
    return parse_bench_config(data)


# ----------------------------------------------------------------------------------------------------------------------- plant


def _rotation(vector: tuple[float, float, float], dt: float) -> Quaternion:
    """The rotation of `vector * dt` (axis times angle, radians)."""
    wx, wy, wz = (vector[0] * dt, vector[1] * dt, vector[2] * dt)
    angle = math.sqrt(wx * wx + wy * wy + wz * wz)
    if angle < 1e-12:
        return Quaternion(1.0, wx / 2.0, wy / 2.0, wz / 2.0).normalized()
    s = math.sin(angle / 2.0) / angle
    return Quaternion(math.cos(angle / 2.0), wx * s, wy * s, wz * s)


class Plant:
    """The simulated tool: it integrates the velocity it is given, after a delay and through a first-order lag."""

    def __init__(self, config: PlantConfig, start: Pose6D, rng: random.Random) -> None:
        self.config = config
        self.pose = start
        self._rng = rng
        self._velocity = [0.0] * 6  # the velocity the tool really has
        self._queue: list[tuple[float, VelocityCommand, float]] = []  # (arrival time, command, time it stops being valid)
        self._applied = VelocityCommand(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self._valid_until = 0.0
        self.peak_linear = 0.0
        self.peak_angular = 0.0
        self.peak_linear_acceleration = 0.0
        self.peak_angular_acceleration = 0.0

    def send(self, command: VelocityCommand, now_s: float) -> None:
        """A command leaves the controller now; it reaches the tool after the delay and is valid for `valid_for_ms` from its issue."""
        self._queue.append((now_s + self.config.command_delay_s, command, now_s + self.config.valid_for_ms / 1000.0))

    def advance(self, now_s: float, dt: float) -> None:
        while self._queue and self._queue[0][0] <= now_s:
            _, self._applied, self._valid_until = self._queue.pop(0)
        cmd = self._applied if now_s <= self._valid_until else VelocityCommand(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)  # a command that is no longer valid is not acted on
        wanted = [c * self.config.velocity_scale for c in (cmd.vx, cmd.vy, cmd.vz, cmd.wroll, cmd.wpitch, cmd.wyaw)]
        alpha = 1.0 if self.config.lag_s <= 0 else 1.0 - math.exp(-dt / self.config.lag_s)
        previous = list(self._velocity)
        self._velocity = [v + alpha * (w - v) for v, w in zip(self._velocity, wanted, strict=True)]
        linear, angular = math.hypot(*self._velocity[:3]), math.hypot(*self._velocity[3:])
        self.peak_linear, self.peak_angular = max(self.peak_linear, linear), max(self.peak_angular, angular)
        self.peak_linear_acceleration = max(self.peak_linear_acceleration, math.hypot(*(a - b for a, b in zip(self._velocity[:3], previous[:3], strict=True))) / dt)
        self.peak_angular_acceleration = max(self.peak_angular_acceleration, math.hypot(*(a - b for a, b in zip(self._velocity[3:], previous[3:], strict=True))) / dt)
        step = _rotation((self._velocity[3], self._velocity[4], self._velocity[5]), dt)
        q = self.pose.quaternion
        moved = (step * q) if active_policy().frame == "base" else (q * step)  # the frame of the angular velocity is the one the service is configured with
        self.pose = Pose6D.from_quaternion(self.pose.x + self._velocity[0] * dt, self.pose.y + self._velocity[1] * dt, self.pose.z + self._velocity[2] * dt, moved)

    def measure(self) -> Pose6D:
        """What the camera would report: the true pose with Gaussian noise."""
        c = self.config
        if c.noise_linear_m == 0.0 and c.noise_angular_rad == 0.0:
            return self.pose
        noise = _rotation((self._rng.gauss(0, c.noise_angular_rad), self._rng.gauss(0, c.noise_angular_rad), self._rng.gauss(0, c.noise_angular_rad)), 1.0)
        return Pose6D.from_quaternion(self.pose.x + self._rng.gauss(0, c.noise_linear_m), self.pose.y + self._rng.gauss(0, c.noise_linear_m),
                                      self.pose.z + self._rng.gauss(0, c.noise_linear_m), noise * self.pose.quaternion)


# ------------------------------------------------------------------------------------------------------------------------- run


def run_bench(config: BenchConfig, session_override: SessionConfig | None = None) -> dict[str, Any]:
    """One approach, from `scenario.start` to `scenario.target`, as a summary. The simulated clock makes it take no real time and give the same numbers every time."""
    session_config = session_override or config.session
    sc, plant_cfg = config.scenario, config.plant
    rng = random.Random(sc.seed)
    plant = Plant(plant_cfg, sc.start, rng)
    session = ServoSession(session_config)
    initial = compute_pose_error(sc.start, sc.target)
    direction = (initial.dx, initial.dy, initial.dz)
    initial_linear = initial.linear_norm
    dt = sc.integration_step_s
    steps = int(round(sc.duration_s / dt))
    ticks_every = max(1, int(round(session_config.period_s / dt)))
    states: dict[str, int] = {}
    settle_time: float | None = None
    stayed_inside_since: float | None = None
    max_overshoot_m = 0.0
    max_angular_error = 0.0
    final_error = initial
    trace: list[dict[str, float]] = []
    for step in range(steps):
        now = step * dt
        if step % ticks_every == 0:
            if rng.random() < plant_cfg.dropout_probability:
                result = session.vision_lost(now)
            else:
                result = session.update(plant.measure(), sc.target, now)
            states[result.state] = states.get(result.state, 0) + 1
            plant.send(result.command, now)
            true_error = compute_pose_error(plant.pose, sc.target)
            final_error = true_error
            if initial_linear > 0:
                along = -(true_error.dx * direction[0] + true_error.dy * direction[1] + true_error.dz * direction[2]) / initial_linear
                max_overshoot_m = max(max_overshoot_m, along)
            max_angular_error = max(max_angular_error, true_error.angular_norm)
            inside = true_error.linear_norm <= session_config.linear_tolerance and true_error.angular_norm <= session_config.angular_tolerance
            if inside:
                stayed_inside_since = now if stayed_inside_since is None else stayed_inside_since
                if settle_time is None and now - stayed_inside_since >= session_config.period_s * 3:
                    settle_time = stayed_inside_since
            else:
                stayed_inside_since = None
                settle_time = None
            if len(trace) < 600:
                trace.append({"t": round(now, 6), "linear_error": true_error.linear_norm, "angular_error": true_error.angular_norm})
        plant.advance(now + dt, dt)
    lim = config.limits
    problems = []
    if settle_time is None:
        problems.append(f"it did not settle within {sc.duration_s:g} s (final error {final_error.linear_norm:.4g} m, {final_error.angular_norm:.4g} rad)")
    elif settle_time > lim.max_settle_s:
        problems.append(f"it settled after {settle_time:.2f} s, above {lim.max_settle_s:g} s")
    if max_overshoot_m > lim.max_overshoot_m:
        problems.append(f"it overshot by {max_overshoot_m:.4g} m, above {lim.max_overshoot_m:g} m")
    if plant.peak_linear > lim.max_peak_linear_speed:
        problems.append(f"the tool reached {plant.peak_linear:.4g} m/s, above {lim.max_peak_linear_speed:g} m/s")
    if plant.peak_angular > lim.max_peak_angular_speed:
        problems.append(f"the tool reached {plant.peak_angular:.4g} rad/s, above {lim.max_peak_angular_speed:g} rad/s")
    return {
        "simulated": True, "gain": session_config.gain, "k_times_t": session_config.gain * session_config.period_s,
        "ok": not problems, "problems": problems, "settled": settle_time is not None, "settle_time_s": settle_time,
        "overshoot_m": max_overshoot_m, "max_angular_error_rad": max_angular_error,
        "final_linear_error_m": final_error.linear_norm, "final_angular_error_rad": final_error.angular_norm,
        "peak_linear_speed": plant.peak_linear, "peak_angular_speed": plant.peak_angular,
        "peak_linear_acceleration": plant.peak_linear_acceleration, "peak_angular_acceleration": plant.peak_angular_acceleration,
        "states": states, "ticks": sum(states.values()), "trace": trace,
    }


def sweep_gains(config: BenchConfig) -> dict[str, Any]:
    """One run for each gain of `sweep.gains` (the gain of the session when the list is empty). A gain the session refuses is reported as refused."""
    gains = config.gains or (config.session.gain,)
    runs: list[dict[str, Any]] = []
    for gain in gains:
        try:
            session = SessionConfig(**{**config.session.__dict__, "gain": float(gain)})
        except ValueError as error:
            runs.append({"gain": float(gain), "refused": str(error), "ok": False})
            continue
        result = run_bench(config, session)
        result.pop("trace")
        runs.append(result)
    passing = [r for r in runs if r["ok"]]
    best = min(passing, key=lambda r: float(r["settle_time_s"])) if passing else None
    return {"simulated": True, "runs": runs, "best_gain": best["gain"] if best else None}
