import pytest

from hydra_umc_visual_servoing_api.authorization import (
    AuthorizationPolicy,
    CorrectionDecision,
    RequestOutcome,
    VisualTargetRequest,
    authorize_correction,
)
from hydra_umc_visual_servoing_api.pose import Pose6D

CURRENT = Pose6D(0, 0, 0, 0, 0, 0)
TARGET = Pose6D(1, 0, 0, 0, 0, 0)


def _request(**overrides):
    defaults = dict(
        current=CURRENT,
        target=TARGET,
        frame_id="cam0-f100",
        confidence=0.9,
        data_age_ms=50.0,
        safety_state="READY",
    )
    defaults.update(overrides)
    return VisualTargetRequest(**defaults)


def test_accepted_when_ready_confident_and_fresh():
    decision = authorize_correction(_request(), AuthorizationPolicy(), gain=1.0)
    assert decision.outcome is RequestOutcome.ACCEPTED
    assert decision.command is not None
    assert decision.error is not None
    assert decision.command.vx == pytest.approx(1.0)


def test_inhibited_when_safety_state_not_ready():
    decision = authorize_correction(_request(safety_state="INHIBITED"), AuthorizationPolicy(), gain=1.0)
    assert decision.outcome is RequestOutcome.INHIBITED
    assert "INHIBITED" in decision.reason
    assert decision.command is None


def test_inhibited_wins_over_bad_data():
    """Safety state is checked before confidence/freshness - an unready
    cell blocks the request even when the frame itself is also bad."""
    decision = authorize_correction(
        _request(safety_state="FAULT", confidence=0.0, data_age_ms=9999.0),
        AuthorizationPolicy(),
        gain=1.0,
    )
    assert decision.outcome is RequestOutcome.INHIBITED


def test_rejected_when_confidence_too_low():
    decision = authorize_correction(
        _request(confidence=0.3), AuthorizationPolicy(min_confidence=0.6), gain=1.0
    )
    assert decision.outcome is RequestOutcome.REJECTED
    assert "confidence" in decision.reason
    assert decision.command is None


def test_rejected_when_data_too_stale():
    decision = authorize_correction(
        _request(data_age_ms=500.0), AuthorizationPolicy(max_data_age_ms=200.0), gain=1.0
    )
    assert decision.outcome is RequestOutcome.REJECTED
    assert "old" in decision.reason


def test_confidence_boundary_exactly_at_minimum_is_accepted():
    """Prueba de limites: the minimum confidence is inclusive."""
    decision = authorize_correction(
        _request(confidence=0.6), AuthorizationPolicy(min_confidence=0.6), gain=1.0
    )
    assert decision.outcome is RequestOutcome.ACCEPTED


def test_confidence_boundary_just_below_minimum_is_rejected():
    decision = authorize_correction(
        _request(confidence=0.599999), AuthorizationPolicy(min_confidence=0.6), gain=1.0
    )
    assert decision.outcome is RequestOutcome.REJECTED


def test_data_age_boundary_exactly_at_maximum_is_accepted():
    """Prueba de limites: the maximum data age is inclusive."""
    decision = authorize_correction(
        _request(data_age_ms=200.0), AuthorizationPolicy(max_data_age_ms=200.0), gain=1.0
    )
    assert decision.outcome is RequestOutcome.ACCEPTED


def test_data_age_boundary_just_above_maximum_is_rejected():
    decision = authorize_correction(
        _request(data_age_ms=200.0001), AuthorizationPolicy(max_data_age_ms=200.0), gain=1.0
    )
    assert decision.outcome is RequestOutcome.REJECTED


def test_accepted_command_respects_speed_limits():
    decision = authorize_correction(
        _request(), AuthorizationPolicy(), gain=100.0, max_linear_speed=0.5
    )
    assert decision.outcome is RequestOutcome.ACCEPTED
    norm = (decision.command.vx**2 + decision.command.vy**2 + decision.command.vz**2) ** 0.5
    assert norm == pytest.approx(0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"frame_id": ""},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"data_age_ms": -1.0},
        {"safety_state": ""},
    ],
)
def test_visual_target_request_rejects_invalid_fields(kwargs):
    with pytest.raises(ValueError):
        _request(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"min_confidence": -0.1}, {"min_confidence": 1.1}, {"max_data_age_ms": 0}, {"max_data_age_ms": -5}],
)
def test_authorization_policy_rejects_invalid_fields(kwargs):
    with pytest.raises(ValueError):
        AuthorizationPolicy(**kwargs)


def test_correction_decision_defaults_have_no_command_or_error():
    decision = CorrectionDecision(RequestOutcome.REJECTED, "test")
    assert decision.command is None
    assert decision.error is None
