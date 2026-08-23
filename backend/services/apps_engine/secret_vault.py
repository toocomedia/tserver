"""Encrypted App Engine secret storage. Values never leave this module's API."""
from __future__ import annotations

from datetime import datetime
import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.container_app_secret import ContainerAppCredential, ContainerAppCredentialAccess, ContainerAppSecret


def _fernet() -> Fernet:
    if getattr(config, "_SECRET_KEY_EPHEMERAL", False) or not config.SECRET_KEY:
        raise RuntimeError("Panel SECRET_KEY must be configured before App Engine secrets can be used.")
    key = base64.urlsafe_b64encode(hashlib.sha256(config.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("App Engine encrypted secret cannot be read with this panel key.") from exc


async def ensure_secret(
    db: AsyncSession, app_id: int, key: str, purpose: str, *, rotate: bool = False,
) -> tuple[ContainerAppSecret, bool]:
    """Return active secret, or make an opaque high-entropy value server-side."""
    current = await db.scalar(select(ContainerAppSecret).where(
        ContainerAppSecret.app_id == app_id,
        ContainerAppSecret.key == key,
        ContainerAppSecret.status == "active",
    ).order_by(ContainerAppSecret.version.desc()))
    if current and not rotate:
        return current, False
    if current:
        current.status = "rotated"
    previous = await db.scalar(select(func.max(ContainerAppSecret.version)).where(
        ContainerAppSecret.app_id == app_id, ContainerAppSecret.key == key,
    )) or 0
    record = ContainerAppSecret(
        app_id=app_id,
        key=key,
        purpose=purpose[:255] or "Application secret",
        version=int(previous) + 1,
        encrypted_value=encrypt(secrets.token_urlsafe(48)),
    )
    db.add(record)
    await db.flush()
    return record, True


async def secret_value(db: AsyncSession, secret_id: int) -> str:
    record = await db.get(ContainerAppSecret, secret_id)
    if record is None:
        raise RuntimeError("App Engine secret reference is missing.")
    return decrypt(record.encrypted_value)


async def create_credential(
    db: AsyncSession, app_id: int, label: str, username: str, password_secret_id: int,
) -> ContainerAppCredential:
    credential = ContainerAppCredential(
        app_id=app_id, label=label[:128], username=username[:255], password_secret_id=password_secret_id,
    )
    db.add(credential)
    await db.flush()
    return credential


async def reveal_credential(
    db: AsyncSession, app_id: int, credential_id: int, *, action: str = "reveal", user_id: int | None = None,
) -> tuple[ContainerAppCredential, str]:
    credential = await db.get(ContainerAppCredential, credential_id)
    if credential is None or credential.app_id != app_id:
        raise ValueError("Access credential not found.")
    credential.reveal_count += 1
    credential.last_revealed_at = datetime.utcnow()
    db.add(ContainerAppCredentialAccess(
        app_id=app_id, credential_id=credential.id, user_id=user_id,
        action=action if action in {"reveal", "copy"} else "reveal",
    ))
    return credential, await secret_value(db, credential.password_secret_id)
