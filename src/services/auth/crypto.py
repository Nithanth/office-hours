"""Encryption helpers for user-owned integration secrets."""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

import config as _cfg


class SecretEncryptionError(RuntimeError):
    """Raised when secret encryption cannot be used safely."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = _cfg.config.LUX_SECRET_ENCRYPTION_KEY
    if not key:
        raise SecretEncryptionError("Set LUX_SECRET_ENCRYPTION_KEY before storing user secrets.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretEncryptionError(
            "LUX_SECRET_ENCRYPTION_KEY must be a valid Fernet key."
        ) from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretEncryptionError("Stored secret could not be decrypted.") from exc
