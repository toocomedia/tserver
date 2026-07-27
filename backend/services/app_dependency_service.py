"""Runtime dependency and public-availability helpers for hosted apps."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import dependency_manager
from models.app_deployment import AppDeployment
from models.domain import Domain
from models.hosted_app import HostedApp
from models.ssl_cert import SslCert
from services import app_hosting_health_service, app_lifecycle_service
from services import hosted_app_control_service, nginx_service


def requirement_ids(app: HostedApp) -> list[str]:
    return ["postgresql"] if app.postgres_mode == "create" else []


def missing_ids(app: HostedApp) -> list[str]:
    return [item for item in requirement_ids(app) if not dependency_manager.is_healthy(item)]


def require_available(app: HostedApp) -> None:
    missing = missing_ids(app)
    if missing:
        raise HTTPException(409, f"Activate {', '.join(missing)} before running this app.")


async def dependent_apps(
    db: AsyncSession, dependency_id: str,
) -> list[tuple[HostedApp, Domain]]:
    rows = (await db.execute(
        select(HostedApp, Domain).join(Domain, Domain.id == HostedApp.domain_id)
    )).all()
    return [(app, domain) for app, domain in rows if dependency_id in requirement_ids(app)]


async def dependent_summaries(db: AsyncSession, dependency_id: str) -> list[dict[str, object]]:
    return [
        {"id": app.id, "name": domain.name, "status": app.status}
        for app, domain in await dependent_apps(db, dependency_id)
    ]


async def ensure_dependents_idle(db: AsyncSession, dependency_id: str) -> None:
    for app, _ in await dependent_apps(db, dependency_id):
        active = await db.scalar(select(AppDeployment.id).where(
            AppDeployment.app_id == app.id,
            AppDeployment.status.in_(("queued", "running")),
        ))
        if active:
            raise HTTPException(409, f"Wait for the deployment on {app.service_name} to finish.")
        app_lifecycle_service.ensure_available(app.id)


async def pause_dependents(
    db: AsyncSession, dependency_id: str,
) -> list[tuple[HostedApp, Domain]]:
    affected = [(app, domain) for app, domain in await dependent_apps(db, dependency_id) if app.status == "running"]
    await ensure_dependents_idle(db, dependency_id)
    paused: list[tuple[HostedApp, Domain]] = []
    try:
        for app, domain in affected:
            await app_lifecycle_service.run(
                app.id, lambda app=app, domain=domain: pause_app(db, app, domain, dependency_id), wait=True,
            )
            paused.append((app, domain))
        return paused
    except Exception:
        await restore_apps(db, paused)
        raise


async def pause_app(db: AsyncSession, app: HostedApp, domain: Domain, dependency_id: str) -> None:
    await _offline(db, app, domain)
    await hosted_app_control_service.control(app, "stop")
    app.status, app.paused_by_dependency = "paused", dependency_id
    app.last_error = None


async def stop_app(db: AsyncSession, app: HostedApp, domain: Domain) -> None:
    await _offline(db, app, domain)
    await hosted_app_control_service.control(app, "stop")
    app.paused_by_dependency = None


async def start_app(db: AsyncSession, app: HostedApp, domain: Domain) -> None:
    require_available(app)
    await hosted_app_control_service.control(app, "start")
    await app_hosting_health_service.wait_for_listener(app.port)
    await _live(db, app, domain)
    app.paused_by_dependency = None


async def restore_apps(db: AsyncSession, apps: list[tuple[HostedApp, Domain]]) -> None:
    for app, domain in apps:
        try:
            await start_app(db, app, domain)
        except Exception:
            app.status = "paused"


async def publish_app(db: AsyncSession, app: HostedApp, domain: Domain) -> None:
    """Restore the live proxy after a successful deployment or explicit start."""
    await _live(db, app, domain)


async def _offline(db: AsyncSession, app: HostedApp, domain: Domain) -> None:
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    cert_path = (cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem") if cert else None
    key_path = f"/etc/letsencrypt/live/{domain.name}/privkey.pem" if cert else None
    await nginx_service.set_hosted_app_offline(domain.name, cert_path=cert_path, key_path=key_path)
    await nginx_service.reload()


async def _live(db: AsyncSession, app: HostedApp, domain: Domain) -> None:
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    if cert:
        key_path = f"/etc/letsencrypt/live/{domain.name}/privkey.pem"
        await nginx_service.update_proxy_ssl(domain.name, "127.0.0.1", app.port, "http", cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem", key_path)
    else:
        await nginx_service.create_proxy(domain.name, "127.0.0.1", app.port, "http")
    await nginx_service.reload()
