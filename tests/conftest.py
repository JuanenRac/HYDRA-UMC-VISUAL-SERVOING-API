# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/conftest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The older HTTP tests send a bare safety_state string. By default the service refuses that (it wants the
SafetyState object with a recent timestamp), so those tests run with the legacy switch on; the tests marked
`strict_safety` run with the real default."""
from __future__ import annotations

import pytest

from hydra_umc_visual_servoing_api import api


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "strict_safety: run with the default safety-state handling (object with a time)")


@pytest.fixture(autouse=True)
def _legacy_safety_strings_for_older_tests(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("strict_safety") is None:
        monkeypatch.setattr(api, "DEFAULT_ACCEPT_LEGACY_SAFETY_STRING", True)
