
import base64
import hashlib
import hmac
import json
import logging
import os
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, dk_hex = stored.split("$", 1)
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), dk_hex)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    sig = hmac.new(settings.secret_key.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return _b64e(sig)


def make_token(user_id: str, tenant_id: str) -> str:
    exp = int(time.time()) + settings.token_ttl_days * 86400
    payload = _b64e(json.dumps({"uid": user_id, "tid": tenant_id, "exp": exp}).encode())
    return f"{payload}.{_sign(payload)}"


def parse_token(token: str) -> dict | None:
    """Return {'uid','tid'} if the token is valid AND not expired, else None.
    Tokens without an `exp` claim (pre-expiry format) are rejected — re-login."""
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(_b64d(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if "uid" not in data or "tid" not in data:
        return None
    if not isinstance(data.get("exp"), int) or data["exp"] < time.time():
        return None
    return data


# --- Secrets at rest (SMTP app passwords) ------------------------------------
# Fernet (AES128-CBC + HMAC) keyed from SECRET_KEY, so no extra key to manage.
# Rotating SECRET_KEY therefore invalidates stored SMTP passwords (users
# reconnect their email) — an accepted trade-off documented in PRODUCTION.md.

_ENC_PREFIX = "enc$"


def _fernet():
    from cryptography.fernet import Fernet  # lazy: keeps startup light

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    """Encrypt a stored credential (marked with a prefix so legacy rows still read)."""
    return _ENC_PREFIX + _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(stored: str | None) -> str | None:
    """Decrypt a stored credential. Legacy plaintext rows (no prefix) pass through
    so existing accounts keep working; they re-encrypt on the next connect."""
    if stored is None or not stored.startswith(_ENC_PREFIX):
        return stored
    try:
        return _fernet().decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except Exception:  # noqa: BLE001 — wrong SECRET_KEY / corrupt value
        logger.warning("stored credential could not be decrypted (SECRET_KEY changed?)")
        return None
