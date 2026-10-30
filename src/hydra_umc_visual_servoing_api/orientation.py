# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/orientation.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""How the orientation error is computed, read from the configuration file.

`configs/visual-servoing.json`:

    {"orientation": {"error": "quaternion", "frame": "base"}}

* `error`: "quaternion" (the rotation between the two orientations, the real
  error) or "euler" (the earlier component-by-component difference of the
  angles; obsolete, kept for installations that rely on it).
* `frame`: "base" expresses the error as an angular velocity about the axes of
  the robot base, "tool" about the axes of the end effector.

The file is the one given with `--config` or named by HYDRA_UMC_VISUAL_SERVOING_CONFIG;
without either the defaults apply (quaternion, base).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ERROR_MODES = ("quaternion", "euler")
FRAMES = ("base", "tool")
CONFIG_ENV = "HYDRA_UMC_VISUAL_SERVOING_CONFIG"


class OrientationConfigError(ValueError):
    """The configuration file cannot be used."""


@dataclass(frozen=True)
class OrientationPolicy:
    error: str = "quaternion"
    frame: str = "base"

    def __post_init__(self) -> None:
        if self.error not in ERROR_MODES:
            raise OrientationConfigError(f"orientation.error must be one of {', '.join(ERROR_MODES)}, got {self.error!r}")
        if self.frame not in FRAMES:
            raise OrientationConfigError(f"orientation.frame must be one of {', '.join(FRAMES)}, got {self.frame!r}")


def load_orientation_policy(path: str | os.PathLike[str] | None = None) -> OrientationPolicy:
    chosen = str(path) if path else os.environ.get(CONFIG_ENV, "")
    if not chosen:
        return OrientationPolicy()
    try:
        data = json.loads(Path(chosen).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise OrientationConfigError(f"cannot read the configuration file {chosen}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("orientation", {}), dict):
        raise OrientationConfigError(f"{chosen} must hold a JSON object with an orientation object")
    section = data.get("orientation", {})
    values = {}
    for key in ("error", "frame"):
        if key in section:
            if not isinstance(section[key], str):
                raise OrientationConfigError(f"orientation.{key} must be text")
            values[key] = section[key].strip().lower()
    return OrientationPolicy(**values)


# The policy that compute_pose_error uses when the caller gives none. The service sets it once at start from its
# configuration file; it is never changed while requests are being served.
_active = OrientationPolicy()


def active_policy() -> OrientationPolicy:
    return _active


def set_active_policy(policy: OrientationPolicy) -> None:
    global _active
    _active = policy
