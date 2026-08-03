"""Start, stop and publish container applications without public Docker ports."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.domain import Domain
from models.ssl_cert import SslCert
from services import container_app_deployment_service, container_app_service, nginx_service


async def control(db: AsyncSession, app: ContainerApp, domain: Domain, action: str) -> None:
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Invalid container app action.")
    if action == "stop":
        await _offline(db, domain)
        await _docker(["docker", "stop", "--time", "20", app.container_name])
        app.status, app.last_error = "stopped", None
        return
    await _docker(["docker", action, app.container_name])
    await container_app_deployment_service.wait_for_http(app.host_port)
    await publish(db, app, domain)
    app.status, app.last_error = "running", None


async def publish(db: AsyncSession, app: ContainerApp, domain: Domain) -> None:
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    if cert:
        domain.nginx_config_path = await nginx_service.update_proxy_ssl(
            domain.name, "127.0.0.1", app.host_port, "http",
            cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem",
            f"/etc/letsencrypt/live/{domain.name}/privkey.pem",
        )
    else:
        domain.nginx_config_path = await nginx_service.create_proxy(
            domain.name, "127.0.0.1", app.host_port, "http",
        )
    await nginx_service.reload()


async def _offline(db: AsyncSession, domain: Domain) -> None:
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    cert_path = (cert.cert_path or f"/etc/letsencrypt/live/{domain.name}/fullchain.pem") if cert else None
    key_path = f"/etc/letsencrypt/live/{domain.name}/privkey.pem" if cert else None
    domain.nginx_config_path = await nginx_service.set_hosted_app_offline(
        domain.name, cert_path=cert_path, key_path=key_path,
    )
    await nginx_service.reload()


async def _docker(command: list[str]) -> None:
    result = await asyncio.to_thread(container_app_service._run, command, timeout=45)
    if result.returncode:
        raise HTTPException(502, (result.stderr or result.stdout or "Docker command failed.")[-1000:])
