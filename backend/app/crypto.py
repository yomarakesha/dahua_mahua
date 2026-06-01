"""At-rest encryption for NVR RTSP passwords.

Uses Fernet (AES-128-CBC + HMAC) with a key from settings.nvr_secret_key.
The key is intentionally separate from the JWT secret so that compromising
issued tokens does not expose stored device credentials, and vice-versa.

A DB dump alone is not enough to read passwords — an attacker also needs
the Fernet key from the environment / secret store.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.settings import get_settings


@lru_cache
def _cipher() -> Fernet:
    settings = get_settings()
    key = settings.nvr_secret_key.encode()
    try:
        return Fernet(key)
    except (ValueError, TypeError) as e:
        raise RuntimeError(
            "NVR_SECRET_KEY must be a 32-byte url-safe base64 string. "
            "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from e


def encrypt_password(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_password(token: str) -> str:
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError(
            "Failed to decrypt NVR password — NVR_SECRET_KEY mismatch with stored ciphertext"
        ) from e
