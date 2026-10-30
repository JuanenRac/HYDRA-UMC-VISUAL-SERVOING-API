# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_orientation_quaternion.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import math

import pytest

from hydra_umc_visual_servoing_api.orientation import (
    OrientationConfigError,
    OrientationPolicy,
    load_orientation_policy,
)
from hydra_umc_visual_servoing_api.pose import Pose6D, PoseParseError
from hydra_umc_visual_servoing_api.rotation import Quaternion
from hydra_umc_visual_servoing_api.servo import compute_pose_error

BASE = OrientationPolicy("quaternion", "base")
TOOL = OrientationPolicy("quaternion", "tool")
EULER = OrientationPolicy("euler", "base")


def _pose(roll=0.0, pitch=0.0, yaw=0.0):
    return Pose6D(0.0, 0.0, 0.0, roll, pitch, yaw)


def test_the_error_is_the_real_rotation_between_two_orientations() -> None:
    # Applying the error rotation to the current orientation must give the target, whatever the angles are.
    current, target = _pose(0.4, 1.0, -2.0), _pose(-1.1, 0.3, 2.9)
    error = compute_pose_error(current, target, BASE)
    delta = Quaternion.from_euler(0, 0, 0)
    vector = error.rotation_vector
    angle = math.sqrt(sum(c * c for c in vector))
    axis = [c / angle for c in vector]
    s = math.sin(angle / 2)
    delta = Quaternion(math.cos(angle / 2), axis[0] * s, axis[1] * s, axis[2] * s)
    assert (delta * current.quaternion).angle_to(target.quaternion) < 1e-9
    # the difference of the Euler angles is not that rotation
    legacy = compute_pose_error(current, target, EULER).rotation_vector
    assert max(abs(a - b) for a, b in zip(vector, legacy)) > 0.1


def test_the_error_is_the_shortest_turn_and_zero_for_the_same_orientation() -> None:
    assert compute_pose_error(_pose(0.3, 0.2, 0.1), _pose(0.3, 0.2, 0.1), BASE).angular_norm == pytest.approx(0.0, abs=1e-12)
    error = compute_pose_error(_pose(yaw=math.radians(170)), _pose(yaw=math.radians(-170)), BASE)
    assert error.dyaw == pytest.approx(math.radians(20))  # across the wrap, not 340 degrees back


def test_near_pitch_ninety_the_angular_error_stays_small_for_a_small_turn() -> None:
    current = _pose(0.0, math.radians(89.9), 0.0)
    target = _pose(math.radians(1.0), math.radians(89.9), 0.0)
    assert compute_pose_error(current, target, BASE).angular_norm < math.radians(2.0)


def test_the_frame_decides_the_axes_of_the_error() -> None:
    current, target = _pose(yaw=math.pi / 2), _pose(yaw=math.pi / 2, roll=0.2)
    base = compute_pose_error(current, target, BASE).rotation_vector
    tool = compute_pose_error(current, target, TOOL).rotation_vector
    assert math.sqrt(sum(c * c for c in base)) == pytest.approx(math.sqrt(sum(c * c for c in tool)))
    assert base == pytest.approx((0.0, 0.2, 0.0), abs=1e-9) or base[0] == pytest.approx(0.0, abs=1e-9)
    assert tool == pytest.approx((0.2, 0.0, 0.0), abs=1e-9)


def test_a_pose_can_be_given_as_a_quaternion() -> None:
    q = Quaternion.from_euler(0.3, -0.2, 1.0)
    from_list = Pose6D.coerce([0.1, 0.2, 0.3, q.w, q.x, q.y, q.z])
    from_text = Pose6D.parse(f"0.1,0.2,0.3,{q.w!r},{q.x!r},{q.y!r},{q.z!r}")
    from_object = Pose6D.coerce({
        "schema_version": "1.0", "frame": "base",
        "position": {"x": 0.1, "y": 0.2, "z": 0.3},
        "orientation": {"w": q.w, "x": q.x, "y": q.y, "z": q.z},
    })
    for pose in (from_list, from_text, from_object):
        assert pose.quaternion.angle_to(q) < 1e-9
        assert (pose.roll, pose.pitch, pose.yaw) == pytest.approx((0.3, -0.2, 1.0))
    with pytest.raises(PoseParseError):
        Pose6D.coerce([0, 0, 0, 1, 1, 0, 0])  # not a unit quaternion
    with pytest.raises(PoseParseError):
        Pose6D.coerce({"position": {"x": 0, "y": 0, "z": 0}, "orientation": {"w": 1, "x": 0, "y": 0}})


def test_the_configuration_file_chooses_the_error_and_the_frame(tmp_path, monkeypatch) -> None:
    assert load_orientation_policy() == OrientationPolicy("quaternion", "base")
    config = tmp_path / "servo.json"
    config.write_text(json.dumps({"orientation": {"error": "Euler", "frame": "tool"}}), encoding="utf-8")
    assert load_orientation_policy(config) == OrientationPolicy("euler", "tool")
    monkeypatch.setenv("HYDRA_UMC_VISUAL_SERVOING_CONFIG", str(config))
    assert load_orientation_policy().error == "euler"
    config.write_text(json.dumps({"orientation": {"error": "axis"}}), encoding="utf-8")
    with pytest.raises(OrientationConfigError):
        load_orientation_policy(config)
    with pytest.raises(OrientationConfigError):
        load_orientation_policy(tmp_path / "missing.json")
