# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_session.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The servoing session against a simulated plant: an integrator (the tool moves by velocity times time) behind a delay, with noise.

These are tests of the loop, not of one error: they run whole approaches with a clock that is only a number, and check what a closed loop must
do - converge without overshoot at the default gain, be refused (not run) at a gain that would diverge, stop when the detections stop, not chatter
at the edge of the tolerance and not ask the tool for more acceleration than it has.
"""
from __future__ import annotations

import math
import random

import pytest

from hydra_umc_visual_servoing_api.pose import Pose6D
from hydra_umc_visual_servoing_api.servo import VelocityCommand, compute_pose_error, compute_velocity_command
from hydra_umc_visual_servoing_api.session import ZERO, ServoSession, SessionConfig

START = Pose6D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
TARGET = Pose6D(0.10, -0.05, 0.02, 0.0, 0.0, 0.2)


def config(**extra) -> SessionConfig:
    values = dict(gain=1.0, max_linear_speed=0.25, max_angular_speed=0.5, period_s=0.1, max_linear_acceleration=100.0, max_angular_acceleration=100.0)
    values.update(extra)
    return SessionConfig(**values)


class Plant:
    """An integrator behind a delay of `delay` periods: the pose moves by the velocity that was commanded `delay` periods ago."""

    def __init__(self, pose: Pose6D = START, delay: int = 0, noise: float = 0.0, seed: int = 1) -> None:
        self.pose = pose
        self.queue = [ZERO] * delay
        self.noise = noise
        self.random = random.Random(seed)

    def apply(self, command: VelocityCommand, period: float) -> None:
        self.queue.append(command)
        c = self.queue.pop(0)
        self.pose = Pose6D(
            self.pose.x + c.vx * period, self.pose.y + c.vy * period, self.pose.z + c.vz * period,
            self.pose.roll + c.wroll * period, self.pose.pitch + c.wpitch * period, self.pose.yaw + c.wyaw * period,
        )

    def measure(self) -> Pose6D:
        n = self.noise
        if n == 0.0:
            return self.pose
        g = self.random.gauss
        p = self.pose
        return Pose6D(p.x + g(0, n), p.y + g(0, n), p.z + g(0, n), p.roll + g(0, n), p.pitch + g(0, n), p.yaw + g(0, n))


def run(session: ServoSession, plant: Plant, steps: int, target: Pose6D = TARGET) -> list[tuple[float, float]]:
    """Runs the loop and returns (distance to the target, along the straight line) per step."""
    out = []
    t = 0.0
    for _ in range(steps):
        step = session.update(plant.measure(), target, t)
        plant.apply(step.command, session.config.period_s)
        e = compute_pose_error(plant.pose, target)
        out.append((e.linear_norm, e.angular_norm))
        t += session.config.period_s
    return out


# ---- convergence ----


@pytest.mark.parametrize("delay", [0, 1, 2])
def test_the_default_gain_converges_without_overshoot_even_with_a_delay(delay):
    session = ServoSession(config(filter_alpha=1.0))
    history = run(session, Plant(delay=delay), 150)
    linear = [h[0] for h in history]
    angular = [h[1] for h in history]
    assert linear[-1] < 0.005 and angular[-1] < 0.02, "it must reach the tolerance"
    # no overshoot: the distance to the target never grows once the approach has begun
    for series in (linear, angular):
        for before, after in zip(series, series[1:], strict=False):
            assert after <= before + 1e-9, "the distance to the target grew: the loop overshot"


def test_a_target_far_away_is_approached_at_the_speed_limit_then_slowed_down():
    session = ServoSession(config(filter_alpha=1.0))
    plant = Plant()
    far = Pose6D(2.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    speeds = []
    t = 0.0
    for _ in range(300):
        step = session.update(plant.measure(), far, t)
        plant.apply(step.command, 0.1)
        speeds.append(math.hypot(step.command.vx, step.command.vy, step.command.vz))
        t += 0.1
    assert max(speeds) <= 0.25 + 1e-9
    assert max(speeds) > 0.24, "far from the target the tool goes at the limit"
    assert speeds[-1] < 0.05


def test_a_gain_that_would_diverge_is_refused_at_the_start_not_run():
    # K*T above 2 diverges for an integrator: with T = 0.1 s a gain of 25 does
    with pytest.raises(ValueError, match="K\\*T"):
        config(gain=25.0)
    # and above the default bound (0.5) is refused too: it would overshoot
    with pytest.raises(ValueError, match="overshoot or diverge"):
        config(gain=8.0)
    # the bound can be moved by the operator, but not past it silently: 8 * 0.1 = 0.8 is allowed once the bound says so
    assert config(gain=8.0, max_gain_period=0.8).gain == 8.0


def test_the_law_itself_does_diverge_at_ktoo_big_which_is_what_the_check_is_for():
    # the simulation behind the refusal above: the bare law with K*T = 2.5 grows the error every step
    plant = Plant()
    error = []
    for _ in range(12):
        e = compute_pose_error(plant.pose, TARGET)
        error.append(e.linear_norm)
        plant.apply(compute_velocity_command(e, gain=25.0), 0.1)
    assert error[-1] > error[0] * 10


# ---- loss of detection ----


def test_when_the_detections_stop_the_command_is_zero_and_the_next_one_starts_from_rest():
    session = ServoSession(config(filter_alpha=1.0, vision_timeout_s=0.5, max_linear_acceleration=1.0))
    plant = Plant()
    t = 0.0
    for _ in range(5):
        step = session.update(plant.measure(), TARGET, t)
        plant.apply(step.command, 0.1)
        t += 0.1
    assert session.tick(t) != ZERO, "the last command is still good while it is fresh"
    assert session.tick(t + 0.3) != ZERO
    assert session.tick(t + 0.6) == ZERO, "no detection within the timeout: zero"
    # the detection comes back after a long pause: it does not continue the old command, it restarts from rest
    step = session.update(plant.measure(), TARGET, t + 5.0)
    assert step.state == "restarted"
    assert math.hypot(step.command.vx, step.command.vy, step.command.vz) <= 1.0 * 0.1 + 1e-9


def test_an_explicit_vision_lost_zeroes_the_command_and_forgets_the_filter():
    session = ServoSession(config())
    session.update(START, TARGET, 0.0)
    lost = session.vision_lost(0.1)
    assert lost.command == ZERO and lost.state == "vision_lost"
    assert session.tick(0.1) == ZERO
    assert session.update(START, TARGET, 0.2).state == "restarted"


def test_a_session_before_its_first_update_commands_nothing():
    assert ServoSession(config()).tick(10.0) == ZERO


# ---- dead band with hysteresis ----


def test_inside_the_tolerance_the_command_is_zero_and_stays_zero_until_the_error_grows_past_the_hysteresis():
    session = ServoSession(config(filter_alpha=1.0, linear_tolerance=0.01, hysteresis=3.0))
    target = Pose6D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    t = 0.0
    inside = session.update(Pose6D(0.005, 0, 0, 0, 0, 0), target, t)
    assert inside.state == "holding" and inside.command == ZERO and inside.converged
    # between the tolerance and 3 times the tolerance: still holding, no chatter
    for error in (0.012, 0.02, 0.029, 0.011):
        t += 0.1
        step = session.update(Pose6D(error, 0, 0, 0, 0, 0), target, t)
        assert step.command == ZERO and step.state == "holding", error
        assert not step.converged
    # past it: the tool moves again
    t += 0.1
    out = session.update(Pose6D(0.05, 0, 0, 0, 0, 0), target, t)
    assert out.state == "tracking" and out.command.vx < 0


def test_an_error_that_hovers_around_the_tolerance_does_not_make_the_command_flip():
    session = ServoSession(config(filter_alpha=1.0, linear_tolerance=0.01, hysteresis=2.0))
    target = Pose6D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    states = []
    t = 0.0
    for k in range(40):
        error = 0.01 + (0.004 if k % 2 else -0.004)  # 0.006 / 0.014, around the edge of 0.01
        states.append(session.update(Pose6D(error, 0, 0, 0, 0, 0), target, t).state)
        t += 0.1
    assert set(states) == {"holding"}, "after the first entry into the band the command stays zero"


# ---- acceleration ----


def test_the_command_never_changes_faster_than_the_acceleration_limit_and_starts_from_rest():
    session = ServoSession(config(filter_alpha=1.0, max_linear_acceleration=0.5, max_angular_acceleration=1.0))
    plant = Plant()
    far = Pose6D(2.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    previous = ZERO
    t = 0.0
    for _ in range(40):
        step = session.update(plant.measure(), far, t)
        c = step.command
        dv = math.hypot(c.vx - previous.vx, c.vy - previous.vy, c.vz - previous.vz)
        dw = math.hypot(c.wroll - previous.wroll, c.wpitch - previous.wpitch, c.wyaw - previous.wyaw)
        assert dv <= 0.5 * 0.1 + 1e-9, "linear acceleration over its limit"
        assert dw <= 1.0 * 0.1 + 1e-9, "angular acceleration over its limit"
        previous = c
        plant.apply(c, 0.1)
        t += 0.1
    assert math.hypot(previous.vx, previous.vy) > 0.2, "it did get up to speed"


# ---- filtering ----


def test_the_filter_makes_the_command_calmer_under_noise():
    def command_spread(alpha):
        session = ServoSession(config(filter_alpha=alpha, linear_tolerance=1e-6, angular_tolerance=1e-6))
        plant = Plant(noise=0.004, seed=7)
        commands = []
        t = 0.0
        for _ in range(120):
            step = session.update(plant.measure(), TARGET, t)
            plant.apply(step.command, 0.1)
            commands.append(step.command.vx)
            t += 0.1
        tail = commands[60:]
        mean = sum(tail) / len(tail)
        return math.sqrt(sum((c - mean) ** 2 for c in tail) / len(tail))

    assert command_spread(0.3) < command_spread(1.0) * 0.8


def test_angles_are_filtered_along_the_shortest_turn():
    session = ServoSession(config(filter_alpha=0.5))
    session.update(Pose6D(0, 0, 0, 0, 0, math.radians(179)), TARGET, 0.0)
    step = session.update(Pose6D(0, 0, 0, 0, 0, math.radians(-179)), TARGET, 0.1)
    # halfway between 179 and -179 degrees is 180 degrees, not 0
    assert abs(abs(step.filtered.yaw) - math.pi) < math.radians(1.5)


# ---- configuration ----


@pytest.mark.parametrize(
    "bad",
    [
        {"gain": 0.0},
        {"gain": float("nan")},
        {"period_s": 0.0},
        {"filter_alpha": 0.0},
        {"filter_alpha": 1.5},
        {"hysteresis": 0.5},
        {"vision_timeout_s": 0.01},
        {"max_linear_acceleration": -1.0},
        {"linear_tolerance": 0.0},
    ],
)
def test_a_configuration_that_cannot_work_is_refused(bad):
    with pytest.raises(ValueError):
        config(**bad)


def test_time_going_backwards_is_refused():
    session = ServoSession(config())
    session.update(START, TARGET, 1.0)
    with pytest.raises(ValueError, match="backwards"):
        session.update(START, TARGET, 0.5)
