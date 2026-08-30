import math

import pytest

from hydra_umc_visual_servoing_api.pose import Pose6D
from hydra_umc_visual_servoing_api.servo import (
    PoseError,
    compute_pose_error,
    compute_velocity_command,
    is_converged,
)


def test_pose_error_linear():
    current = Pose6D(0, 0, 0, 0, 0, 0)
    target = Pose6D(1, -2, 0.5, 0, 0, 0)
    error = compute_pose_error(current, target)
    assert error.dx == pytest.approx(1.0)
    assert error.dy == pytest.approx(-2.0)
    assert error.dz == pytest.approx(0.5)
    assert error.linear_norm == pytest.approx(math.sqrt(1 + 4 + 0.25))


def test_pose_error_angular_takes_shortest_turn():
    # Crossing the +-pi seam: from 3.0 rad to -3.0 rad should be a small
    # positive step (through pi), not a ~6 rad step the long way around.
    current = Pose6D(0, 0, 0, 3.0, 0, 0)
    target = Pose6D(0, 0, 0, -3.0, 0, 0)
    error = compute_pose_error(current, target)
    assert abs(error.droll) < 1.0


def test_pose_error_zero_when_equal():
    p = Pose6D(1, 2, 3, 0.1, 0.2, 0.3)
    error = compute_pose_error(p, p)
    assert error.linear_norm == pytest.approx(0.0)
    assert error.angular_norm == pytest.approx(0.0)


def test_velocity_command_proportional():
    error = compute_pose_error(Pose6D(0, 0, 0, 0, 0, 0), Pose6D(2, 0, 0, 0, 0, 0))
    command = compute_velocity_command(error, gain=0.5)
    assert command.vx == pytest.approx(1.0)
    assert command.vy == pytest.approx(0.0)


def test_velocity_command_rejects_nonpositive_gain():
    error = compute_pose_error(Pose6D(0, 0, 0, 0, 0, 0), Pose6D(1, 0, 0, 0, 0, 0))
    with pytest.raises(ValueError):
        compute_velocity_command(error, gain=0.0)


def test_velocity_command_clamp_preserves_direction():
    error = compute_pose_error(Pose6D(0, 0, 0, 0, 0, 0), Pose6D(3, 4, 0, 0, 0, 0))
    command = compute_velocity_command(error, gain=1.0, max_linear_speed=5.0)
    # Unclamped norm is 5.0 already (3-4-5 triangle) - clamp should be a no-op.
    assert command.vx == pytest.approx(3.0)
    assert command.vy == pytest.approx(4.0)

    command_clamped = compute_velocity_command(error, gain=1.0, max_linear_speed=1.0)
    norm = math.sqrt(command_clamped.vx**2 + command_clamped.vy**2)
    assert norm == pytest.approx(1.0)
    # Direction preserved: vy/vx ratio should still be 4/3.
    assert command_clamped.vy / command_clamped.vx == pytest.approx(4 / 3)


def test_is_converged():
    tiny_error = compute_pose_error(Pose6D(0, 0, 0, 0, 0, 0), Pose6D(0.0001, 0, 0, 0, 0, 0))
    assert is_converged(tiny_error, linear_tol=0.001, angular_tol=0.01)

    big_error = compute_pose_error(Pose6D(0, 0, 0, 0, 0, 0), Pose6D(1.0, 0, 0, 0, 0, 0))
    assert not is_converged(big_error, linear_tol=0.001, angular_tol=0.01)


@pytest.mark.parametrize("gain", [0.0, -1.0, float("nan"), float("inf")])
def test_velocity_rejects_invalid_gain(gain):
    with pytest.raises(ValueError):
        compute_velocity_command(PoseError(1, 0, 0, 0, 0, 0), gain)


@pytest.mark.parametrize("maximum", [0.0, -0.1, float("nan")])
def test_velocity_rejects_invalid_speed_limit(maximum):
    with pytest.raises(ValueError):
        compute_velocity_command(
            PoseError(1, 0, 0, 0, 0, 0), gain=1.0, max_linear_speed=maximum
        )
