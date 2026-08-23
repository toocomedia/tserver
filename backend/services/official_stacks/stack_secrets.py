"""Generate and bind reviewed stack secrets to one deployment snapshot."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app_secret import ContainerAppSecret
from services.apps_engine import secret_vault
from services.official_stacks.schema import OfficialStackDefinition


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
            if record is None:
                raise RuntimeError(f"Saved stack secret '{requirement.key}' is missing.")
        else:
            record, _created = await secret_vault.ensure_secret(
                db, app_id, requirement.key, requirement.purpose, generator=requirement.generator,
            )
            versions[requirement.key] = record.version
        values[requirement.key] = await secret_vault.secret_value(db, record.id)
    return values, versions
