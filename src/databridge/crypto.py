import json
from cryptography.fernet import Fernet

from databridge.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode())


def encrypt_credentials(creds: dict) -> bytes:
    return _fernet().encrypt(json.dumps(creds).encode())


def decrypt_credentials(ciphertext: bytes) -> dict:
    return json.loads(_fernet().decrypt(ciphertext))
