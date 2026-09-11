# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_safety_zones_integration.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real cross-repo integration test (F05): every other
test in this repo passes a hand-typed safety_state string. This one starts
the REAL HYDRA-UMC-SAFETY-ZONES HTTP server (a real, separate sibling repo/
package, its own venv - not vendored or reimplemented here), feeds it a real
zones/detections scenario, takes the exact `sdkSafetyState.state` string it
emits, and feeds THAT literal value into this repo's own
authorize_correction() - proving the full real chain end to end instead of
each side trusting the other's own unit tests in isolation.

Skips (does not fail) if the sibling HYDRA-UMC-SAFETY-ZONES checkout or its
own .venv is not present - this is a real integration test across two
independently-versioned repos, not something CI can assume a bare checkout
of this one repo alone provides.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hydra_umc_visual_servoing_api.authorization import (
    AuthorizationPolicy,
    RequestOutcome,
    VisualTargetRequest,
    authorize_correction,
)
from hydra_umc_visual_servoing_api.pose import Pose6D

THIS_REPO = Path(__file__).resolve().parents[1]
SAFETY_ZONES_ROOT = Path(
    __import__("os").environ.get("HYDRA_UMC_SAFETY_ZONES_ROOT", str(THIS_REPO.parent / "HYDRA-UMC-SAFETY-ZONES"))
)


def _safety_zones_python() -> Path | None:
    for candidate in (SAFETY_ZONES_ROOT / ".venv" / "Scripts" / "python.exe", SAFETY_ZONES_ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _zones() -> dict:
    return {
        "zones": [
            {"id": "warn1", "level": "warning", "min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 10, "y": 10, "z": 10}},
            {"id": "danger1", "level": "danger", "min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 2, "y": 2, "z": 2}},
        ],
        "calibration": {"version": "cal-1", "source": "manual", "calibrated_at": _today_str(), "max_age_days": 30},
    }


def _detections(x: float, y: float, z: float) -> dict:
    return {"objects": [{"id": "op1", "position": {"x": x, "y": y, "z": z}}]}


@pytest.fixture(scope="module")
def safety_zones_server():
    python = _safety_zones_python()
    if python is None:
        pytest.skip(f"sibling HYDRA-UMC-SAFETY-ZONES checkout/.venv not found at {SAFETY_ZONES_ROOT} - real "
                     "cross-repo integration test, needs that repo present to run, same convention "
                     "HYDRA-UMC-SERVER's own verify_voice_relay_contract.mjs already uses")
    port = _free_port()
    proc = subprocess.Popen(
        [str(python), "-m", "hydra_umc_safety_zones.main", "serve", "--addr", "127.0.0.1", "--port", str(port)],
        cwd=str(SAFETY_ZONES_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"hydra-umc-safety-zones serve exited early ({proc.returncode}):\n{out}")
            try:
                urllib.request.urlopen(f"{base}/stats", timeout=1)
                break
            except (urllib.error.URLError, ConnectionError) as e:
                last_error = e
                time.sleep(0.2)
        else:
            raise RuntimeError(f"hydra-umc-safety-zones serve did not become ready: {last_error}")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _request_with(safety_state: str) -> RequestOutcome:
    request = VisualTargetRequest(
        current=Pose6D(0, 0, 0, 0, 0, 0), target=Pose6D(1, 0, 0, 0, 0, 0),
        frame_id="integration-f1", confidence=0.9, data_age_ms=50.0, safety_state=safety_state,
    )
    return authorize_correction(request, AuthorizationPolicy(), gain=1.0).outcome


def test_real_ready_state_from_safety_zones_authorizes_a_correction(safety_zones_server):
    body = _post(f"{safety_zones_server}/check", {"zones": _zones(), "detections": _detections(50, 50, 50)})
    sdk_state = body["sdkSafetyState"]["state"]
    assert sdk_state == "READY"
    assert _request_with(sdk_state) is RequestOutcome.ACCEPTED


def test_real_warning_breach_from_safety_zones_blocks_a_correction(safety_zones_server):
    body = _post(f"{safety_zones_server}/check", {"zones": _zones(), "detections": _detections(5, 5, 5)})
    sdk_state = body["sdkSafetyState"]["state"]
    assert sdk_state == "INHIBITED"
    assert _request_with(sdk_state) is RequestOutcome.INHIBITED


def test_real_danger_breach_from_safety_zones_blocks_a_correction(safety_zones_server):
    body = _post(f"{safety_zones_server}/check", {"zones": _zones(), "detections": _detections(1, 1, 1)})
    sdk_state = body["sdkSafetyState"]["state"]
    assert sdk_state == "SAFE_STOP"
    assert _request_with(sdk_state) is RequestOutcome.INHIBITED


def test_real_expired_calibration_from_safety_zones_blocks_a_correction(safety_zones_server):
    zones = _zones()
    zones["calibration"] = {"version": "cal-0", "source": "manual", "calibrated_at": "2020-01-01", "max_age_days": 30}
    body = _post(f"{safety_zones_server}/check", {"zones": zones, "detections": _detections(50, 50, 50)})
    sdk_state = body["sdkSafetyState"]["state"]
    assert sdk_state == "INHIBITED"
    assert _request_with(sdk_state) is RequestOutcome.INHIBITED
