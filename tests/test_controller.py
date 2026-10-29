import math

import pytest

from hydra_umc_visual_servoing_api.controller import ControllerConfig, ServoController
from hydra_umc_visual_servoing_api.servo import PoseError


def _cfg(**over):
    base = dict(gain=2.0, period_s=0.1, max_linear_speed=0.25, max_angular_speed=0.5,
                max_linear_accel=1.0, max_angular_accel=2.0, filter_alpha=1.0, vision_timeout_s=0.5)
    base.update(over)
    return ControllerConfig(**base)


def _err(dx=0.0, droll=0.0):
    return PoseError(dx, 0.0, 0.0, droll, 0.0, 0.0)


def test_gain_times_period_above_the_limit_is_refused():
    with pytest.raises(ValueError, match="gain x period"):
        _cfg(gain=6.0, period_s=0.1)
    _cfg(gain=5.0, period_s=0.1)  # exactly 0.5 is allowed


@pytest.mark.parametrize("over", [{"gain": 0}, {"period_s": float("nan")}, {"filter_alpha": 0}, {"filter_alpha": 1.5},
                                  {"linear_dead_in": -1}, {"linear_dead_in": 0.1, "linear_dead_out": 0.05},
                                  {"vision_timeout_s": True}])
def test_bad_config_values_are_refused(over):
    with pytest.raises(ValueError):
        _cfg(**over)


def test_acceleration_limit_ramps_the_command():
    c = ServoController(_cfg(max_linear_accel=1.0))
    first = c.step(_err(dx=1.0), 0.0)
    assert first.vx == pytest.approx(0.1)  # 1.0 m/s^2 x 0.1 s, though the law asks for the 0.25 clamp
    second = c.step(_err(dx=1.0), 0.1)
    assert second.vx == pytest.approx(0.2)
    assert c.step(_err(dx=1.0), 0.2).vx == pytest.approx(0.25)


def test_vision_loss_forces_zero_after_the_timeout_and_the_ramp_restarts():
    c = ServoController(_cfg(max_linear_accel=100.0))
    assert c.step(_err(dx=1.0), 0.0).vx > 0
    assert c.step(None, 0.3).vx > 0  # still inside the timeout: the last filtered error keeps being used
    assert c.step(None, 0.6).vx == 0.0  # 0.6 s without a measurement
    assert c.step(None, 0.7).vx == 0.0  # and stays zero until a measurement arrives
    assert c.step(_err(dx=1.0), 0.8).vx > 0


def test_a_controller_that_never_saw_a_measurement_outputs_zero():
    assert ServoController(_cfg()).step(None, 0.0).vx == 0.0


def test_dead_band_has_hysteresis():
    c = ServoController(_cfg(max_linear_accel=100.0, linear_dead_in=0.01, linear_dead_out=0.03,
                             angular_dead_in=0.05, angular_dead_out=0.05))
    assert c.step(_err(dx=0.005), 0.0).vx == 0.0  # inside: zero
    assert c.step(_err(dx=0.02), 0.1).vx == 0.0  # between in and out: still zero
    assert c.step(_err(dx=0.04), 0.2).vx > 0  # past out: moving
    assert c.step(_err(dx=0.02), 0.3).vx > 0  # between in and out while moving: keeps moving
    assert c.step(_err(dx=0.005), 0.4).vx == 0.0


def test_filter_smooths_a_jump_in_the_measurement():
    c = ServoController(_cfg(filter_alpha=0.5, max_linear_accel=100.0, max_linear_speed=10.0))
    c.step(_err(dx=0.0), 0.0)
    assert c.step(_err(dx=0.4), 0.1).vx == pytest.approx(2.0 * 0.2)  # half way to 0.4


def test_non_finite_error_and_clock_going_back_force_zero():
    c = ServoController(_cfg(max_linear_accel=100.0))
    c.step(_err(dx=1.0), 1.0)
    assert c.step(_err(dx=math.inf), 1.1).vx == 0.0
    c.step(_err(dx=1.0), 1.2)
    assert c.step(_err(dx=1.0), 0.5).vx == 0.0  # the clock went back
    with pytest.raises(ValueError):
        c.step(_err(), float("nan"))


def test_angular_command_is_limited_in_its_own_units():
    c = ServoController(_cfg(max_angular_accel=2.0))
    assert c.step(_err(droll=1.0), 0.0).wroll == pytest.approx(0.2)  # 2 rad/s^2 x 0.1 s


def test_reset_forgets_the_session():
    c = ServoController(_cfg(max_linear_accel=100.0))
    c.step(_err(dx=1.0), 0.0)
    c.reset()
    assert c.last_command.vx == 0.0 and c.step(None, 0.1).vx == 0.0
