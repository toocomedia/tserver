"""Credential encryption helpers using Fernet (symmetric AES-128-CBC + HMAC)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _fernet(secret: str) -> Fernet:
    # Derive a 32-byte key from the panel secret key via SHA-256
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str, secret: str) -> str:
    return _fernet(secret).encrypt(value.encode()).decode()


def decrypt(token: str, secret: str) -> str:
    return _fernet(secret).decrypt(token.encode()).decode()
