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


async def uninstall(db: AsyncSession, app: ContainerApp, domain: Domain, *, remove_network: bool = True) -> None:
    errors: list[str] = []
    await _step(errors, "remove app container", lambda: _remove_container(app))
    has_docker_databases = bool(await db.scalar(select(ContainerAppDatabase.id).where(
        ContainerAppDatabase.app_id == app.id, ContainerAppDatabase.provider == "docker",
    )))
    if remove_network and not has_docker_databases:
        await _step(errors, "remove private app network", lambda: _remove_network(app))
    await _step(errors, "restore domain site", lambda: _restore_domain_site(db, domain))
    await _step(errors, "remove build files", lambda: asyncio.to_thread(_remove_path, container_app_service.root(app.id)))
    env_p = Path(app.env_path) if app.env_path else container_app_service.env_path(app.id)
    await _step(errors, "remove environment file", lambda: asyncio.to_thread(_remove_path, env_p))
    from dependencies.git import repository_service
    await _step(errors, "remove deploy key", lambda: asyncio.to_thread(repository_service.delete_deploy_key, app.id))
    if errors:
        raise HTTPException(500, "Cleanup incomplete: " + "; ".join(errors))


async def delete_app(
    db: AsyncSession, app: ContainerApp, *, keep_database_ids: list[int] = [], keep_app_volume: bool = False,
) -> None:
    """Completely uninstalls and deletes a container application record."""
    from sqlalchemy import delete
    from models.container_app_deployment import ContainerAppDeployment
    from services import container_app_database_service, container_app_removal_service

    domain = await db.get(Domain, app.domain_id)
    if domain:
        await uninstall(db, app, domain, remove_network=False)
    attachments = await container_app_database_service.attachments_for(db, app.id)
    managed_ids = {item.id for item in attachments if item.provider in {"docker", "panel_postgres", "panel_mariadb"}}
    delete_database_ids = list(managed_ids - set(keep_database_ids))
    delete_app_volume = (bool(app.data_volume) or bool(app.storage_mounts)) and not keep_app_volume
    await container_app_removal_service.remove_selected_data(
        db, app, attachments,
        database_ids=delete_database_ids,
        delete_app_volume=delete_app_volume,
        delete_wordpress_files=bool(app.wordpress_content_volume),
        delete_backups=True,
    )
    await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
    await db.delete(app)
    await db.flush()


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


async def remove_private_network(app: ContainerApp) -> None:
    await _remove_network(app)


async def remove_volume(volume: str) -> None:
    result = await asyncio.to_thread(container_app_service._run, ["docker", "volume", "rm", volume], timeout=45)
    if result.returncode and not _missing(result.stderr):
        raise HTTPException(502, (result.stderr or result.stdout or "Could not remove data volume.")[-1000:])


async def list_app_storage_volumes(app_id: int) -> list[str]:
    """Return volumes owned by this Railpack app, including detached mounts."""
    result = await asyncio.to_thread(
        container_app_service._run,
        [
            "docker", "volume", "ls",
            "--filter", "label=srv-panel.plugin=railpack_apps",
            "--filter", f"label=srv-panel.app-id={app_id}",
            "--format", "{{.Name}}",
        ],
        timeout=30,
    )
    if result.returncode:
        raise HTTPException(502, (result.stderr or result.stdout or "Could not list app storage volumes.")[-1000:])
    return [name.strip() for name in result.stdout.splitlines() if name.strip()]


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
