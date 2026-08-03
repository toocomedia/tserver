"""Recoverable cleanup for one container application and its deployment files."""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_database import ContainerAppDatabase
from models.domain import Domain
from models.ssl_cert import SslCert
from services import container_app_service, nginx_service


async def uninstall(db: AsyncSession, app: ContainerApp, domain: Domain) -> None:
    errors: list[str] = []
    await _step(errors, "remove app container", lambda: _remove_container(app))
    has_databases = bool(await db.scalar(select(ContainerAppDatabase.id).where(ContainerAppDatabase.app_id == app.id)))
    if not has_databases:
        await _step(errors, "remove private app network", lambda: _remove_network(app))
    await _step(errors, "restore domain site", lambda: _restore_domain_site(db, domain))
    await _step(errors, "remove build files", lambda: asyncio.to_thread(_remove_path, container_app_service._root(app.id)))
    await _step(errors, "remove environment file", lambda: asyncio.to_thread(_remove_path, Path(app.env_path)))
    if errors:
        raise HTTPException(500, "Cleanup incomplete: " + "; ".join(errors))


async def _remove_container(app: ContainerApp) -> None:
    result = await asyncio.to_thread(
        container_app_service._run, ["docker", "rm", "-f", app.container_name], timeout=45,
    )
    if result.returncode and not _missing(result.stderr):
        raise RuntimeError(result.stderr or result.stdout or "Could not remove app container.")


async def _remove_network(app: ContainerApp) -> None:
    result = await asyncio.to_thread(
        container_app_service._run,
        ["docker", "network", "rm", container_app_service.network_name(app.id)], timeout=30,
    )
    if result.returncode and not _missing(result.stderr):
        raise RuntimeError(result.stderr or result.stdout or "Could not remove app network.")


async def _restore_domain_site(db: AsyncSession, domain: Domain) -> None:
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    if cert:
        domain.nginx_config_path = await nginx_service.update_static_site_ssl(
            domain.name,
            cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem",
            f"/etc/letsencrypt/live/{domain.name}/privkey.pem",
        )
    else:
        domain.nginx_config_path = await nginx_service.create_static_site(domain.name)
    domain.project_type = "static"
    await nginx_service.reload()


async def _step(errors: list[str], label: str, operation: Callable[[], Awaitable[object]]) -> None:
    try:
        await operation()
    except Exception as exc:
        errors.append(f"{label}: {exc}")


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _missing(message: str) -> bool:
    return "no such" in message.lower() or "not found" in message.lower()
