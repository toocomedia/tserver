"""Idempotent removal of one hosted Python application's owned resources."""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import HTTPException

from models.hosted_app import HostedApp
from plugins.postgres_manager import queries as pg
from services import app_runtime_service, nginx_service
from utils import shell


async def uninstall(app: HostedApp, domain_name: str | None, *, delete_database: bool) -> None:
    errors: list[str] = []
    await _step(errors, "stop service", lambda: app_runtime_service.stop(app, allow_missing=True))
    await _step(errors, "disable service", lambda: app_runtime_service.systemctl("disable", app.service_name, allow_missing=True))
    await _step(errors, "remove service unit", lambda: shell.remove_path(app_runtime_service.service_unit(app)))
    await _step(errors, "reload systemd", lambda: app_runtime_service.systemctl("daemon-reload"))
    if delete_database:
        await _step(errors, "delete managed PostgreSQL data", lambda: _drop_database(app))
    if domain_name:
        await _step(errors, "remove Nginx proxy", lambda: nginx_service.remove_site(domain_name))
        await _step(errors, "reload Nginx", nginx_service.reload)
    await _step(errors, "remove app files", lambda: asyncio.to_thread(_remove_tree, app.work_dir))
    await _step(errors, "remove environment file", lambda: asyncio.to_thread(_remove_file, app.env_path))
    _verify(app, domain_name, errors)
    if errors:
        raise HTTPException(500, "Cleanup incomplete: " + "; ".join(errors))


async def _step(errors: list[str], label: str, operation: Callable[[], Awaitable[object]]) -> None:
    try:
        await operation()
    except Exception as exc:
        errors.append(f"{label}: {exc}")


async def _drop_database(app: HostedApp) -> None:
    if app.postgres_mode != "create" or not app.database_name or not app.database_user:
        return
    await asyncio.to_thread(pg.drop_app_database_and_user, app.database_name, app.database_user)


def _remove_tree(path: str) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(target)


def _remove_file(path: str) -> None:
    Path(path).unlink(missing_ok=True)


def _verify(app: HostedApp, domain_name: str | None, errors: list[str]) -> None:
    checks = {
        "service unit still exists": app_runtime_service.service_unit(app).exists(),
        "app files still exist": Path(app.work_dir).exists(),
        "environment file still exists": Path(app.env_path).exists(),
        "Nginx proxy still exists": bool(domain_name and nginx_service.config_exists(domain_name)),
    }
    errors.extend(label for label, failed in checks.items() if failed)
