# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/hailo_runtime.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real HailoRT (hailo_platform) integration boundary for the Hailo-8 pose
estimator servo.py's own header says this API would source a current pose
from. Until now nothing in this repo knew how to actually load that real
model or turn its raw output into the `Pose6D` compute_pose_error() /
compute_velocity_command() already consume. This module is that missing
piece, prepared ahead of the real Hailo-8 module landing: once it plugs
in, `load_hailo_pose_model()` is the one function that needs to actually
run against real silicon - everything else here is real, tested logic
today.

The real pip package is `hailort` (not on PyPI - Hailo Developer Zone, or
`apt install hailo-all` on Raspberry Pi OS with a Hailo module attached);
its Python import name is `hailo_platform`. Real, confirmed API surface
used here: `VDevice()`, `HEF(path)`, `ConfigureParams.create_from_hef(hef,
interface=HailoStreamInterface.PCIe)`, `vdevice.configure(hef,
configure_params)` -> a list of `ConfiguredNetworkGroup`, and each vstream
info exposing real `.name`/`.shape` attributes via
`hef.get_input_vstream_infos()` / `get_output_vstream_infos()`.

This project's own contract for a pose model's output (`hailo_output_to_pose`
below): exactly 6 float values, in `Pose6D`'s own (x, y, z, roll, pitch,
yaw) order - a real, explicit design decision for whichever model this
project eventually integrates, not a claim about every Hailo pose model's
output layout in general. A model that outputs a quaternion or rotation
matrix instead would need its own conversion step added here, not a
change to `Pose6D` itself.

Same lazy-import + injectable-boundary pattern as every other real
hardware transport this ecosystem has added (serial_transport.py,
mavlink_transport.py, VLA-ENGINE's and VISION-STREAMER's own
hailo_runtime.py, ...): `hailo_platform` is imported only inside the two
functions that genuinely need real HailoRT (`open_vdevice`,
`load_hailo_pose_model`), each raising a clear `HailoNotAvailableError`
instead of a bare `ImportError` when the package isn't installed - true
on this development machine today. `hailo_output_to_pose()` is written
against plain data (an array-like with `.tolist()`, matching both a real
numpy-backed HailoRT result and the plain-list fakes this module's own
tests use), so it is fully unit-testable without hailort or a Hailo-8 NPU.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pose import Pose6D, PoseParseError

HAILORT_INSTALL_HINT = (
    "hailort is not installed - get it from the Hailo Developer Zone, or "
    "`apt install hailo-all` on Raspberry Pi OS with a Hailo-8 module attached "
    "(it is not on PyPI). This module's pose-adaptation logic works and is "
    "tested without it."
)


class HailoNotAvailableError(RuntimeError):
    """Raised when hailo_platform (the hailort package) is not importable,
    or when a real .hef does not match this API's single-tensor-in/
    single-tensor-out pose estimation contract."""


@dataclass(frozen=True)
class HailoPoseModel:
    """A real HailoRT network group, configured from a real .hef file -
    only ever constructed by load_hailo_pose_model() below, never by
    hand, since `network_group` is a real HailoRT object."""

    hef_path: Path
    input_name: str
    input_shape: tuple[int, ...]
    output_name: str
    output_shape: tuple[int, ...]
    network_group: object


def open_vdevice() -> object:
    """Open a real Hailo VDevice targeting whichever Hailo-8 module is
    actually attached - the only place this module imports hailo_platform
    to obtain one. Lazy, so a host without the real hailort package
    installed still gets a clear RuntimeError instead of an ImportError
    surfacing from deep inside this module.
    """
    try:
        from hailo_platform import VDevice  # type: ignore[import-not-found]
    except ImportError as error:
        raise HailoNotAvailableError(HAILORT_INSTALL_HINT) from error
    return VDevice()


def load_hailo_pose_model(vdevice: object, hef_path: Path) -> HailoPoseModel:
    """Configure a real pose-estimation .hef onto an already-open VDevice
    and extract its real input/output vstream shapes.

    Real HailoRT flow: HEF(path) -> ConfigureParams.create_from_hef(hef,
    interface=HailoStreamInterface.PCIe) -> vdevice.configure(hef,
    params). Needs a real hailort install and a real compiled .hef - this
    function is the one real boundary where that dependency is
    unavoidable, same as bootloader_client.py needing a real spidev
    transport to flash real silicon (this session's own STM32H745
    SPI-OTA work).
    """
    try:
        from hailo_platform import (  # type: ignore[import-not-found]
            HEF,
            ConfigureParams,
            HailoStreamInterface,
        )
    except ImportError as error:
        raise HailoNotAvailableError(HAILORT_INSTALL_HINT) from error

    hef = HEF(str(hef_path))
    configure_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
    network_groups = vdevice.configure(hef, configure_params)
    network_group = network_groups[0]

    input_infos = hef.get_input_vstream_infos()
    output_infos = hef.get_output_vstream_infos()
    if len(input_infos) != 1 or len(output_infos) != 1:
        raise HailoNotAvailableError(
            f"{hef_path}: expected exactly 1 input and 1 output vstream for this "
            f"API's single-tensor-in/single-tensor-out pose contract, got "
            f"{len(input_infos)} input(s) and {len(output_infos)} output(s)"
        )

    return HailoPoseModel(
        hef_path=hef_path,
        input_name=input_infos[0].name,
        input_shape=tuple(input_infos[0].shape),
        output_name=output_infos[0].name,
        output_shape=tuple(output_infos[0].shape),
        network_group=network_group,
    )


def hailo_output_to_pose(raw_output: dict[str, Any], model: HailoPoseModel) -> Pose6D:
    """Adapt a real HailoRT inference result (output vstream name -> an
    array-like with a real .tolist()) into this project's own `Pose6D` -
    exactly the shape `servo.py`'s `compute_pose_error()` already
    consumes, so a real Hailo-8 estimator can feed the existing PBVS
    correction law unchanged once it exists.
    """
    if model.output_name not in raw_output:
        raise PoseParseError(
            f"hailo output missing expected vstream {model.output_name!r} (got {sorted(raw_output)})"
        )
    array = raw_output[model.output_name]
    # A real ndarray from InferVStreams.infer() is batched: shape (1, 6)
    # for this API's one-inference-per-call usage. .tolist() is a real
    # numpy method too, so this line works unchanged against either a
    # real ndarray or the plain-list fakes this module's tests use.
    values = array.tolist() if hasattr(array, "tolist") else list(array)
    if len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = values[0]

    if len(values) != 6:
        raise PoseParseError(
            f"hailo output {model.output_name!r}: expected 6 values (x, y, z, roll, "
            f"pitch, yaw), got {len(values)}"
        )
    return Pose6D(*(float(value) for value in values))
