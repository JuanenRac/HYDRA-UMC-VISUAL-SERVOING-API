# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_hailo_runtime_fake_device.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The Hailo boundary against a stand-in `hailo_platform` that has the API surface the module documents (evidence: simulator).

The stand-in records how the model was configured, so the tests can say that the model is built from the verified bytes and not from a path, that the PCIe interface is
asked for and that a model with the wrong number of streams is refused. It says nothing about the byte layout of the output of a real compiled model, and nothing here
has run against a real Hailo-8.
"""
from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from hydra_umc_visual_servoing_api.hailo_runtime import (
    HailoNotAvailableError,
    ModelIntegrityError,
    load_hailo_pose_model,
    open_vdevice,
)


class _Info:
    def __init__(self, name: str, shape: tuple[int, ...]) -> None:
        self.name, self.shape = name, shape


def install_fake_hailo(monkeypatch: pytest.MonkeyPatch, inputs: list[_Info], outputs: list[_Info]) -> dict[str, Any]:
    seen: dict[str, Any] = {"hef_sources": [], "configure": [], "interfaces": []}
    module = types.ModuleType("hailo_platform")

    class HailoStreamInterface:
        PCIe = "PCIe"

    class HEF:
        def __init__(self, source: Any) -> None:
            seen["hef_sources"].append(source)

        def get_input_vstream_infos(self) -> list[_Info]:
            return inputs

        def get_output_vstream_infos(self) -> list[_Info]:
            return outputs

    class ConfigureParams:
        @staticmethod
        def create_from_hef(hef: Any, interface: Any) -> str:
            seen["interfaces"].append(interface)
            return "configure-params"

    class VDevice:
        def configure(self, hef: Any, params: Any) -> list[str]:
            seen["configure"].append(params)
            return ["network-group-0"]

    class _Params:
        @staticmethod
        def make(group: Any) -> str:
            return f"params-of-{group}"

    module.HailoStreamInterface = HailoStreamInterface
    module.HEF, module.ConfigureParams, module.VDevice = HEF, ConfigureParams, VDevice
    module.InputVStreamParams = module.OutputVStreamParams = _Params
    monkeypatch.setitem(sys.modules, "hailo_platform", module)
    return seen


ONE_IN = [_Info("pose_input", (256, 256, 3))]
ONE_OUT = [_Info("pose_output", (6,))]


def test_open_vdevice_returns_the_device_of_the_stand_in(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_hailo(monkeypatch, ONE_IN, ONE_OUT)
    assert type(open_vdevice()).__name__ == "VDevice"


def test_a_model_with_one_input_and_one_output_is_configured_on_pcie(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_fake_hailo(monkeypatch, ONE_IN, ONE_OUT)
    model = load_hailo_pose_model(open_vdevice(), Path("model.hef"))
    assert (model.input_name, model.input_shape) == ("pose_input", (256, 256, 3))
    assert (model.output_name, model.output_shape) == ("pose_output", (6,))
    assert model.network_group == "network-group-0"
    assert seen["interfaces"] == ["PCIe"] and seen["configure"] == ["configure-params"]
    assert seen["hef_sources"] == ["model.hef"]


@pytest.mark.parametrize(
    ("inputs", "outputs"),
    [([], ONE_OUT), (ONE_IN * 2, ONE_OUT), (ONE_IN, []), (ONE_IN, ONE_OUT * 2)],
)
def test_a_model_with_another_number_of_streams_is_refused(monkeypatch: pytest.MonkeyPatch, inputs: list[_Info], outputs: list[_Info]) -> None:
    install_fake_hailo(monkeypatch, inputs, outputs)
    with pytest.raises(HailoNotAvailableError, match="expected exactly 1 input"):
        load_hailo_pose_model(open_vdevice(), Path("model.hef"))


def test_the_model_is_built_from_the_verified_bytes_and_the_path_is_not_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_fake_hailo(monkeypatch, ONE_IN, ONE_OUT)
    data = b"compiled-model-bytes"
    model = load_hailo_pose_model(open_vdevice(), Path("does-not-exist.hef"), hef_bytes=data, expected_sha256=hashlib.sha256(data).hexdigest().upper())
    assert seen["hef_sources"] == [data] and model.input_name == "pose_input"


@pytest.mark.parametrize("data", [b"", b"other-bytes"])
def test_bytes_that_are_empty_or_do_not_match_the_registry_checksum_are_refused_before_the_device_is_touched(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    seen = install_fake_hailo(monkeypatch, ONE_IN, ONE_OUT)
    with pytest.raises(ModelIntegrityError):
        load_hailo_pose_model(open_vdevice(), Path("model.hef"), hef_bytes=data, expected_sha256=hashlib.sha256(b"compiled-model-bytes").hexdigest())
    assert seen["hef_sources"] == [] and seen["configure"] == []
