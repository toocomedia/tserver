"""Generate and bind reviewed stack secrets to one deployment snapshot."""
from __future__ import annotations

import base64
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app_secret import ContainerAppSecret
from services.apps_engine import secret_vault
from services.official_stacks.schema import OfficialStackDefinition


def _secret_matches_generator(val: str, generator: str) -> bool:
    if not val:
        return False
    if generator == "base64_32":
        try:
            return len(base64.b64decode(val.encode("ascii"))) == 32
        except Exception:
            return False
    if generator == "base64_48":
        try:
            return len(base64.b64decode(val.encode("ascii"))) == 48
        except Exception:
            return False
    if generator == "base64_64":
        try:
            return len(base64.b64decode(val.encode("ascii"))) == 64
        except Exception:
            return False
    return True


async def values_for_snapshot(
    db: AsyncSession, app_id: int, stack: OfficialStackDefinition, versions_json: str | None,
) -> tuple[dict[str, str], dict[str, int]]:
    try:
        versions = {str(key): int(value) for key, value in json.loads(versions_json or "{}").items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("Stack snapshot secret versions are invalid.") from None
    values: dict[str, str] = {}
    for requirement in stack.required_secrets:
        version = versions.get(requirement.key)
        record = None
        if version:
            record = await db.scalar(select(ContainerAppSecret).where(
                ContainerAppSecret.app_id == app_id,
                ContainerAppSecret.key == requirement.key,
                ContainerAppSecret.version == version,
            ))
            if record is not None:
                val = await secret_vault.secret_value(db, record.id)
                if not _secret_matches_generator(val, requirement.generator):
                    record = None
        if record is None:
            record, _created = await secret_vault.ensure_secret(
                db, app_id, requirement.key, requirement.purpose, rotate=True, generator=requirement.generator,
            )
            versions[requirement.key] = record.version
        values[requirement.key] = await secret_vault.secret_value(db, record.id)
    return values, versions
