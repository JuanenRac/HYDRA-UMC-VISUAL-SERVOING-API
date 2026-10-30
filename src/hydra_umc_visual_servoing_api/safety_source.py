# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/safety_source.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Where the safety state of a correction comes from.

By default the caller of a request says what the safety state is. When the service is started with a
SAFETY-ZONES address, the caller's claim is ignored: the service asks SAFETY-ZONES for its latest verdict
(GET /state) and judges that, with the same age limit as any other SafetyState. Anything that goes wrong
(no answer, an error, a body that is not a SafetyState) is the absence of a confirmation: the state is
INHIBITED, never READY.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

DEFAULT_TIMEOUT_MS = 500.0
STATE_PATH = "/state"
# a SafetyState is a few hundred bytes; anything larger is not one
MAX_BODY_BYTES = 65536


def normalize_url(url: str) -> str:
    """The base address of SAFETY-ZONES, or ValueError when it is not an http(s) address."""
    text = url.strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("the SAFETY-ZONES address must be an http:// or https:// URL")
    return text


class SafetyZonesClient:
    def __init__(self, base_url: str, token: str = "", timeout_ms: float = DEFAULT_TIMEOUT_MS) -> None:
        if not timeout_ms > 0:
            raise ValueError("the SAFETY-ZONES timeout must be positive")
        self.base_url = normalize_url(base_url)
        self.token = token
        self.timeout_s = timeout_ms / 1000.0

    def latest_state(self) -> tuple[object | None, str | None]:
        """(the SafetyState object, None) or (None, why there is none)."""
        request = urllib.request.Request(self.base_url + STATE_PATH, method="GET")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310 - scheme checked above
                raw = response.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, "SAFETY-ZONES has not reached a verdict yet"
            return None, f"SAFETY-ZONES answered HTTP {exc.code}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return None, f"SAFETY-ZONES is not reachable ({type(exc).__name__})"
        if len(raw) > MAX_BODY_BYTES:
            return None, "SAFETY-ZONES answered with a body that is too large"
        try:
            body = json.loads(raw)
        except ValueError:
            return None, "SAFETY-ZONES did not answer with JSON"
        state = body.get("sdkSafetyState") if isinstance(body, dict) else None
        if not isinstance(state, dict):
            return None, "SAFETY-ZONES answered without a SafetyState"
        return state, None
