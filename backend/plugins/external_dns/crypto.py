"""
plugins/external_dns/crypto.py — Fernet encryption for provider credentials.

Credentials are stored encrypted at rest and never returned to the UI in
plaintext. Key derivation matches services/apps_engine/secret_vault.py so the
whole panel shares one SECRET_KEY-derived Fernet key.
"""
from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

import config


def _fernet() -> Fernet:
    if getattr(config, "_SECRET_KEY_EPHEMERAL", False) or not config.SECRET_KEY:
        raise RuntimeError(
            "Panel SECRET_KEY must be configured before external DNS credentials can be stored."
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(config.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_dict(data: dict) -> str:
    """Encrypt a credential dict into an ASCII token string."""
    blob = json.dumps(data or {}, separators=(",", ":"), sort_keys=True)
    return _fernet().encrypt(blob.encode("utf-8")).decode("ascii")


def decrypt_dict(token: str) -> dict:
    """Decrypt a stored credential token back into a dict."""
    try:
        raw = _fernet().decrypt((token or "").encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "External DNS credentials cannot be read with this panel key."
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Stored external DNS credentials are corrupt.") from exc
    return data if isinstance(data, dict) else {}


def mask_secret(value: str, keep: int = 4) -> str:
    """Mask a secret for safe display — only the last few chars are shown."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * 8}{value[-keep:]}"
