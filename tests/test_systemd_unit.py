# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - systemd unit tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The unit that is deployed asks the safety zones service for the verdict: the flag, its default address and the environment file."""

import re
from pathlib import Path

UNIT = (Path(__file__).resolve().parents[1] / "systemd" / "hydra-umc-visual-servoing-api.service").read_text(encoding="utf-8")


def lines(prefix):
    return [line for line in UNIT.splitlines() if line.startswith(prefix)]


def test_the_service_is_started_with_the_safety_zones_option() -> None:
    (exec_start,) = lines("ExecStart=")
    assert "--safety-zones-url ${HYDRA_UMC_SAFETY_ZONES_URL}" in exec_start


def test_the_default_address_is_the_loopback_one_of_the_safety_zones_service() -> None:
    assert "Environment=HYDRA_UMC_SAFETY_ZONES_URL=http://127.0.0.1:8108" in lines("Environment=")
    manifest = (Path(__file__).resolve().parents[2] / "HYDRA-UMC-SAFETY-ZONES" / "hydra-umc.project.json")
    if manifest.is_file():
        assert re.search(r'"port":\s*8108', manifest.read_text(encoding="utf-8"))


def test_an_optional_environment_file_is_read_after_the_default() -> None:
    env_files = lines("EnvironmentFile=")
    assert env_files == ["EnvironmentFile=-/etc/hydra-umc/visual-servoing-api.env"]  # the dash: the file may be missing
    default_at = UNIT.index("Environment=HYDRA_UMC_SAFETY_ZONES_URL=")
    assert UNIT.index("EnvironmentFile=") > default_at  # so the file overrides the default


def test_the_service_still_listens_on_the_loopback_address_only() -> None:
    (exec_start,) = lines("ExecStart=")
    assert "--addr 127.0.0.1" in exec_start
