# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/access.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Optional Bearer token for the HTTP API.

On loopback the API needs no token (the only caller is the machine itself) unless one is set. Away from loopback a token is
mandatory: the API refuses to start without one. When a token is set, every request must carry `Authorization: Bearer <token>`;
the comparison takes constant time. The token comes from a file (--token-file) or the HYDRA_UMC_VISUAL_SERVOING_TOKEN environment variable.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path

TOKEN_ENV = "HYDRA_UMC_VISUAL_SERVOING_TOKEN"
MIN_TOKEN_LENGTH = 16


def is_loopback(addr: str) -> bool:
    """True for localhost and the loopback addresses; a wildcard (0.0.0.0, ::) or any other address is not loopback."""
    if addr.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(addr.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def load_token(token_file: str | Path | None, environ: dict[str, str] | None = None) -> str:
    """The configured token ("" when none). Raises ValueError for an unreadable file or a token that is too short."""
    env = os.environ if environ is None else environ
    token = env.get(TOKEN_ENV, "").strip()
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"cannot read the token file: {error}") from error
    if token and len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"the bearer token must have at least {MIN_TOKEN_LENGTH} characters")
    return token


def bind_problem(addr: str, token: str) -> str | None:
    """Why this address cannot be served (None when it can): away from loopback a token is required."""
    if not is_loopback(addr) and not token:
        return (f"--addr {addr} is not loopback: set --token-file (or {TOKEN_ENV}) so that only a caller holding the token can use the API")
    return None


def authorized(handler: BaseHTTPRequestHandler, expected: str) -> bool:
    """True when no token is configured, or the request presents exactly that Bearer token."""
    if not expected:
        return True
    scheme, _, presented = handler.headers.get("Authorization", "").partition(" ")
    return scheme == "Bearer" and hmac.compare_digest(presented.strip().encode("utf-8"), expected.encode("utf-8"))


def reject(handler: BaseHTTPRequestHandler) -> None:
    """Answer 401 for a request that did not present the token."""
    body = b'{"error": "a valid bearer token is required"}'
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", "Bearer")
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
