# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_ibvs.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""IBVS: the interaction matrix against the geometry, a closed loop against a simulated camera, the limits and the route."""
from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.ibvs import (
    IbvsError,
    IbvsPolicy,
    compute_ibvs_command,
    interaction_matrix,
    load_ibvs_policy,
)

SQUARE = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]


def _world(z: float, dx: float = 0.0, dy: float = 0.0) -> list[list[float]]:
    return [[x + dx, y + dy, z] for x, y in SQUARE]


def _project(world: list[list[float]]) -> list[tuple[float, float]]:
    return [(p[0] / p[2], p[1] / p[2]) for p in world]


def _move_camera(world: list[list[float]], v: tuple[float, ...], w: tuple[float, ...], dt: float) -> list[list[float]]:
    """Camera-frame points after the camera moves with twist (v, w) for dt: dP/dt = -v - w x P."""
    out = []
    for x, y, z in world:
        cross = (w[1] * z - w[2] * y, w[2] * x - w[0] * z, w[0] * y - w[1] * x)
        out.append([x + (-v[0] - cross[0]) * dt, y + (-v[1] - cross[1]) * dt, z + (-v[2] - cross[2]) * dt])
    return out


def test_interaction_matrix_matches_the_projection_geometry() -> None:
    world = [[0.3, -0.2, 1.5], [-0.1, 0.25, 0.9], [0.0, 0.0, 2.0]]
    points = _project(world)
    depths = [p[2] for p in world]
    rows = interaction_matrix(points, depths)
    dt = 1e-6
    for axis in range(6):
        twist = [0.0] * 6
        twist[axis] = 1.0
        moved = _project(_move_camera(world, tuple(twist[:3]), tuple(twist[3:]), dt))
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(points, moved, strict=False)):
            assert (x1 - x0) / dt == pytest.approx(rows[2 * i][axis], abs=1e-4)
            assert (y1 - y0) / dt == pytest.approx(rows[2 * i + 1][axis], abs=1e-4)


def test_no_error_gives_no_motion() -> None:
    command, rms = compute_ibvs_command([list(p) for p in SQUARE], [list(p) for p in SQUARE], 1.0, gain=1.0, damping=1e-4)
    assert rms == 0.0
    assert all(abs(v) < 1e-12 for v in (command.vx, command.vy, command.vz, command.wroll, command.wpitch, command.wyaw))


def test_closed_loop_converges_in_a_simulator() -> None:
    desired = _project(_world(1.0))
    world = _world(1.4, dx=0.12, dy=-0.08)  # starts farther away and off to the side
    dt, gain = 0.05, 1.0
    first = None
    for _ in range(600):
        current = _project(world)
        depth = [p[2] for p in world]
        command, rms = compute_ibvs_command([list(p) for p in current], [list(p) for p in desired], depth, gain=gain, damping=1e-6)
        first = rms if first is None else first
        world = _move_camera(world, (command.vx, command.vy, command.vz), (command.wroll, command.wpitch, command.wyaw), dt)
    assert first is not None and rms < 1e-3 < first
    assert all(math.isfinite(c) for p in world for c in p)


def test_closed_loop_converges_with_a_rotation_too() -> None:
    desired = _project(_world(1.0))
    c, s = math.cos(0.3), math.sin(0.3)
    world = [[c * x - s * y, s * x + c * y, z] for x, y, z in _world(1.2)]  # the square seen turned about the optical axis
    for _ in range(800):
        current = _project(world)
        command, rms = compute_ibvs_command([list(p) for p in current], [list(p) for p in desired], [p[2] for p in world], gain=1.0, damping=1e-6)
        world = _move_camera(world, (command.vx, command.vy, command.vz), (command.wroll, command.wpitch, command.wyaw), 0.05)
    assert rms < 2e-3


def test_command_is_clamped_keeping_its_direction() -> None:
    far = [[x + 0.5, y + 0.5] for x, y in SQUARE]
    free, _ = compute_ibvs_command(far, [list(p) for p in SQUARE], 1.0, gain=2.0, damping=1e-4)
    capped, _ = compute_ibvs_command(far, [list(p) for p in SQUARE], 1.0, gain=2.0, damping=1e-4, max_linear_speed=0.01, max_angular_speed=0.01)
    assert math.hypot(capped.vx, capped.vy, capped.vz) == pytest.approx(0.01)
    norm = math.hypot(free.vx, free.vy, free.vz)
    assert capped.vx / 0.01 == pytest.approx(free.vx / norm)


@pytest.mark.parametrize(
    "features,desired,depth",
    [
        (SQUARE[:2], SQUARE[:2], 1.0),  # fewer than 3 points
        (SQUARE, SQUARE[:3], 1.0),  # different counts
        ([[0.0, 0.0, 1.0]] * 4, SQUARE, 1.0),
        ([[float("nan"), 0.0]] * 4, SQUARE, 1.0),
        ([[9.0, 0.0]] * 4, SQUARE, 1.0),  # outside normalized coordinates
        ([[True, 0.0]] * 4, SQUARE, 1.0),
        (SQUARE, SQUARE, 0.0),
        (SQUARE, SQUARE, -1.0),
        (SQUARE, SQUARE, [1.0, 1.0]),
        (SQUARE, SQUARE, True),
        ("x", SQUARE, 1.0),
    ],
)
def test_refuses_what_cannot_be_used(features: object, desired: object, depth: object) -> None:
    with pytest.raises(IbvsError):
        compute_ibvs_command(features, desired, depth, gain=1.0, damping=1e-4)


def test_a_degenerate_set_of_points_is_refused_without_damping() -> None:
    collinear = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]  # the same point three times: rank 2
    with pytest.raises(IbvsError):
        compute_ibvs_command(collinear, [[0.1, 0.1]] * 3, 1.0, gain=1.0, damping=0.0)
    command, _ = compute_ibvs_command(collinear, [[0.1, 0.1]] * 3, 1.0, gain=1.0, damping=1e-3)
    assert all(math.isfinite(v) for v in (command.vx, command.vy, command.vz))


def test_gain_must_be_positive() -> None:
    with pytest.raises(ValueError):
        compute_ibvs_command(SQUARE, SQUARE, 1.0, gain=0.0, damping=1e-4)


def test_policy_defaults_and_loading(tmp_path: Path) -> None:
    assert IbvsPolicy() == IbvsPolicy(False, 1e-4)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"orientation": {}, "ibvs": {"enabled": True, "damping": 0.01}}), encoding="utf-8")
    assert load_ibvs_policy(cfg) == IbvsPolicy(True, 0.01)
    cfg.write_text("{}", encoding="utf-8")
    assert load_ibvs_policy(cfg).enabled is False
    for bad in ('{"ibvs": 1}', '{"ibvs": {"enabled": "yes"}}', '{"ibvs": {"damping": -1}}', "not json"):
        cfg.write_text(bad, encoding="utf-8")
        with pytest.raises(IbvsError):
            load_ibvs_policy(cfg)
    with pytest.raises(IbvsError):
        load_ibvs_policy(tmp_path / "missing.json")


def test_shipped_configuration_keeps_ibvs_off() -> None:
    assert load_ibvs_policy(Path(__file__).resolve().parents[1] / "configs" / "visual-servoing.json").enabled is False


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _run(**kwargs: object) -> tuple[VisualServoingServer, threading.Thread]:
    server = VisualServoingServer(("127.0.0.1", 0), "test", **kwargs)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server: VisualServoingServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_route_answers_404_while_off() -> None:
    server, thread = _run()
    try:
        status, _ = _post(f"http://127.0.0.1:{server.server_address[1]}/ibvs/correct", {"features": SQUARE, "desired": SQUARE, "depth": 1.0})
        assert status == 404
    finally:
        _stop(server, thread)


def test_route_returns_a_bounded_command_when_on() -> None:
    server, thread = _run(ibvs=IbvsPolicy(enabled=True))
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/ibvs/correct"
        shifted = [[x + 0.2, y] for x, y in SQUARE]
        status, body = _post(url, {"features": shifted, "desired": SQUARE, "depth": 1.0, "frame_id": "f1"})
        assert status == 200 and body["converged"] is False and body["feature_rms"] > 0
        command = body["command"]
        assert command["hold"] is False and command["frame_id"] == "f1" and command["valid_for_ms"] > 0
        assert math.hypot(command["vx"], command["vy"], command["vz"]) <= server.limits.max_linear_speed + 1e-12
        # already there: the command is zero and held
        status, body = _post(url, {"features": SQUARE, "desired": SQUARE, "depth": 1.0})
        assert status == 200 and body["converged"] is True and body["command"]["hold"] is True and body["command"]["vx"] == 0.0
        # looser than the server allows is refused
        status, body = _post(url, {"features": shifted, "desired": SQUARE, "depth": 1.0, "gain": server.limits.max_gain + 1})
        assert status == 400
        status, body = _post(url, {"features": SQUARE[:2], "desired": SQUARE[:2], "depth": 1.0})
        assert status == 400 and "error" in body
    finally:
        _stop(server, thread)
