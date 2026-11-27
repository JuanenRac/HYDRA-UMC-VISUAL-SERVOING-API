"""The `gate` field of a command."""

import pytest

from hydra_umc_visual_servoing_api.contract import ContractError, validate_velocity_command


def command(**extra):
    return {"schema_version": "1.0", "vx": 0.0, "vy": 0.0, "vz": 0.0, "wroll": 0.0, "wpitch": 0.0, "wyaw": 0.0,
            "hold": True, "issued_at_ms": 1, "valid_for_ms": 100, "seq": 1, **extra}


@pytest.mark.parametrize("gate", ["passed", "none"])
def test_the_two_values_of_the_contract_are_accepted(gate):
    validate_velocity_command(command(gate=gate))


def test_any_other_value_is_refused():
    with pytest.raises(ContractError):
        validate_velocity_command(command(gate="yes"))
