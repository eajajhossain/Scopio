import base64
import hashlib
import hmac
import json
import time

from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    make_token,
    parse_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret!")
    assert h != "s3cret!"          # never store plaintext
    assert verify_password("s3cret!", h)
    assert not verify_password("wrong", h)


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")  # random salt each time


def test_verify_handles_garbage():
    assert not verify_password("x", None)
    assert not verify_password("x", "not-a-valid-hash")


def test_token_roundtrip():
    tok = make_token("user-1", "tenant-1")
    data = parse_token(tok)
    assert data["uid"] == "user-1" and data["tid"] == "tenant-1"
    assert data["exp"] > time.time()  # tokens carry an expiry now


def test_tampered_token_rejected():
    tok = make_token("user-1", "tenant-1")
    # flip a character in the payload
    bad = ("A" if tok[0] != "A" else "B") + tok[1:]
    assert parse_token(bad) is None
    assert parse_token("garbage") is None
    assert parse_token("a.b.c") is None


def _forge_token(payload: dict) -> str:
    """Correctly-signed token with an arbitrary payload (tests expiry, not the HMAC)."""
    from app.core.config import settings

    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(settings.secret_key.encode(), b64.encode(), hashlib.sha256).digest()
    return f"{b64}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def test_expired_token_rejected():
    expired = _forge_token({"uid": "u", "tid": "t", "exp": int(time.time()) - 10})
    assert parse_token(expired) is None


def test_legacy_token_without_exp_rejected():
    # Pre-expiry tokens (no exp claim) must be rejected — the user just logs in again.
    legacy = _forge_token({"uid": "u", "tid": "t"})
    assert parse_token(legacy) is None


def test_encrypt_secret_roundtrip():
    enc = encrypt_secret("gmail-app-password")
    assert enc != "gmail-app-password" and enc.startswith("enc$")
    assert decrypt_secret(enc) == "gmail-app-password"


def test_decrypt_secret_legacy_plaintext_passthrough():
    # Rows stored before encryption existed keep working until re-connected.
    assert decrypt_secret("old-plaintext-password") == "old-plaintext-password"
    assert decrypt_secret(None) is None


def test_decrypt_secret_wrong_key_returns_none(monkeypatch):
    import app.core.security as sec

    enc = encrypt_secret("pw")
    monkeypatch.setattr(sec.settings, "secret_key", "a-different-secret")
    assert decrypt_secret(enc) is None  # never returns garbage/ciphertext
