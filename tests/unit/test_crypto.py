"""Tests for services.auth.crypto encryption helpers."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Generate a real Fernet key for tests
_TEST_FERNET_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Provide a valid Fernet key and clear the LRU cache between tests."""
    import config as _cfg

    monkeypatch.setattr(_cfg.config, "LUX_SECRET_ENCRYPTION_KEY", _TEST_FERNET_KEY)

    # Clear the cached Fernet instance so each test picks up the monkeypatched key
    from services.auth.crypto import _fernet

    _fernet.cache_clear()

    yield

    _fernet.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_encrypt_then_decrypt_roundtrip():
    """Encrypting then decrypting should return the original plaintext."""
    from services.auth.crypto import decrypt_secret, encrypt_secret

    plaintext = "sk-ant-api-key-12345"
    ciphertext = encrypt_secret(plaintext)

    # Ciphertext must differ from plaintext
    assert ciphertext != plaintext
    # Round-trip
    assert decrypt_secret(ciphertext) == plaintext


def test_encrypt_produces_different_ciphertexts():
    """Fernet uses random IVs, so encrypting the same value twice yields different output."""
    from services.auth.crypto import encrypt_secret

    a = encrypt_secret("same-secret")
    b = encrypt_secret("same-secret")
    assert a != b


def test_decrypt_invalid_token_raises():
    """Decrypting garbage should raise SecretEncryptionError."""
    from services.auth.crypto import SecretEncryptionError, decrypt_secret

    with pytest.raises(SecretEncryptionError, match="could not be decrypted"):
        decrypt_secret("not-a-valid-fernet-token")


def test_missing_key_raises(monkeypatch):
    """encrypt_secret must raise when LUX_SECRET_ENCRYPTION_KEY is empty."""
    import config as _cfg
    from services.auth.crypto import SecretEncryptionError, _fernet, encrypt_secret

    _fernet.cache_clear()
    monkeypatch.setattr(_cfg.config, "LUX_SECRET_ENCRYPTION_KEY", "")

    with pytest.raises(SecretEncryptionError, match="Set LUX_SECRET_ENCRYPTION_KEY"):
        encrypt_secret("anything")


def test_invalid_key_raises(monkeypatch):
    """A non-base64 key must raise SecretEncryptionError."""
    import config as _cfg
    from services.auth.crypto import SecretEncryptionError, _fernet, encrypt_secret

    _fernet.cache_clear()
    monkeypatch.setattr(_cfg.config, "LUX_SECRET_ENCRYPTION_KEY", "not-a-valid-key")

    with pytest.raises(SecretEncryptionError, match="valid Fernet key"):
        encrypt_secret("anything")


def test_encrypt_empty_string():
    """Encrypting an empty string should work and round-trip."""
    from services.auth.crypto import decrypt_secret, encrypt_secret

    ct = encrypt_secret("")
    assert decrypt_secret(ct) == ""


def test_encrypt_unicode():
    """Unicode content should round-trip correctly."""
    from services.auth.crypto import decrypt_secret, encrypt_secret

    plaintext = "Hello, \u4e16\u754c! \U0001f680"
    assert decrypt_secret(encrypt_secret(plaintext)) == plaintext
