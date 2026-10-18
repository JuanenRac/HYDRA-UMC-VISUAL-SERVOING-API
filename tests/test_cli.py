import subprocess
import sys


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "hydra_umc_visual_servoing_api.main", *args],
        capture_output=True, text=True,
    )


def test_bare_invocation_prints_identity():
    result = run_cli()
    assert result.returncode == 0
    assert "HYDRA-UMC-VISUAL-SERVOING-API" in result.stdout


def test_correct_prints_velocity_command():
    result = run_cli("correct", "--current", "0,0,0,0,0,0", "--target", "1,0,0,0,0,0")
    assert result.returncode == 0
    assert "velocity cmd" in result.stdout
    assert "converged    : False" in result.stdout


def test_correct_reports_convergence_when_poses_match():
    result = run_cli("correct", "--current", "1,1,1,0,0,0", "--target", "1,1,1,0,0,0")
    assert result.returncode == 0
    assert "converged    : True" in result.stdout


def test_correct_rejects_malformed_pose():
    result = run_cli("correct", "--current", "not,a,pose", "--target", "0,0,0,0,0,0")
    assert result.returncode == 1
    assert "error" in result.stderr


def _request_args(**overrides):
    args = {
        "--current": "0,0,0,0,0,0",
        "--target": "1,0,0,0,0,0",
        "--frame-id": "cam0-f100",
        "--confidence": "0.9",
        "--data-age-ms": "50",
        "--safety-state": "READY",
    }
    args.update(overrides)
    flat = []
    for k, v in args.items():
        flat.extend([k, v])
    return flat


def test_request_accepted_prints_velocity_command():
    result = run_cli("request", *_request_args())
    assert result.returncode == 0
    assert "outcome : ACCEPTED" in result.stdout
    assert "velocity cmd" in result.stdout


def test_request_inhibited_when_safety_state_not_ready():
    result = run_cli("request", *_request_args(**{"--safety-state": "FAULT"}))
    assert result.returncode == 2
    assert "outcome : INHIBITED" in result.stdout
    assert "velocity cmd" not in result.stdout


def test_request_rejected_when_confidence_too_low():
    result = run_cli("request", *_request_args(**{"--confidence": "0.1"}))
    assert result.returncode == 1
    assert "outcome : REJECTED" in result.stdout
    assert "velocity cmd" not in result.stdout


def test_request_rejected_when_data_too_stale():
    result = run_cli("request", *_request_args(**{"--data-age-ms": "5000"}))
    assert result.returncode == 1
    assert "outcome : REJECTED" in result.stdout


def test_request_rejects_malformed_pose():
    result = run_cli("request", *_request_args(**{"--current": "not,a,pose"}))
    assert result.returncode == 1
    assert "error" in result.stderr


def test_correct_with_a_zero_gain_is_an_error_message_not_a_traceback():
    result = run_cli("correct", "--current", "0,0,0,0,0,0", "--target", "1,0,0,0,0,0", "--gain", "0")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "gain" in result.stderr


def test_request_with_a_zero_max_speed_is_an_error_message_not_a_traceback():
    result = run_cli(
        "request", "--current", "0,0,0,0,0,0", "--target", "1,0,0,0,0,0", "--frame-id", "f", "--confidence", "0.9",
        "--data-age-ms", "10", "--safety-state", "READY", "--max-linear-speed", "0",
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_the_commands_are_bounded_by_default():
    result = run_cli("correct", "--current", "0,0,0,0,0,0", "--target", "10,0,0,0,0,0")
    assert result.returncode == 0
    assert "vx=0.250000" in result.stdout
