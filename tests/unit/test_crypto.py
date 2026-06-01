import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


def test_round_trip(fernet_key, monkeypatch):
    from databridge.config import Settings, ServerConfig
    monkeypatch.setenv("DATABRIDGE_CONFIG", "")
    import databridge.crypto as crypto_mod
    from unittest.mock import patch
    settings = Settings(
        server=ServerConfig(),
        database_url="postgresql://x",
        encryption_key=fernet_key,
        datasources=(),
    )
    with patch("databridge.crypto.get_settings", return_value=settings):
        from databridge.crypto import encrypt_credentials, decrypt_credentials
        payload = {"user": "alice", "password": "s3cr3t", "token": "abc123"}
        ct = encrypt_credentials(payload)
        assert isinstance(ct, bytes)
        recovered = decrypt_credentials(ct)
        assert recovered == payload


def test_empty_payload(fernet_key):
    from databridge.config import Settings, ServerConfig
    from databridge.crypto import encrypt_credentials, decrypt_credentials
    from unittest.mock import patch
    settings = Settings(
        server=ServerConfig(), database_url="postgresql://x",
        encryption_key=fernet_key, datasources=(),
    )
    with patch("databridge.crypto.get_settings", return_value=settings):
        ct = encrypt_credentials({})
        assert decrypt_credentials(ct) == {}


def test_large_payload(fernet_key):
    from databridge.config import Settings, ServerConfig
    from databridge.crypto import encrypt_credentials, decrypt_credentials
    from unittest.mock import patch
    settings = Settings(
        server=ServerConfig(), database_url="postgresql://x",
        encryption_key=fernet_key, datasources=(),
    )
    with patch("databridge.crypto.get_settings", return_value=settings):
        big = {"data": "x" * (1024 * 1024)}  # >1 MB
        ct = encrypt_credentials(big)
        assert decrypt_credentials(ct) == big


def test_invalid_ciphertext_raises(fernet_key):
    from databridge.config import Settings, ServerConfig
    from databridge.crypto import decrypt_credentials
    from cryptography.fernet import InvalidToken
    from unittest.mock import patch
    settings = Settings(
        server=ServerConfig(), database_url="postgresql://x",
        encryption_key=fernet_key, datasources=(),
    )
    with patch("databridge.crypto.get_settings", return_value=settings):
        with pytest.raises(InvalidToken):
            decrypt_credentials(b"not-valid-ciphertext")
