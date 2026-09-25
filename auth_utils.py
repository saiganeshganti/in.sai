"""
Authentication helpers for per-recruiter accounts.

Kept deliberately small and dependency-light:
  - Password hashing uses only the Python standard library (PBKDF2-HMAC).
  - Session tokens are a simple HMAC-signed, base64 payload (no external
    JWT library needed).
  - The Gmail App Password each recruiter enters is encrypted at rest
    using Fernet (from the `cryptography` package) so it isn't stored
    in plaintext in the database.

Required environment variables (set these on Render):
    SECRET_KEY    any long random string -- signs session tokens
    FERNET_KEY    a Fernet key used to encrypt/decrypt stored SMTP
                   app passwords. Generate one locally with:

                       python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

                   and paste the output as the FERNET_KEY env var.
                   If this ever changes, every recruiter's saved SMTP
                   app password becomes unreadable and must be re-entered.
"""

import os
import hmac
import time
import base64
import hashlib
import secrets
import json

from cryptography.fernet import Fernet, InvalidToken


# =========================================================
# CONFIG
# =========================================================

SECRET_KEY = os.getenv("SECRET_KEY", "")
FERNET_KEY = os.getenv("FERNET_KEY", "")

if not SECRET_KEY:
    print("WARNING: SECRET_KEY is not set. Login sessions will not be secure.")

_fernet = Fernet(FERNET_KEY.encode()) if FERNET_KEY else None

if not _fernet:
    print("WARNING: FERNET_KEY is not set. Recruiters will not be able to save SMTP credentials.")


# =========================================================
# PASSWORD HASHING (PBKDF2-HMAC-SHA256, stdlib only)
# =========================================================

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PBKDF2_ITERATIONS
    )
    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, expected_hex = stored_hash.split("$", 1)
    except ValueError:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PBKDF2_ITERATIONS
    )

    return hmac.compare_digest(derived.hex(), expected_hex)


# =========================================================
# SESSION TOKENS (HMAC-signed, no external JWT library)
# =========================================================
# Format: base64(json payload) + "." + base64(hmac signature)

_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def create_session_token(recruiter_id: int) -> str:
    payload = {
        "recruiter_id": recruiter_id,
        "issued_at": int(time.time())
    }

    payload_bytes = json.dumps(payload).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return f"{payload_b64}.{signature}"


def verify_session_token(token: str):
    """Returns the recruiter_id if the token is valid and not expired, else None."""
    if not token or "." not in token:
        return None

    payload_b64, signature = token.rsplit(".", 1)

    expected_signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes)
    except Exception:
        return None

    issued_at = payload.get("issued_at", 0)
    if time.time() - issued_at > _TOKEN_TTL_SECONDS:
        return None

    return payload.get("recruiter_id")


# =========================================================
# PASSWORD RESET TOKENS
# =========================================================
# A single-use, time-limited random token emailed to the recruiter.
# Not a signed/verifiable token like the session token above -- it's
# looked up directly in the database (see main.py), so a long random
# value is sufficient here.

def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


# =========================================================
# SMTP APP PASSWORD ENCRYPTION
# =========================================================

def encrypt_secret(plaintext: str) -> str:
    if not _fernet:
        raise RuntimeError("FERNET_KEY is not configured on the server.")
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not _fernet:
        raise RuntimeError("FERNET_KEY is not configured on the server.")
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise RuntimeError("Stored SMTP credential could not be decrypted. It may need to be re-entered.")
