"""--gated-only closes the routes without a safety state; --require-safety-zones refuses to start without SAFETY-ZONES."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

import pytest
from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.main import main

CORRECT = {"current": [0, 0, 0, 0, 0, 0], "target": [0.1, 0, 0, 0, 0, 0]}
SESSION = {"gain": 1.0, "max_linear_speed": 0.1, "max_angular_speed": 0.2}


def _post(url: str, body: object) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.parametrize(("gated", "expected"), [(False, 200), (True, 403)])
def test_the_routes_without_a_safety_state_close_only_with_the_option(gated: bool, expected: int) -> None:
    server = VisualServoingServer(("127.0.0.1", 0), "service", accept_legacy_safety_string=True, gated_only=gated)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        status, answer = _post(base + "/correct", CORRECT)
        assert status == expected
        if gated:
            assert answer["code"] == "gate-required"
        status, opened = _post(base + "/sessions", SESSION)
        assert status == 201
        step_status, step = _post(base + f"/sessions/{opened['session_id']}/correct", CORRECT)
        assert step_status == expected and (step.get("code") == "gate-required") == gated
        # the step that reports a lost picture does not compute a command from poses of the caller
        assert _post(base + f"/sessions/{opened['session_id']}/vision-lost", {})[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_the_request_route_stays_open_with_gated_only() -> None:
    server = VisualServoingServer(("127.0.0.1", 0), "service", accept_legacy_safety_string=True, gated_only=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        body = {"current": [0, 0, 0, 0, 0, 0], "target": [0.1, 0, 0, 0, 0, 0], "frame_id": "f-1", "confidence": 0.9, "data_age_ms": 10, "safety_state": "READY"}
        assert _post(base + "/request", body)[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_the_service_does_not_start_without_safety_zones_when_it_is_required(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYDRA_UMC_SAFETY_ZONES_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["hydra-umc-visual-servoing-api", "serve", "--port", "0", "--require-safety-zones"])
    assert main() == 2
    assert "--safety-zones-url" in capsys.readouterr().err
