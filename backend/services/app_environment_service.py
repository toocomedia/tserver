"""Protected environment-variable editing for hosted Python apps."""
import os
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.app_environment import AppEnvironmentVariable
from models.hosted_app import HostedApp

KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
RESERVED = {"HOST", "PORT"}


async def keys(db: AsyncSession, app_id: int) -> list[str]:
    rows = await db.scalars(select(AppEnvironmentVariable.key).where(
        AppEnvironmentVariable.app_id == app_id,
    ).order_by(AppEnvironmentVariable.key))
    return list(rows)


async def set_value(db: AsyncSession, app: HostedApp, key: str, value: str) -> None:
    await set_values(db, app, {key: value})


async def set_values(db: AsyncSession, app: HostedApp, values: dict[str, str]) -> None:
    normalized: dict[str, str] = {}
    for key, value in values.items():
        key = key.strip().upper()
        if not KEY_RE.fullmatch(key) or key in RESERVED or key == "DATABASE_URL":
            raise HTTPException(400, "Use a valid environment name that is not panel-managed.")
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise HTTPException(400, "Environment values cannot contain new lines.")
        if value:
            normalized[key] = value
    if not normalized:
        return
    values = _read_values(Path(app.env_path))
    values.update(normalized)
    _write_values(Path(app.env_path), values)
    existing = set(await keys(db, app.id))
    for key in normalized:
        if key not in existing:
            db.add(AppEnvironmentVariable(app_id=app.id, key=key))


async def remove(db: AsyncSession, app: HostedApp, key: str) -> None:
    if key in RESERVED or key == "DATABASE_URL":
        raise HTTPException(400, "This environment value is managed by the panel.")
    values = _read_values(Path(app.env_path))
    values.pop(key, None)
    _write_values(Path(app.env_path), values)
    row = await db.scalar(select(AppEnvironmentVariable).where(
        AppEnvironmentVariable.app_id == app.id,
        AppEnvironmentVariable.key == key,
    ))
    if row:
        await db.delete(row)


def _read_values(path: Path) -> dict[str, str]:
    if not path.exists(): return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def _write_values(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    os.chmod(path, 0o600)
