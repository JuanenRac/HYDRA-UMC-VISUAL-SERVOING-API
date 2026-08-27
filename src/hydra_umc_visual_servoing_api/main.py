# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Entry point for HYDRA-UMC-VISUAL-SERVOING-API.

Skeleton stage: prints identity and exits 0. Real servoing logic (6-DOF pose
estimation, Eye-in-Hand/Eye-to-Hand error-delta calculation, low-latency
feed towards the HYDRA-UMC core) lands when this project's turn comes up in
SONNET/5.PLAN_EJECUCION_32_PROYECTOS_NUEVOS.txt.
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version

PROJECT_NAME = "HYDRA-UMC-VISUAL-SERVOING-API"
DIST_NAME = "hydra-umc-visual-servoing-api"
ROLE = (
    "Closed-loop kinematic correction from Hailo-8 visual feedback to "
    "real-time pose corrections for the HYDRA-UMC core."
)


def get_version() -> str:
    """Read the running version from installed package metadata, which is
    sourced from pyproject.toml - the single place bump_version.py edits."""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0.0.0-dev (package not installed - run build.sh/build.bat first)"


def main() -> int:
    print(f"{PROJECT_NAME} v{get_version()}")
    print(ROLE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
