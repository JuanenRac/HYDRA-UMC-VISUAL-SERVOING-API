# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - tests/test_state_verification.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The signed verdict of SAFETY-ZONES: valid, altered, repeated, expired, downgraded and unsigned, alone and through the service."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from test_safety_zones_source import _captured, _request, _state, fake_zones, service

from hydra_umc_visual_servoing_api.api import VisualServoingServer
from hydra_umc_visual_servoing_api.main import main
from hydra_umc_visual_servoing_api.safety_source import SafetyZonesClient
from hydra_umc_visual_servoing_api.state_verification import (
    StateVerificationError,
    StateVerificationPolicy,
    StateVerifier,
    load_state_verification_policy,
)

pytestmark = pytest.mark.strict_safety

KEY = "k" * 40


def _message(state: dict, counter: int, key_id: str) -> bytes:
    body = {"state": state, "counter": counter, "key_id": key_id}
    return b"hydra-umc-safety-state-v1\n" + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def hmac_signature(state: dict, counter: int = None, key_id: str = "line1", key: str = KEY) -> dict:  # type: ignore[assignment]
    counter = counter if counter is not None else time.time_ns() // 1_000_000
    sig = hmac.new(key.encode(), _message(state, counter, key_id), hashlib.sha256).hexdigest()
    return {"alg": "HMAC-SHA256", "key_id": key_id, "counter": counter, "sig": sig}


def hmac_verifier(tmp_path, **policy) -> StateVerifier:
    key_file = tmp_path / "state.key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    return StateVerifier(StateVerificationPolicy(enabled=True, alg="hmac", key_file=str(key_file), key_id="line1", **policy))


# --- the verifier alone, HMAC ---


def test_a_valid_hmac_verdict_is_accepted(tmp_path) -> None:
    verifier = hmac_verifier(tmp_path)
    state = _state()
    assert verifier.check(state, hmac_signature(state)) == (True, "")


def test_an_unsigned_altered_forged_or_other_algorithm_verdict_is_refused(tmp_path) -> None:
    verifier = hmac_verifier(tmp_path)
    state = _state()
    good = hmac_signature(state)
    assert not verifier.check(state, None)[0]
    assert not verifier.check({**state, "state": "SAFE_STOP"}, good)[0]
    assert not verifier.check(state, {**good, "counter": good["counter"] + 1})[0]
    assert not verifier.check(state, hmac_signature(state, key="x" * 40))[0]
    assert not verifier.check(state, hmac_signature(state, key_id="other"))[0]
    assert not verifier.check(state, {**good, "alg": "Ed25519"})[0]
    assert not verifier.check(state, {**good, "alg": "none"})[0]
    assert not verifier.check(state, {**good, "counter": True})[0]
    assert not verifier.check(state, {k: v for k, v in good.items() if k != "sig"})[0]


def test_an_expired_verdict_is_refused(tmp_path) -> None:
    verifier = hmac_verifier(tmp_path, max_age_ms=1000)
    old = _state(age_ms=30_000)
    ok, why = verifier.check(old, hmac_signature(old))
    assert not ok and "old" in why
    future = _state(age_ms=-30_000)
    assert not verifier.check(future, hmac_signature(future))[0]
    no_time = {k: v for k, v in _state().items() if k != "timestamp_utc"}
    assert not verifier.check(no_time, hmac_signature(no_time))[0]


def test_an_older_verdict_is_a_replay_but_the_same_one_read_again_is_not(tmp_path) -> None:
    verifier = hmac_verifier(tmp_path)
    state = _state()
    newer = hmac_signature(state, counter=2_000)
    assert verifier.check(state, newer)[0]
    assert verifier.check(state, newer)[0], "polling the last verdict again is not a replay"
    ok, why = verifier.check(state, hmac_signature(state, counter=1_000))
    assert not ok and "replay" in why
    assert verifier.check(state, hmac_signature(state, counter=3_000))[0]


# --- the verifier alone, Ed25519 ---


def ed25519_pair(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    public_file = tmp_path / "state.pub"
    public_file.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return private, public_file


def ed_signature(private, state: dict, counter: int = None, key_id: str = "line1") -> dict:  # type: ignore[assignment]
    counter = counter if counter is not None else time.time_ns() // 1_000_000
    return {"alg": "Ed25519", "key_id": key_id, "counter": counter, "sig": private.sign(_message(state, counter, key_id)).hex()}


def test_ed25519_valid_altered_expired_and_replayed(tmp_path) -> None:
    private, public_file = ed25519_pair(tmp_path)
    verifier = StateVerifier(StateVerificationPolicy(enabled=True, alg="ed25519", key_file=str(public_file), key_id="line1"))
    state = _state()
    good = ed_signature(private, state, counter=5_000)
    assert verifier.check(state, good) == (True, "")
    assert not verifier.check({**state, "state": "SAFE_STOP"}, good)[0]
    assert not verifier.check(state, {**good, "sig": "00" * 64})[0]
    assert not verifier.check(state, {**good, "sig": "zz"})[0]
    assert not verifier.check(state, {**good, "sig": good["sig"][:-2]})[0]
    assert not verifier.check(state, ed_signature(private, state, key_id="other"))[0]
    from cryptography.hazmat.primitives.asymmetric import ed25519

    assert not verifier.check(state, ed_signature(ed25519.Ed25519PrivateKey.generate(), state))[0]
    old = _state(age_ms=30_000)
    assert not verifier.check(old, ed_signature(private, old, counter=6_000))[0]
    assert not verifier.check(state, ed_signature(private, state, counter=4_000))[0]
    # a verdict signed with HMAC is refused by a consumer that expects Ed25519, even if someone knows a key
    assert not verifier.check(state, hmac_signature(state))[0]


# --- the configuration ---


def test_the_policy_is_off_by_default_and_the_configuration_is_checked(tmp_path) -> None:
    assert not load_state_verification_policy(None).enabled
    config = tmp_path / "c.json"
    config.write_text(json.dumps({"servo_log": {}}), encoding="utf-8")
    assert not load_state_verification_policy(config).enabled
    config.write_text(json.dumps({"safety_state_verification": {"enabled": True, "alg": "hmac", "key_file": "k", "max_age_ms": 500}}), encoding="utf-8")
    policy = load_state_verification_policy(config)
    assert policy.enabled and policy.max_age_ms == 500
    for bad in (
        {"enabled": "yes"},
        {"enabled": True},
        {"enabled": True, "key_file": "k", "alg": "rsa"},
        {"enabled": True, "key_file": "k", "max_age_ms": 0},
        {"enabled": True, "key_file": "k", "max_age_ms": 10**9},
        {"enabled": True, "key_file": "k", "extra": 1},
    ):
        config.write_text(json.dumps({"safety_state_verification": bad}), encoding="utf-8")
        with pytest.raises(StateVerificationError):
            load_state_verification_policy(config)
    config.write_text("{", encoding="utf-8")
    with pytest.raises(StateVerificationError):
        load_state_verification_policy(config)


def test_a_short_or_missing_key_stops_the_verifier(tmp_path) -> None:
    short = tmp_path / "short"
    short.write_text("abc", encoding="utf-8")
    with pytest.raises(StateVerificationError, match="at least 32"):
        StateVerifier(StateVerificationPolicy(enabled=True, alg="hmac", key_file=str(short)))
    with pytest.raises(StateVerificationError):
        StateVerifier(StateVerificationPolicy(enabled=True, alg="hmac", key_file=str(tmp_path / "missing")))
    with pytest.raises(StateVerificationError):
        StateVerifier(StateVerificationPolicy())


def test_serve_refuses_a_verification_without_a_safety_zones_address(tmp_path, capsys, monkeypatch) -> None:
    key = tmp_path / "k"
    key.write_text(KEY, encoding="utf-8")
    config = tmp_path / "c.json"
    config.write_text(json.dumps({"safety_state_verification": {"enabled": True, "alg": "hmac", "key_file": str(key)}}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["hydra-umc-visual-servoing-api", "serve", "--port", "0", "--config", str(config)])
    monkeypatch.delenv("HYDRA_UMC_SAFETY_ZONES_URL", raising=False)
    assert main() == 2
    assert "no SAFETY-ZONES address" in capsys.readouterr().err


# --- through the service ---


def test_with_verification_on_only_a_valid_signed_verdict_authorizes(tmp_path) -> None:
    verifier = hmac_verifier(tmp_path)
    ready = _state("READY")
    good = {"sdkSafetyState": ready, "sdkSafetyStateSignature": hmac_signature(ready)}
    with fake_zones(body=good) as (url, _):
        server = VisualServoingServer(("127.0.0.1", 0), "t", None, "", safety_zones=SafetyZonesClient(url), state_verifier=verifier)
        import threading

        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            assert _request(base, captured_at_utc=_captured())["outcome"] == "accepted"
        finally:
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize("tamper", ["unsigned", "altered", "other_key", "old", "other_alg"])
def test_with_verification_on_a_bad_verdict_is_inhibited_even_if_it_says_ready(tmp_path, tamper: str) -> None:
    verifier = hmac_verifier(tmp_path)
    ready = _state("READY", age_ms=30_000 if tamper == "old" else 10.0)
    signature = hmac_signature(ready, key="x" * 40 if tamper == "other_key" else KEY)
    if tamper == "other_alg":
        signature["alg"] = "Ed25519"
    body: dict = {"sdkSafetyState": ready, "sdkSafetyStateSignature": signature}
    if tamper == "unsigned":
        del body["sdkSafetyStateSignature"]
    if tamper == "altered":
        body["sdkSafetyState"] = {**ready, "state": "READY", "source": "someone-else"}
    with fake_zones(body=body) as (url, _):
        server = VisualServoingServer(("127.0.0.1", 0), "t", None, "", safety_zones=SafetyZonesClient(url), state_verifier=verifier)
        import threading

        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            answer = _request(base, captured_at_utc=_captured())
            assert answer["outcome"] == "inhibited", answer
        finally:
            server.shutdown()
            server.server_close()


def test_without_verification_an_unsigned_verdict_still_works(tmp_path) -> None:
    with fake_zones(body={"sdkSafetyState": _state("READY")}) as (url, _), service(SafetyZonesClient(url)) as base:
        assert _request(base, captured_at_utc=_captured())["outcome"] == "accepted"
