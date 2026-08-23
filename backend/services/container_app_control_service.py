"""Start, stop and publish container applications without public Docker ports."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.domain import Domain
from models.ssl_cert import SslCert
from services import container_app_deployment_progress_service, container_app_service, nginx_service


async def control(db: AsyncSession, app: ContainerApp, domain: Domain, action: str) -> None:
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Invalid container app action.")
    is_stack = getattr(app, "deploy_type", None) == "official_stack"
    if is_stack:
        from services.official_stacks.catalog import get_stack
        from services.official_stacks import stack_runtime_service
        stack_id = getattr(app, "stack_catalog_id", None) or "plausible_ce"
        stack = get_stack(stack_id)
        if stack is None:
            raise HTTPException(404, f"Official stack '{stack_id}' was not found in catalog.")
        if action == "stop":
            await _offline(db, domain)
            await asyncio.to_thread(stack_runtime_service.stop_stack, app.id, stack)
            app.status, app.last_error = "stopped", None
            return
        # Start or Restart
        for svc_name in stack.startup_order:
            svc = stack.services[svc_name]
            cname = stack_runtime_service.stack_container_name(app.id, svc_name)
            await _docker(["docker", action, cname])
        await container_app_deployment_progress_service.wait_for_http(
            app.host_port, path=stack.web_health_path, timeout_seconds=stack.startup_timeout_seconds,
        )
        await publish(db, app, domain)
        app.status, app.last_error = "running", None
        return

    if action == "stop":
        await _offline(db, domain)
        await _docker(["docker", "stop", "--time", "20", app.container_name])
        app.status, app.last_error = "stopped", None
        return
    try:
        await _docker(["docker", action, app.container_name])
        await container_app_deployment_progress_service.wait_for_http(app.host_port)
    except RuntimeError as exc:
        raise HTTPException(
            409, container_app_deployment_progress_service.runtime_error_summary(app)
        ) from exc
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
