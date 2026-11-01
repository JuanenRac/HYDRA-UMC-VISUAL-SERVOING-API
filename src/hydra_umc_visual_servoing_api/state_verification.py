# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/state_verification.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Verifying the signature of the safety state that SAFETY-ZONES serves.

Off by default. With `safety_state_verification` switched on in the configuration file, the verdict read from SAFETY-ZONES (`GET /state`) must carry
`sdkSafetyStateSignature`, and the service only acts on a verdict that is signed with the key it expects (HMAC-SHA256 with a shared key, or Ed25519
with the public key of SAFETY-ZONES), made with the key id it expects, not older than `max_age_ms`, and not older than the newest verdict it has
already read (the counter only goes up). Anything else - unsigned, forged, signed with the other algorithm, expired or replayed - is `INHIBITED`,
never READY. The same verdict read again while it is still fresh is not a replay.

The configuration file (the one `--config` / HYDRA_UMC_VISUAL_SERVOING_CONFIG names):

    {"safety_state_verification": {"enabled": true, "alg": "ed25519", "key_file": "/etc/hydra/safety-zones.pub", "key_id": "line1", "max_age_ms": 2000}}

`alg` is `hmac` (key_file holds the shared key, at least 32 characters) or `ed25519` (key_file is the PEM public key; it needs the `cryptography` package).
The message that is signed is the one SAFETY-ZONES builds (see its `signing.py`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CONFIG_ENV = "HYDRA_UMC_VISUAL_SERVOING_CONFIG"
DOMAIN = b"hydra-umc-safety-state-v1\n"
ALG_HMAC = "HMAC-SHA256"
ALG_ED25519 = "Ed25519"
MIN_KEY_CHARS = 32
DEFAULT_MAX_AGE_MS = 2000.0
MAX_AGE_LIMIT_MS = 60_000.0
_SECTION_FIELDS = {"enabled", "alg", "key_file", "key_id", "max_age_ms"}


class StateVerificationError(ValueError):
    """The configuration or the key cannot be used."""


@dataclass(frozen=True)
class StateVerificationPolicy:
    enabled: bool = False
    alg: str = "hmac"
    key_file: str = ""
    key_id: str = ""
    max_age_ms: float = DEFAULT_MAX_AGE_MS

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise StateVerificationError("safety_state_verification.enabled must be true or false")
        if self.alg not in ("hmac", "ed25519"):
            raise StateVerificationError("safety_state_verification.alg must be hmac or ed25519")
        if not isinstance(self.key_file, str) or not isinstance(self.key_id, str):
            raise StateVerificationError("safety_state_verification.key_file and key_id must be text")
        if isinstance(self.max_age_ms, bool) or not isinstance(self.max_age_ms, (int, float)) or not 0 < self.max_age_ms <= MAX_AGE_LIMIT_MS:
            raise StateVerificationError(f"safety_state_verification.max_age_ms must be a number above 0 and at most {int(MAX_AGE_LIMIT_MS)}")
        if self.enabled and not self.key_file:
            raise StateVerificationError("safety_state_verification is enabled but has no key_file")


def load_state_verification_policy(path: str | os.PathLike[str] | None = None) -> StateVerificationPolicy:
    chosen = str(path) if path else os.environ.get(CONFIG_ENV, "")
    if not chosen:
        return StateVerificationPolicy()
    try:
        data = json.loads(Path(chosen).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StateVerificationError(f"cannot read the configuration file {chosen}: {error}") from error
    section = data.get("safety_state_verification", {}) if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise StateVerificationError(f"{chosen} must hold a JSON object whose safety_state_verification entry is an object")
    unknown = sorted(set(section) - _SECTION_FIELDS)
    if unknown:
        raise StateVerificationError(f"unknown field(s) in safety_state_verification: {', '.join(unknown)}")
    return StateVerificationPolicy(**section)


def _message(state: dict, counter: int, key_id: str) -> bytes:
    body = {"state": state, "counter": counter, "key_id": key_id}
    return DOMAIN + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _age_ms(state: dict, now_ms: float) -> float | None:
    stamp = state.get("timestamp_utc")
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return now_ms - moment.timestamp() * 1000.0


class StateVerifier:
    """Checks each verdict and remembers the newest counter it accepted."""

    def __init__(self, policy: StateVerificationPolicy) -> None:
        if not policy.enabled:
            raise StateVerificationError("the verifier is built only for an enabled policy")
        self.policy = policy
        self._expected = ALG_ED25519 if policy.alg == "ed25519" else ALG_HMAC
        self._hmac_key: bytes | None = None
        self._public_key: object | None = None
        if policy.alg == "hmac":
            try:
                text = Path(policy.key_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise StateVerificationError(f"the key file cannot be read: {exc.strerror or type(exc).__name__}") from exc
            if len(text) < MIN_KEY_CHARS:
                raise StateVerificationError(f"the key file must hold at least {MIN_KEY_CHARS} characters")
            self._hmac_key = text.encode("utf-8")
        else:
            try:
                from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
                from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: PLC0415
            except ImportError as exc:
                raise StateVerificationError("Ed25519 needs the cryptography package (pip install cryptography)") from exc
            try:
                key = serialization.load_pem_public_key(Path(policy.key_file).read_bytes())
            except OSError as exc:
                raise StateVerificationError(f"the key file cannot be read: {exc.strerror or type(exc).__name__}") from exc
            except (ValueError, TypeError) as exc:
                raise StateVerificationError("the key file is not a PEM public key") from exc
            if not isinstance(key, ed25519.Ed25519PublicKey):
                raise StateVerificationError("the key file does not hold an Ed25519 public key")
            self._public_key = key
        self._lock = threading.Lock()
        self._newest = 0

    def check(self, state: object, signature: object, now_ms: float | None = None) -> tuple[bool, str]:
        """(True, "") for a verdict that may be acted on, else (False, why)."""
        if not isinstance(state, dict) or not isinstance(signature, dict):
            return False, "the safety state is not signed"
        if signature.get("alg") != self._expected:
            return False, f"the safety state is not signed with {self._expected}"
        counter, sig, key_id = signature.get("counter"), signature.get("sig"), signature.get("key_id")
        if isinstance(counter, bool) or not isinstance(counter, int) or counter < 1 or not isinstance(sig, str) or not isinstance(key_id, str):
            return False, "the signature of the safety state is malformed"
        if self.policy.key_id and key_id != self.policy.key_id:
            return False, "the safety state was signed with another key id"
        message = _message(state, counter, key_id)
        if self._hmac_key is not None:
            if not hmac.compare_digest(hmac.new(self._hmac_key, message, hashlib.sha256).hexdigest(), sig):
                return False, "the signature of the safety state does not match"
        else:
            try:
                raw = bytes.fromhex(sig)
                if len(raw) != 64:
                    raise ValueError
                self._public_key.verify(raw, message)  # type: ignore[attr-defined,union-attr]
            except Exception:  # noqa: BLE001 - a bad signature, a bad encoding and a wrong key are all "does not match"
                return False, "the signature of the safety state does not match"
        now = time.time() * 1000.0 if now_ms is None else now_ms
        age = _age_ms(state, now)
        if age is None:
            return False, "the signed safety state has no readable time"
        if age > self.policy.max_age_ms or age < -self.policy.max_age_ms:
            return False, f"the signed safety state is {int(age)} ms old (limit {int(self.policy.max_age_ms)} ms)"
        with self._lock:
            if counter < self._newest:
                return False, "the signed safety state is older than one already read (replay)"
            self._newest = counter
        return True, ""
