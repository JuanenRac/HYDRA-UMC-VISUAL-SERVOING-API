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
