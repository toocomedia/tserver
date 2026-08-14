"""Native PHP website ownership, lifecycle, health, database, and operation service."""
from __future__ import annotations

import asyncio
from datetime import datetime
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from dependencies import dependency_manager
from models.container_app import ContainerApp
from models.domain import Domain
from models.hosted_app import HostedApp
from models.php_website import PhpWebsite
from models.php_website_database import PhpWebsiteDatabase
from models.php_website_operation import PhpWebsiteOperation
from models.ssl_cert import SslCert
from services import php_site_filament_service as filament
from services import php_site_laravel_service as laravel
from services import php_site_runtime as runtime
from services import nginx_service, ssl_service


ACTIVE_OPERATION_STATES = ("queued", "running")
MANAGED_SITE_STATES = {"provisioning", "active", "disabled", "degraded", "failed", "archived", "deleting"}


def site_root(domain_name: str) -> Path:
    return Path(config.NGINX_WEBROOT) / domain_name


def document_root(site: PhpWebsite) -> Path:
    return Path(site.root_path).joinpath(*site.document_root.split("/"))


def log_paths(site: PhpWebsite) -> tuple[str, str]:
    root = Path(config.PHP_SITE_LOG_ROOT) / str(site.id)
    return str(root / "access.log"), str(root / "nginx-error.log")


async def get_site(db: AsyncSession, site_id: int) -> PhpWebsite:
    site = await db.get(PhpWebsite, site_id)
    if site is None:
        raise HTTPException(404, "PHP website not found.")
    return site


async def database_for(db: AsyncSession, site_id: int) -> PhpWebsiteDatabase | None:
    return await db.scalar(select(PhpWebsiteDatabase).where(PhpWebsiteDatabase.site_id == site_id))


async def active_operation(db: AsyncSession, site_id: int) -> PhpWebsiteOperation | None:
    return await db.scalar(select(PhpWebsiteOperation).where(
        PhpWebsiteOperation.site_id == site_id,
        PhpWebsiteOperation.status.in_(ACTIVE_OPERATION_STATES),
    ).order_by(PhpWebsiteOperation.id.desc()))


def selectable_versions(*, force: bool = False) -> list[dict[str, Any]]:
    status = dependency_manager.get_status("php", force=force) or {}
    return [
        item for item in status.get("versions", [])
        if item.get("installed") and item.get("managed") and item.get("healthy")
    ]


def require_version(version: str) -> dict[str, Any]:
    selected = next((item for item in selectable_versions(force=True) if item.get("version") == version), None)
    if selected is None:
        raise HTTPException(
            409,
            f"PHP {version} must be panel-managed, installed, running, and socket-healthy before a website can use it.",
        )
    return selected


def require_mariadb() -> dict[str, Any]:
    status = dependency_manager.get_status("mariadb", force=True) or {}
    if not status.get("healthy"):
        raise HTTPException(409, "Start MariaDB from Dependencies before creating a website database.")
    if status.get("install_origin") != "panel_managed":
        raise HTTPException(409, "PHP website databases require panel-managed local MariaDB.")
    return status


_ext_cache: dict[str, Any] = {}
_ext_cache_at: float = 0.0


async def options(db: AsyncSession) -> dict[str, Any]:
    global _ext_cache, _ext_cache_at
    used = set((await db.scalars(select(ContainerApp.domain_id))).all())
    used.update((await db.scalars(select(HostedApp.domain_id))).all())
    used.update((await db.scalars(select(PhpWebsite.domain_id))).all())
    domains = list((await db.scalars(select(Domain).where(
        Domain.project_type.in_(("static", "dns")),
    ).order_by(Domain.name))).all())
    certs = set((await db.scalars(select(SslCert.full_domain))).all())
    versions = await asyncio.to_thread(selectable_versions, force=False)
    now = time.monotonic()
    if not _ext_cache or (now - _ext_cache_at > 60.0):
        wp_vers: dict[str, Any] = {}
        db_exts: dict[str, Any] = {}
        for item in versions:
            version = str(item["version"])
            try:
                wp_vers[version] = await asyncio.to_thread(runtime.wordpress_extension_status, version)
            except RuntimeError as exc:
                wp_vers[version] = {"ready": False, "missing_packages": [], "error": str(exc)}
            try:
                db_exts[version] = await asyncio.to_thread(runtime.database_extension_status, version)
            except RuntimeError as exc:
                db_exts[version] = {"ready": False, "missing_packages": [], "error": str(exc)}
        _ext_cache = {
            "wordpress_versions": wp_vers,
            "database_extensions": db_exts,
            "laravel": await laravel.options(versions),
            "filament": await filament.options(),
        }
        _ext_cache_at = now

    wordpress: dict[str, Any] = {
        "wp_cli_available": os.name != "nt" and Path("/usr/local/bin/wp").is_file(),
        "versions": _ext_cache.get("wordpress_versions", {}),
    }
    mariadb = dependency_manager.get_status("mariadb", cached=True) or {}
    return {
        "domains": [
            {
                "id": item.id,
                "name": item.name,
                "project_type": item.project_type,
                "has_ssl": item.name in certs,
            }
            for item in domains if item.id not in used
        ],
        "php_versions": versions,
        "default_document_root": "public",
        "mariadb": {
            "healthy": bool(mariadb.get("healthy")),
            "panel_managed": mariadb.get("install_origin") == "panel_managed",
        },
        "wordpress": wordpress,
        "laravel": _ext_cache.get("laravel", {}),
        "filament": _ext_cache.get("filament", {}),
        "database_extensions": _ext_cache.get("database_extensions", {}),
    }


async def list_sites(db: AsyncSession) -> list[dict[str, Any]]:
    sites = list((await db.scalars(select(PhpWebsite).order_by(PhpWebsite.id.desc()))).all())
    if not sites:
        return []

    site_ids = [site.id for site in sites]
    domain_ids = list({site.domain_id for site in sites})
    domains = {
        item.id: item
        for item in (await db.scalars(select(Domain).where(Domain.id.in_(domain_ids)))).all()
    }
    databases = {
        item.site_id: item
        for item in (await db.scalars(
            select(PhpWebsiteDatabase).where(PhpWebsiteDatabase.site_id.in_(site_ids))
        )).all()
    }
    certificates = {
        (item.domain_id, item.full_domain): item
        for item in (await db.scalars(
            select(SslCert).where(SslCert.domain_id.in_(domain_ids))
        )).all()
    }
    operations: dict[int, PhpWebsiteOperation] = {}
    for item in (await db.scalars(
        select(PhpWebsiteOperation)
        .where(
            PhpWebsiteOperation.site_id.in_(site_ids),
            PhpWebsiteOperation.status.in_(ACTIVE_OPERATION_STATES),
        )
        .order_by(PhpWebsiteOperation.id.desc())
    )).all():
        operations.setdefault(item.site_id, item)

    results = []
    for site in sites:
        domain = domains.get(site.domain_id)
        cert = certificates.get((site.domain_id, domain.name if domain else ""))
        results.append(_site_payload(
            site,
            domain,
            databases.get(site.id),
            cert,
            operations.get(site.id),
        ))
    return results


async def serialize_site(
    db: AsyncSession, site: PhpWebsite, *, include_health: bool = True,
) -> dict[str, Any]:
    domain = await db.get(Domain, site.domain_id)
    database = await database_for(db, site.id)
    cert = await db.scalar(select(SslCert).where(SslCert.domain_id == site.domain_id, SslCert.full_domain == (domain.name if domain else "")))
    operation = await active_operation(db, site.id)
    result = _site_payload(site, domain, database, cert, operation)
    if include_health:
        result["health"] = await health(db, site)
    return result


def _site_payload(
    site: PhpWebsite,
    domain: Domain | None,
    database: PhpWebsiteDatabase | None,
    cert: SslCert | None,
    operation: PhpWebsiteOperation | None,
) -> dict[str, Any]:
    result = {
        "id": site.id,
        "domain_id": site.domain_id,
        "domain": domain.name if domain else None,
        "preset": site.preset,
        "php_version": site.php_version,
        "document_root": site.document_root,
        "root_path": site.root_path,
        "status": site.status,
        "last_error": site.last_error,
        "last_warning": site.last_warning,
        "ssl": {
            "active": cert is not None,
            "requested": site.ssl_requested,
            "include_www": site.ssl_include_www,
            "certificate_id": cert.id if cert else None,
            "expiry_date": cert.expiry_date.isoformat() if cert and cert.expiry_date else None,
        },
        "database": None if database is None else {
            "id": database.id, "database": database.database_name,
            "username": database.username, "host": "127.0.0.1", "port": 3306,
            "status": database.status, "last_error": database.last_error,
        },
        "wordpress": None if site.preset != "wordpress" else {
            "site_title": site.wordpress_site_title,
            "admin_user": site.wordpress_admin_user,
            "admin_email": site.wordpress_admin_email,
            "installed": site.wordpress_installed_at is not None,
            "installed_at": site.wordpress_installed_at.isoformat() if site.wordpress_installed_at else None,
        },
        "operation": None if operation is None else operation_payload(operation),
        "file_manager_target": f"php:{site.id}",
        "available_actions": _available_actions(site, operation is not None, database is not None, cert is not None),
        "created_at": site.created_at.isoformat() if site.created_at else None,
    }
    return result


def operation_payload(operation: PhpWebsiteOperation) -> dict[str, Any]:
    return {
        "id": operation.id, "site_id": operation.site_id, "action": operation.action,
        "status": operation.status, "stage": operation.stage, "message": operation.message,
        "error": operation.error,
        "started_at": operation.started_at.isoformat() if operation.started_at else None,
        "finished_at": operation.finished_at.isoformat() if operation.finished_at else None,
    }


def _available_actions(
    site: PhpWebsite, busy: bool, has_database: bool, has_certificate: bool,
) -> dict[str, bool]:
    return {
        "change_php_version": not busy and site.status in {"active", "degraded"},
        "change_document_root": not busy and site.status in {"active", "degraded"},
        "enable": not busy and site.status in {"disabled", "failed", "degraded"},
        "disable": not busy and site.status in {"active", "degraded"},
        "repair": not busy and site.status in {"failed", "degraded", "disabled"},
        "create_database": (
            not busy and not has_database and site.preset != "wordpress" and not laravel.is_laravel_preset(site.preset)
            and site.status in {"active", "degraded"}
        ),
        "rotate_database": not busy and has_database and site.status != "deleting",
        "delete_database": (
            not busy and has_database and site.status != "deleting"
            and site.preset != "wordpress" and not laravel.is_laravel_preset(site.preset)
        ),
        "issue_ssl": not busy and not has_certificate and site.status in {"active", "degraded"},
        "renew_ssl": not busy and has_certificate and site.status in {"active", "disabled", "degraded", "archived"},
        "revoke_ssl": not busy and has_certificate and site.status in {"active", "disabled", "degraded", "archived"},
        "archive": not busy and site.status in {"active", "disabled", "degraded", "failed"},
        "restore": not busy and site.status == "archived",
        "wordpress_retry": (
            not busy and site.preset == "wordpress" and site.wordpress_installed_at is None
            and site.status in {"active", "degraded", "failed"}
        ),
        "laravel_retry": (
            not busy and site.preset == laravel.PRESET and site.status in {"degraded", "failed"}
        ),
        "filament_retry": (
            not busy and site.preset == filament.PRESET and site.status in {"degraded", "failed"}
        ),
        "delete_site": not busy and site.status != "deleting",
    }


async def health(db: AsyncSession, site: PhpWebsite) -> dict[str, Any]:
    domain = await db.get(Domain, site.domain_id)
    errors: list[str] = []
    socket = Path(runtime.socket_path(site.id, site.php_version))
    socket_healthy = False
    try:
        socket_healthy = stat.S_ISSOCK(socket.stat().st_mode)
    except OSError:
        pass
    if site.status not in {"disabled", "archived"} and not socket_healthy:
        errors.append(f"PHP-FPM socket is unavailable: {socket}.")
    nginx_active = bool(domain and nginx_service.config_exists(domain.name))
    if not nginx_active:
        errors.append("Nginx site configuration is unavailable.")
    database = await database_for(db, site.id)
    mariadb_healthy = None
    if database:
        mariadb_healthy = bool((dependency_manager.get_status("mariadb", cached=True) or {}).get("healthy"))
        if not mariadb_healthy:
            errors.append("Local MariaDB is unavailable.")
    http = None
    if domain and nginx_active and site.status not in {"disabled", "archived"}:
        http = await _probe_http(domain.name)
        if not http["healthy"]:
            errors.append(http["error"] or "Website HTTP check failed.")
    return {
        "healthy": not errors and site.status == "active",
        "state": "offline" if site.status in {"disabled", "archived"} else ("healthy" if not errors else "degraded"),
        "socket_healthy": socket_healthy,
        "socket_path": str(socket),
        "nginx_active": nginx_active,
        "mariadb_healthy": mariadb_healthy,
        "http": http,
        "errors": errors,
    }


async def _probe_http(domain_name: str) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 80), timeout=2)
        writer.write(f"GET / HTTP/1.0\r\nHost: {domain_name}\r\nConnection: close\r\n\r\n".encode("ascii"))
        await writer.drain()
        first = await asyncio.wait_for(reader.readline(), timeout=3)
        writer.close()
        await writer.wait_closed()
        text = first.decode("ascii", errors="replace").strip()
        parts = text.split()
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return {"healthy": 200 <= status < 500, "status_code": status, "error": None if status else "Invalid HTTP response."}
    except (OSError, asyncio.TimeoutError) as exc:
        return {"healthy": False, "status_code": None, "error": f"Local HTTP check failed: {exc}"}


async def create_site(db: AsyncSession, body) -> tuple[PhpWebsite, PhpWebsiteOperation]:
    await asyncio.to_thread(require_version, body.php_version)
    domain = await db.get(Domain, body.domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found.")
    if domain.project_type not in {"static", "dns"}:
        raise HTTPException(409, "This domain is already used by another hosting feature.")
    if nginx_service.server_name_in_use(
        domain.name, ignore_names={domain.name, f"{domain.name}.conf"},
    ):
        raise HTTPException(409, "Another Nginx configuration already owns this domain or its www name.")
    if await db.scalar(select(PhpWebsite.id).where(PhpWebsite.domain_id == domain.id)):
        raise HTTPException(409, "This domain already has a PHP website.")
    if await db.scalar(select(ContainerApp.id).where(ContainerApp.domain_id == domain.id)) or await db.scalar(
        select(HostedApp.id).where(HostedApp.domain_id == domain.id)
    ):
        raise HTTPException(409, "This domain is already used by another application.")
    create_database = bool(body.create_database or laravel.requires_database(body.preset))
    if create_database:
        await asyncio.to_thread(require_mariadb)
    from services.resource_guard_service import resource_guard_service
    profile = laravel.install_profile(body.preset)
    preflight = await resource_guard_service.preflight(db, profile)
    if not preflight["ok"]:
        raise HTTPException(409, f"Resource Guard blocked PHP website creation: {preflight['reason']}")
    root = site_root(domain.name)
    site = PhpWebsite(
        domain_id=domain.id, preset=body.preset, previous_project_type=domain.project_type,
        php_version=body.php_version, document_root=body.document_root,
        root_path=str(root), linux_user="pending", status="provisioning",
        ssl_requested=body.ssl, ssl_include_www=body.include_www,
        wordpress_site_title=body.wordpress.site_title if body.wordpress else None,
        wordpress_admin_user=body.wordpress.admin_user if body.wordpress else None,
        wordpress_admin_email=body.wordpress.admin_email if body.wordpress else None,
    )
    db.add(site)
    await db.flush()
    site.linux_user = f"srvphp{site.id}"[:31]
    domain.project_type = "php"
    operation = PhpWebsiteOperation(site_id=site.id, action="create")
    db.add(operation)
    await db.flush()
    payload = {
        "create_database": create_database,
        "ssl": body.ssl,
        "include_www": body.include_www,
        "install_missing_extensions": body.install_missing_extensions,
        "wordpress": body.wordpress.model_dump() if body.wordpress else None,
        "filament": body.filament.model_dump() if body.filament else None,
    }
    asyncio.create_task(_run_after_commit(operation.id, "create", payload))
    return site, operation


async def queue_action(
    db: AsyncSession, site: PhpWebsite, action: str, payload: dict[str, Any] | None = None,
) -> PhpWebsiteOperation:
    if await active_operation(db, site.id):
        raise HTTPException(409, "Another PHP website operation is already running.")
    allowed_states = {
        "runtime": {"active", "degraded"},
        "document_root": {"active", "degraded"},
        "enable": {"disabled", "failed", "degraded"},
        "disable": {"active", "degraded"},
        "repair": {"disabled", "failed", "degraded"},
        "archive": {"active", "disabled", "degraded", "failed"},
        "restore": {"archived"},
        "ssl_issue": {"active", "degraded"},
        "ssl_renew": {"active", "disabled", "degraded", "archived"},
        "ssl_revoke": {"active", "disabled", "degraded", "archived"},
        "wordpress_retry": {"active", "degraded", "failed"},
        "laravel_retry": {"degraded", "failed"},
        "filament_retry": {"degraded", "failed"},
    }
    if action not in allowed_states or site.status not in allowed_states[action]:
        raise HTTPException(409, f"Action {action} is not available while the website is {site.status}.")
    if action == "wordpress_retry" and site.preset != "wordpress":
        raise HTTPException(409, "This is not a WordPress website.")
    if action == "laravel_retry" and site.preset != laravel.PRESET:
        raise HTTPException(409, "This is not a Laravel website.")
    if action == "filament_retry" and site.preset != filament.PRESET:
        raise HTTPException(409, "This is not a Filament website.")
    operation = PhpWebsiteOperation(site_id=site.id, action=action)
    db.add(operation)
    await db.flush()
    asyncio.create_task(_run_after_commit(operation.id, action, payload or {}))
    return operation


async def _run_after_commit(operation_id: int, action: str, payload: dict[str, Any]) -> None:
    from database import AsyncSessionLocal

    await asyncio.sleep(0.3)
    async with AsyncSessionLocal() as db:
        guard_token = None
        operation = await db.get(PhpWebsiteOperation, operation_id)
        site = await db.get(PhpWebsite, operation.site_id) if operation else None
        domain = await db.get(Domain, site.domain_id) if site else None
        if operation is None or site is None or domain is None or operation.status != "queued":
            return
        operation.status, operation.started_at = "running", datetime.utcnow()
        await db.commit()
        from services.resource_guard_service import resource_guard_service
        task = asyncio.current_task()
        framework_install = (
            action == "create" and (site.preset == "wordpress" or laravel.is_laravel_preset(site.preset))
        ) or action in {"wordpress_retry", "laravel_retry", "filament_retry"}
        profile = laravel.install_profile(site.preset) if framework_install else "native_light"
        guard_token = resource_guard_service.register(
            "php_site", str(site.id), "normal", f"PHP website: {domain.name}",
            (lambda: task.cancel()) if task else None, profile=profile,
        )
        try:
            if action == "create":
                await _execute_create(db, operation, site, domain, payload)
            elif action == "runtime":
                await _execute_runtime(db, operation, site, domain, str(payload["php_version"]))
            elif action == "document_root":
                await _execute_document_root(db, operation, site, domain, str(payload["document_root"]))
            elif action in {"enable", "restore", "repair"}:
                await _execute_enable(db, operation, site, domain, action)
            elif action in {"disable", "archive"}:
                await _execute_disable(db, operation, site, domain, action)
            elif action == "ssl_issue":
                await _execute_ssl_issue(db, operation, site, domain, bool(payload.get("include_www")))
            elif action == "ssl_renew":
                await _execute_ssl_renew(db, operation, site, domain)
            elif action == "ssl_revoke":
                await _execute_ssl_revoke(db, operation, site, domain)
            elif action == "wordpress_retry":
                await _execute_wordpress_retry(
                    db, operation, site, domain, str(payload["admin_password"]),
                    bool(payload.get("install_missing_extensions")),
                )
            elif action == "laravel_retry":
                await _execute_laravel_retry(
                    db, operation, site, domain, bool(payload.get("install_missing_extensions")),
                )
            elif action == "filament_retry":
                await _execute_filament_retry(
                    db, operation, site, domain, dict(payload["filament"]),
                    bool(payload.get("install_missing_extensions")),
                )
            else:
                raise RuntimeError("Unsupported PHP website operation.")
            operation.status, operation.stage = "succeeded", "complete"
            operation.message, operation.error = "Operation complete.", None
        except asyncio.CancelledError:
            operation.status, operation.stage = "failed", "cancelled"
            operation.error = "The PHP website operation was cancelled before completion. Retry the action."
            operation.message = "Operation stopped."
            operation.finished_at = datetime.utcnow()
            if action == "create" and site.status == "active":
                site.status = "degraded"
            site.last_error = operation.error
            await db.commit()
            if guard_token is not None:
                resource_guard_service.unregister(guard_token)
                guard_token = None
            raise
        except Exception as exc:
            operation.status, operation.stage = "failed", "failed"
            operation.error = str(getattr(exc, "detail", exc))[:2000]
            operation.message = "Operation failed."
            if action == "create" and site.status == "active":
                site.status = "degraded"
            elif site.status not in {"active", "disabled", "archived"}:
                site.status = "failed"
            site.last_error = operation.error[:1000]
        operation.finished_at = datetime.utcnow()
        await db.commit()
        if guard_token is not None:
            resource_guard_service.unregister(guard_token)


async def _stage(db: AsyncSession, operation: PhpWebsiteOperation, stage: str, message: str) -> None:
    operation.stage, operation.message = stage, message
    await db.commit()


async def _execute_create(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain, payload: dict[str, Any],
) -> None:
    database = None
    if site.preset == "wordpress":
        await _stage(db, operation, "extensions", "Checking WordPress PHP extensions.")
        await ensure_wordpress_extensions(
            site.php_version, install=bool(payload.get("install_missing_extensions")),
        )
    elif laravel.is_laravel_preset(site.preset):
        await _stage(db, operation, "extensions", "Checking Laravel PHP extensions and Composer.")
        await laravel.ensure_requirements(
            site.php_version, install=bool(payload.get("install_missing_extensions")),
        )
        if site.preset == filament.PRESET:
            await filament.ensure_requirements()
    elif payload.get("create_database"):
        await _stage(db, operation, "extensions", "Checking PHP MariaDB extension.")
        await ensure_database_extension(
            site.php_version, install=bool(payload.get("install_missing_extensions")),
        )
    await _stage(db, operation, "runtime", "Creating isolated PHP-FPM pool and website root.")
    await asyncio.to_thread(runtime.provision, site, domain.name, database=None)
    if payload.get("create_database"):
        await _stage(db, operation, "database", "Creating local MariaDB database.")
        database = await create_database(db, site, during_provision=True)
    credentials = read_credentials(database) if database else None
    if credentials:
        await _stage(db, operation, "runtime", "Adding database credentials to the PHP-FPM pool.")
        await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
    await _stage(db, operation, "routing", "Publishing PHP website through Nginx.")
    await publish(db, site, domain)
    site.status, site.last_error = "active", None
    await db.commit()
    if site.preset == "wordpress":
        if credentials is None or not payload.get("wordpress"):
            raise RuntimeError("WordPress database or administrator details are missing.")
        await _stage(db, operation, "wordpress", "Installing WordPress.")
        cert = await _certificate(db, domain)
        await asyncio.to_thread(
            runtime.install_wordpress, site, domain.name, credentials, payload["wordpress"], https=cert is not None,
        )
        site.wordpress_installed_at = datetime.utcnow()
        await db.commit()
    elif site.preset == laravel.PRESET:
        if credentials is None:
            raise RuntimeError("Laravel database credentials are missing.")
        await _stage(db, operation, "laravel", "Installing Laravel.")
        cert = await _certificate(db, domain)
        await laravel.install(site, domain.name, credentials, https=cert is not None)
    elif site.preset == filament.PRESET:
        if credentials is None or not payload.get("filament"):
            raise RuntimeError("Filament database or administrator details are missing.")
        await _stage(db, operation, "laravel", "Installing Laravel.")
        cert = await _certificate(db, domain)
        await laravel.install(site, domain.name, credentials, https=cert is not None)
        await _stage(db, operation, "filament", "Installing Filament admin panel.")
        await filament.install(site, domain.name, dict(payload["filament"]))
    if payload.get("ssl"):
        await _stage(db, operation, "ssl", "Requesting SSL certificate.")
        try:
            await ssl_service.issue_cert(db, domain.id, domain.name, bool(payload.get("include_www")))
            if site.preset == "wordpress":
                await asyncio.to_thread(runtime.update_wordpress_url, site, domain.name, https=True)
            elif laravel.is_laravel_preset(site.preset):
                await laravel.update_url(site, domain.name, https=True)
        except Exception as exc:
            site.last_warning = f"Website is active over HTTP, but SSL failed: {getattr(exc, 'detail', exc)}"[:1000]
    await _stage(db, operation, "health", "Verifying website health.")
    check = await health(db, site)
    if not check["healthy"]:
        site.status = "degraded"
        site.last_warning = "; ".join(check["errors"])[:1000]


async def _execute_runtime(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain, new_version: str,
) -> None:
    await asyncio.to_thread(require_version, new_version)
    if new_version == site.php_version:
        return
    database = await database_for(db, site.id)
    credentials = read_credentials(database) if database else None
    old_version = site.php_version
    await _stage(db, operation, "prepare_runtime", f"Preparing PHP {new_version} pool.")
    await asyncio.to_thread(runtime.prepare_version, site, domain.name, new_version, database=credentials)
    site.php_version = new_version
    try:
        await _stage(db, operation, "routing", f"Switching Nginx to PHP {new_version}.")
        await publish(db, site, domain)
    except Exception:
        site.php_version = old_version
        await asyncio.to_thread(runtime.finalize_version, site, new_version)
        raise
    await _stage(db, operation, "cleanup", f"Removing PHP {old_version} site pool.")
    try:
        await asyncio.to_thread(runtime.finalize_version, site, old_version)
        site.last_warning = None
    except RuntimeError as exc:
        site.last_warning = f"PHP switched, but old pool cleanup failed: {exc}"[:1000]
    site.status, site.last_error = "active", None


async def _execute_document_root(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain, new_root: str,
) -> None:
    if new_root == site.document_root:
        return
    old_root = site.document_root
    site.document_root = new_root
    database = await database_for(db, site.id)
    credentials = read_credentials(database) if database else None
    try:
        await _stage(db, operation, "filesystem", "Preparing new document root.")
        await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
        await _stage(db, operation, "routing", "Publishing new document root.")
        await publish(db, site, domain)
    except Exception:
        site.document_root = old_root
        await publish(db, site, domain)
        raise
    site.status, site.last_error = "active", None


async def _execute_enable(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain, action: str,
) -> None:
    was_offline = site.status in {"disabled", "archived", "failed"}
    await asyncio.to_thread(require_version, site.php_version)
    database = await database_for(db, site.id)
    credentials = read_credentials(database) if database else None
    await _stage(db, operation, "runtime", "Restoring PHP-FPM pool.")
    await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
    await _stage(db, operation, "routing", "Publishing PHP website.")
    try:
        await publish(db, site, domain)
    except Exception:
        if was_offline:
            try:
                await asyncio.to_thread(runtime.set_enabled, site, domain.name, False)
            except RuntimeError:
                pass
        raise
    site.status, site.last_error = "active", None


async def _execute_disable(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain, action: str,
) -> None:
    cert = await _certificate(db, domain)
    await _stage(db, operation, "routing", "Publishing intentional offline response.")
    domain.nginx_config_path = await nginx_service.set_php_site_offline(
        domain.name,
        cert_path=(cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem") if cert else None,
        key_path=f"/etc/letsencrypt/live/{domain.name}/privkey.pem" if cert else None,
        include_www=site.ssl_include_www,
    )
    await nginx_service.reload()
    site.status = "archived" if action == "archive" else "disabled"
    site.last_error = None
    await db.commit()
    await _stage(db, operation, "runtime", "Stopping website PHP-FPM pool.")
    await asyncio.to_thread(runtime.set_enabled, site, domain.name, False)


async def publish(db: AsyncSession, site: PhpWebsite, domain: Domain) -> None:
    cert = await _certificate(db, domain)
    access_log, error_log = log_paths(site)
    values = (
        domain.name, str(document_root(site)), runtime.socket_path(site.id, site.php_version),
        access_log, error_log,
    )
    if cert:
        domain.nginx_config_path = await nginx_service.update_php_site_ssl(
            *values,
            cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem",
            f"/etc/letsencrypt/live/{domain.name}/privkey.pem",
            include_www=site.ssl_include_www,
        )
    else:
        domain.nginx_config_path = await nginx_service.create_php_site(
            *values, include_www=site.ssl_include_www,
        )
    await nginx_service.reload()
    domain.nginx_active = True
    domain.project_type = "php"
    domain.webroot_path = str(document_root(site))


async def _certificate(db: AsyncSession, domain: Domain) -> SslCert | None:
    return await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))


async def _execute_ssl_issue(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain, include_www: bool,
) -> None:
    await _stage(db, operation, "ssl", "Issuing SSL certificate.")
    site.ssl_include_www = include_www
    await ssl_service.issue_cert(db, domain.id, domain.name, include_www)
    if site.preset == "wordpress" and site.wordpress_installed_at:
        await asyncio.to_thread(runtime.update_wordpress_url, site, domain.name, https=True)
    elif laravel.is_laravel_preset(site.preset):
        await laravel.update_url(site, domain.name, https=True)


async def _execute_ssl_renew(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain,
) -> None:
    cert = await _certificate(db, domain)
    if cert is None:
        raise HTTPException(404, "SSL certificate not found.")
    await _stage(db, operation, "ssl", "Renewing SSL certificate.")
    await ssl_service.renew_cert(db, cert.id)


async def _execute_ssl_revoke(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain,
) -> None:
    cert = await _certificate(db, domain)
    if cert is None:
        raise HTTPException(404, "SSL certificate not found.")
    await _stage(db, operation, "ssl", "Revoking SSL certificate.")
    await ssl_service.revoke_cert(db, cert.id)
    if site.preset == "wordpress" and site.wordpress_installed_at:
        await asyncio.to_thread(runtime.update_wordpress_url, site, domain.name, https=False)
    elif laravel.is_laravel_preset(site.preset):
        await laravel.update_url(site, domain.name, https=False)


async def _execute_wordpress_retry(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain,
    password: str, install_missing_extensions: bool,
) -> None:
    if site.preset != "wordpress":
        raise HTTPException(409, "This is not a WordPress website.")
    await _stage(db, operation, "extensions", "Checking WordPress PHP extensions.")
    await ensure_wordpress_extensions(site.php_version, install=install_missing_extensions)
    database = await database_for(db, site.id)
    if database is None:
        await _stage(db, operation, "database", "Creating missing WordPress database.")
        database = await create_database(db, site, during_provision=True)
    await _stage(db, operation, "wordpress", "Retrying WordPress installation.")
    credentials = read_credentials(database)
    await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
    await publish(db, site, domain)
    cert = await _certificate(db, domain)
    await asyncio.to_thread(runtime.install_wordpress, site, domain.name, credentials, {
        "site_title": site.wordpress_site_title or domain.name,
        "admin_user": site.wordpress_admin_user or "admin",
        "admin_email": site.wordpress_admin_email or f"admin@{domain.name}",
        "admin_password": password,
    }, https=cert is not None)
    site.wordpress_installed_at = datetime.utcnow()
    site.status, site.last_error = "active", None


async def _execute_laravel_retry(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain,
    install_missing_extensions: bool,
) -> None:
    if site.preset != laravel.PRESET:
        raise HTTPException(409, "This is not a Laravel website.")
    await _stage(db, operation, "extensions", "Checking Laravel PHP extensions and Composer.")
    await laravel.ensure_requirements(site.php_version, install=install_missing_extensions)
    database = await database_for(db, site.id)
    if database is None:
        await _stage(db, operation, "database", "Creating missing Laravel database.")
        database = await create_database(db, site, during_provision=True)
    credentials = read_credentials(database)
    await _stage(db, operation, "runtime", "Restoring PHP-FPM pool and website root.")
    await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
    await _stage(db, operation, "routing", "Publishing PHP website through Nginx.")
    await publish(db, site, domain)
    await _stage(db, operation, "laravel", "Retrying Laravel installation.")
    cert = await _certificate(db, domain)
    await laravel.install(site, domain.name, credentials, https=cert is not None)
    site.status, site.last_error = "active", None


async def _execute_filament_retry(
    db: AsyncSession, operation: PhpWebsiteOperation, site: PhpWebsite, domain: Domain,
    values: dict[str, str], install_missing_extensions: bool,
) -> None:
    if site.preset != filament.PRESET:
        raise HTTPException(409, "This is not a Filament website.")
    await _stage(db, operation, "extensions", "Checking Laravel PHP extensions and Composer.")
    await laravel.ensure_requirements(site.php_version, install=install_missing_extensions)
    await filament.ensure_requirements()
    database = await database_for(db, site.id)
    if database is None:
        await _stage(db, operation, "database", "Creating missing Filament database.")
        database = await create_database(db, site, during_provision=True)
    credentials = read_credentials(database)
    await _stage(db, operation, "runtime", "Restoring PHP-FPM pool and website root.")
    await asyncio.to_thread(runtime.provision, site, domain.name, database=credentials)
    await _stage(db, operation, "routing", "Publishing PHP website through Nginx.")
    await publish(db, site, domain)
    await _stage(db, operation, "laravel", "Retrying Laravel installation.")
    cert = await _certificate(db, domain)
    await laravel.install(site, domain.name, credentials, https=cert is not None)
    await _stage(db, operation, "filament", "Retrying Filament admin panel setup.")
    await filament.install(site, domain.name, values)
    site.status, site.last_error = "active", None


async def ensure_wordpress_extensions(version: str, *, install: bool) -> dict[str, Any]:
    try:
        status = await asyncio.to_thread(runtime.wordpress_extension_status, version)
        if status.get("ready"):
            return status
        missing = ", ".join(status.get("missing_packages") or []) or "required WordPress PHP extensions"
        if not install:
            raise HTTPException(409, f"Missing {missing}. Retry with install_missing_extensions enabled.")
        await asyncio.to_thread(runtime.install_wordpress_extensions, version)
        status = await asyncio.to_thread(runtime.wordpress_extension_status, version)
        if not status.get("ready"):
            raise HTTPException(502, "WordPress PHP extensions remain unavailable after installation.")
        return status
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


async def ensure_database_extension(version: str, *, install: bool) -> dict[str, Any]:
    try:
        status = await asyncio.to_thread(runtime.database_extension_status, version)
        if status.get("ready"):
            return status
        missing = ", ".join(status.get("missing_packages") or []) or f"PHP {version} MariaDB extension"
        if not install:
            raise HTTPException(409, f"Missing {missing}. Allow its explicit installation and retry.")
        await asyncio.to_thread(runtime.install_database_extension, version)
        status = await asyncio.to_thread(runtime.database_extension_status, version)
        if not status.get("ready"):
            raise HTTPException(502, "The PHP MariaDB extension remains unavailable after installation.")
        return status
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


async def create_database(
    db: AsyncSession, site: PhpWebsite, *, during_provision: bool = False,
) -> PhpWebsiteDatabase:
    if not during_provision:
        if site.preset == "wordpress" or laravel.is_laravel_preset(site.preset):
            raise HTTPException(409, "Framework database ownership is managed by its website setup.")
        if site.status not in {"active", "degraded"}:
            raise HTTPException(409, "Enable the PHP website before attaching a database.")
        if await active_operation(db, site.id):
            raise HTTPException(409, "Another PHP website operation is already running.")
    if await database_for(db, site.id):
        raise HTTPException(409, "This PHP website already has a database.")
    await asyncio.to_thread(require_mariadb)
    from plugins.mariadb_manager.service import mariadb_manager_service
    database_name = f"phpsite_{site.id}"[:63]
    username = f"ps{site.id}"[:31]
    try:
        result = await asyncio.to_thread(mariadb_manager_service.create_local_database, database_name, username)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    try:
        item = PhpWebsiteDatabase(
            site_id=site.id, database_name=result["database"], username=result["user"],
            credentials_path=str(runtime.credentials_path(site.id)),
        )
        db.add(item)
        await db.flush()
        write_credentials(item, result["password"])
    except Exception:
        try:
            await asyncio.to_thread(mariadb_manager_service.drop_database, result["database"])
            await asyncio.to_thread(mariadb_manager_service.drop_user, result["user"])
        except RuntimeError:
            pass
        raise
    return item


def write_credentials(item: PhpWebsiteDatabase, password: str) -> None:
    if not password or "\n" in password or "\r" in password:
        raise HTTPException(500, "Invalid generated database password.")
    path = Path(item.credentials_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    temporary.write_text(
        f"DATABASE={item.database_name}\nUSERNAME={item.username}\nPASSWORD={password}\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_credentials(item: PhpWebsiteDatabase | None) -> dict[str, str]:
    if item is None:
        raise HTTPException(404, "PHP website database not found.")
    path = Path(item.credentials_path)
    if not path.is_file() or path.is_symlink():
        raise HTTPException(409, "Database credentials are unavailable. Rotate credentials to recover.")
    values = dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    if not all(values.get(key) for key in ("DATABASE", "USERNAME", "PASSWORD")):
        raise HTTPException(409, "Database credentials file is invalid.")
    return {"database": values["DATABASE"], "username": values["USERNAME"], "password": values["PASSWORD"]}


async def rotate_database(db: AsyncSession, site: PhpWebsite) -> dict[str, str]:
    item = await database_for(db, site.id)
    if item is None:
        raise HTTPException(404, "PHP website database not found.")
    if await active_operation(db, site.id):
        raise HTTPException(409, "Another PHP website operation is already running.")
    from plugins.mariadb_manager.service import mariadb_manager_service
    try:
        old = read_credentials(item)
    except HTTPException:
        old = None
    domain = await db.get(Domain, site.domain_id)
    if domain is None:
        raise HTTPException(409, "PHP website domain is missing.")
    try:
        password = await asyncio.to_thread(mariadb_manager_service.reset_local_password, item.username)
        write_credentials(item, password)
        current = read_credentials(item)
        if site.status not in {"disabled", "archived"}:
            await asyncio.to_thread(runtime.provision, site, domain.name, database=current)
        if site.preset == "wordpress" and site.wordpress_installed_at:
            await asyncio.to_thread(runtime.update_wordpress_database_password, site, domain.name, current)
        elif laravel.is_laravel_preset(site.preset):
            await laravel.update_database_password(site, domain.name, current)
        item.status, item.last_error = "ready", None
        return current
    except Exception as exc:
        if old is None:
            item.status = "error"
            item.last_error = "Credentials rotated, but the website configuration refresh failed. Retry Repair."
            await db.commit()
        else:
            try:
                await asyncio.to_thread(
                    mariadb_manager_service.set_local_password, item.username, old["password"],
                )
                write_credentials(item, old["password"])
                if site.status not in {"disabled", "archived"}:
                    await asyncio.to_thread(runtime.provision, site, domain.name, database=old)
                if site.preset == "wordpress" and site.wordpress_installed_at:
                    await asyncio.to_thread(
                        runtime.update_wordpress_database_password, site, domain.name, old,
                    )
                elif laravel.is_laravel_preset(site.preset):
                    await laravel.update_database_password(site, domain.name, old)
            except Exception:
                item.status, item.last_error = "error", "Database password rotation rollback failed. Repair credentials manually."
            await db.commit()
        raise HTTPException(502, str(getattr(exc, "detail", exc))) from exc


async def remove_database(db: AsyncSession, site: PhpWebsite, confirmation: str) -> str | None:
    item = await database_for(db, site.id)
    if item is None:
        raise HTTPException(404, "PHP website database not found.")
    if site.preset == "wordpress" or laravel.is_laravel_preset(site.preset):
        raise HTTPException(409, "A framework database can be removed only with the complete website.")
    if await active_operation(db, site.id):
        raise HTTPException(409, "Another PHP website operation is already running.")
    if confirmation != f"DELETE DATABASE {item.database_name}":
        raise HTTPException(409, f"Type DELETE DATABASE {item.database_name} to confirm.")
    from plugins.mariadb_manager.service import mariadb_manager_service
    try:
        await asyncio.to_thread(mariadb_manager_service.drop_database, item.database_name)
        await asyncio.to_thread(mariadb_manager_service.drop_user, item.username)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    Path(item.credentials_path).unlink(missing_ok=True)
    await db.delete(item)
    await db.commit()
    if site.status in {"active", "degraded"}:
        domain = await db.get(Domain, site.domain_id)
        if domain:
            try:
                await asyncio.to_thread(runtime.provision, site, domain.name, database=None)
            except RuntimeError as exc:
                site.last_warning = f"Database removed, but PHP-FPM environment refresh failed: {exc}"[:1000]
                await db.commit()
                return site.last_warning
    return None


async def delete_site(
    db: AsyncSession, site: PhpWebsite, confirmation: str, *, delete_database_data: bool,
) -> Domain:
    domain = await db.get(Domain, site.domain_id)
    if domain is None:
        raise HTTPException(409, "PHP website domain is missing.")
    if confirmation != f"DELETE {domain.name}":
        raise HTTPException(409, f"Type DELETE {domain.name} to confirm permanent removal.")
    if await active_operation(db, site.id):
        raise HTTPException(409, "Another PHP website operation is already running.")
    database = await database_for(db, site.id)
    site.status = "deleting"
    await db.commit()
    try:
        await asyncio.to_thread(runtime.purge, site, domain.name)
        if database and delete_database_data:
            from plugins.mariadb_manager.service import mariadb_manager_service
            await asyncio.to_thread(mariadb_manager_service.drop_database, database.database_name)
            await asyncio.to_thread(mariadb_manager_service.drop_user, database.username)
        if database:
            Path(database.credentials_path).unlink(missing_ok=True)
            await db.delete(database)
        domain.project_type = "static"
        domain.webroot_path = nginx_service.create_webroot(domain.name)
        cert = await _certificate(db, domain)
        if cert:
            domain.nginx_config_path = await nginx_service.update_static_site_ssl(
                domain.name,
                cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem",
                f"/etc/letsencrypt/live/{domain.name}/privkey.pem",
            )
        else:
            domain.nginx_config_path = await nginx_service.create_static_site(domain.name)
        domain.nginx_active = True
        await nginx_service.reload()
        await db.execute(delete(PhpWebsiteOperation).where(PhpWebsiteOperation.site_id == site.id))
        await db.delete(site)
        return domain
    except Exception as exc:
        site.status, site.last_error = "failed", str(getattr(exc, "detail", exc))[:1000]
        await db.commit()
        raise HTTPException(500, f"PHP website removal incomplete: {getattr(exc, 'detail', exc)}") from exc


async def recover_interrupted() -> None:
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        operations = list((await db.scalars(select(PhpWebsiteOperation).where(
            PhpWebsiteOperation.status.in_(ACTIVE_OPERATION_STATES),
        ))).all())
        now = datetime.utcnow()
        for operation in operations:
            operation.status, operation.stage = "failed", "interrupted"
            operation.error = "Panel restarted before this PHP website operation completed. Use Repair or Retry."
            operation.message, operation.finished_at = "Operation interrupted.", now
            site = await db.get(PhpWebsite, operation.site_id)
            if site and site.status in {"provisioning", "deleting"}:
                site.status, site.last_error = "failed", operation.error
        await db.commit()
