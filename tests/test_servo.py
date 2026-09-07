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


# SERVO-02 (found in an ecosystem-wide software-improvements audit, P1):
# individually-finite pose components can still combine into a
# non-finite result - a real gap this whole class of test exercises.

def test_pose_error_rejects_a_subtraction_that_overflows_to_infinite():
    # Both poses are finite and individually valid (Pose6D.parse's own
    # isfinite check would accept either alone) - it's the SUBTRACTION
    # that overflows a float, not either input on its own.
    current = Pose6D(-1e308, 0, 0, 0, 0, 0)
    target = Pose6D(1e308, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        compute_pose_error(current, target)


def test_linear_norm_of_an_extreme_but_finite_error_does_not_raise_overflowerror():
    # 1e200 ** 2 alone raises OverflowError in plain Python - this used
    # to crash here (via the old sqrt(sum(c**2)) implementation) instead
    # of returning the real, finite norm math.hypot computes correctly.
    error = PoseError(1e200, 0, 0, 0, 0, 0)
    assert error.linear_norm == pytest.approx(1e200)


def test_velocity_command_rejects_a_result_that_overflows_to_infinite_with_no_clamp():
    # No max_linear_speed given at all - _clamp_vector's old
    # short-circuit meant NOTHING checked this output's finiteness
    # before it reached compute_velocity_command's own return.
    error = PoseError(1e308, 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        compute_velocity_command(error, gain=1e300)


def test_velocity_command_clamp_itself_never_produces_nan_for_an_infinite_input():
    # Even WITH a clamp bound, an inf component divided/scaled can
    # produce NaN (inf * 0.0) rather than raising cleanly - this must
    # still surface as a real ValueError, never a silent NaN in the
    # returned command.
    error = PoseError(float("inf"), 0, 0, 0, 0, 0)
    with pytest.raises(ValueError):
        compute_velocity_command(error, gain=1.0, max_linear_speed=1.0)
