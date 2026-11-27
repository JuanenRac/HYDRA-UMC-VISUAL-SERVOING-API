# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_cut_answers.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""An answer of SAFETY-ZONES cut in the middle. urlopen is replaced, so nothing leaves the machine."""
from __future__ import annotations

import http.client
import io
import urllib.request

from hydra_umc_visual_servoing_api.safety_source import SafetyZonesClient


class CutAnswer(io.BytesIO):
    """An answer that stops in the middle of its body."""

    status = 200

    def read(self, *args):
        raise http.client.IncompleteRead(b"{", 100)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_cut_answer_means_not_reachable(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: CutAnswer())
    state, why = SafetyZonesClient("http://127.0.0.1:1").latest_state()
    assert state is None
    assert "not reachable" in why
