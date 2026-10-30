# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_hailo_runtime.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

from pathlib import Path

import pytest

from hydra_umc_visual_servoing_api.hailo_runtime import (
    HailoNotAvailableError,
    HailoPoseModel,
    hailo_output_to_pose,
    load_hailo_pose_model,
    open_vdevice,
)
from hydra_umc_visual_servoing_api.pose import Pose6D, PoseParseError


def _fake_model() -> HailoPoseModel:
    # Constructed directly (never via load_hailo_pose_model, which needs
    # real hailort) - proves hailo_output_to_pose works against any
    # object with the real HailoPoseModel shape, hailort installed or not.
    return HailoPoseModel(
        hef_path=Path("pose-estimator.hef"),
        input_name="pose_input",
        input_shape=(224, 224, 3),
        output_name="pose_output",
        output_shape=(6,),
        network_group=object(),
    )


class _FakeArray:
    """Stands in for a real numpy ndarray: only .tolist() is used."""

    def __init__(self, data: list) -> None:
        self._data = data

    def tolist(self) -> list:
        return self._data


def test_open_vdevice_raises_clear_error_without_hailort() -> None:
    # hailort is not installed on this development machine - the real,
    # honest state this module must degrade to cleanly.
    with pytest.raises(HailoNotAvailableError, match="hailort is not installed"):
        open_vdevice()


def test_load_hailo_pose_model_raises_clear_error_without_hailort() -> None:
    with pytest.raises(HailoNotAvailableError, match="hailort is not installed"):
        load_hailo_pose_model(vdevice=object(), hef_path=Path("pose-estimator.hef"))


def test_hailo_output_to_pose_batched_ndarray_shape() -> None:
    model = _fake_model()
    # Real HailoRT InferVStreams.infer() batches output: shape (1, 6).
    raw_output = {"pose_output": _FakeArray([[0.10, -0.05, 0.30, 0.0, 0.1, -0.2]])}

    pose = hailo_output_to_pose(raw_output, model)

    assert pose == Pose6D(x=0.10, y=-0.05, z=0.30, roll=0.0, pitch=0.1, yaw=-0.2)


def test_hailo_output_to_pose_unbatched_shape() -> None:
    model = _fake_model()
    raw_output = {"pose_output": _FakeArray([1.0, 2.0, 3.0, 0.1, 0.2, 0.3])}

    pose = hailo_output_to_pose(raw_output, model)

    assert pose == Pose6D(x=1.0, y=2.0, z=3.0, roll=0.1, pitch=0.2, yaw=0.3)


def test_hailo_output_to_pose_plain_list_without_tolist() -> None:
    model = _fake_model()
    raw_output = {"pose_output": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}

    pose = hailo_output_to_pose(raw_output, model)

    assert pose == Pose6D(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_hailo_output_to_pose_missing_output_vstream() -> None:
    model = _fake_model()

    with pytest.raises(PoseParseError, match="missing expected vstream"):
        hailo_output_to_pose({"some_other_output": _FakeArray([1.0])}, model)


def test_hailo_output_to_pose_wrong_arity() -> None:
    model = _fake_model()
    raw_output = {"pose_output": _FakeArray([1.0, 2.0, 3.0])}

    with pytest.raises(PoseParseError, match="expected 6 values"):
        hailo_output_to_pose(raw_output, model)


# --- a model built from the bytes the registry verified ---------------------------------------------

import hashlib
import sys
import types

from hydra_umc_visual_servoing_api.hailo_runtime import ModelIntegrityError


class _FakeInfo:
    def __init__(self, name, shape):
        self.name, self.shape = name, shape


class _FakeHef:
    sources: list = []

    def __init__(self, source):
        _FakeHef.sources.append(source)

    def get_input_vstream_infos(self):
        return [_FakeInfo("in", (4, 4, 3))]

    def get_output_vstream_infos(self):
        return [_FakeInfo("out", (6,))]


class _FakeVDevice:
    def configure(self, hef, params):
        return ["group"]


@pytest.fixture
def fake_hailo(monkeypatch):
    module = types.SimpleNamespace(
        HEF=_FakeHef,
        ConfigureParams=types.SimpleNamespace(create_from_hef=lambda hef, interface=None: "params"),
        HailoStreamInterface=types.SimpleNamespace(PCIe="pcie"),
    )
    monkeypatch.setitem(sys.modules, "hailo_platform", module)
    _FakeHef.sources = []
    return module


def test_the_model_is_built_from_the_verified_bytes_and_the_path_is_not_opened(fake_hailo) -> None:
    data = b"hef-bytes"
    model = load_hailo_pose_model(_FakeVDevice(), Path("does-not-exist.hef"), hef_bytes=data, expected_sha256=hashlib.sha256(data).hexdigest())
    assert _FakeHef.sources == [data]
    assert model.output_shape == (6,)


def test_bytes_that_do_not_match_the_checksum_are_refused_before_anything_is_configured(fake_hailo) -> None:
    with pytest.raises(ModelIntegrityError, match="checksum"):
        load_hailo_pose_model(_FakeVDevice(), Path("m.hef"), hef_bytes=b"other", expected_sha256=hashlib.sha256(b"hef-bytes").hexdigest())
    assert _FakeHef.sources == []


def test_empty_bytes_are_refused(fake_hailo) -> None:
    with pytest.raises(ModelIntegrityError, match="empty"):
        load_hailo_pose_model(_FakeVDevice(), Path("m.hef"), hef_bytes=b"")


def test_without_bytes_the_path_is_opened_as_before(fake_hailo) -> None:
    load_hailo_pose_model(_FakeVDevice(), Path("m.hef"))
    assert _FakeHef.sources == ["m.hef"]