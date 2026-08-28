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
from services.apps_engine.runtime_dispatch import is_compose_app


async def control(db: AsyncSession, app: ContainerApp, domain: Domain, action: str) -> None:
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(400, "Invalid container app action.")
    is_stack = is_compose_app(app)
    if is_stack:
        from services.official_stacks import compose_runtime, stack_runtime_service
        try:
            stack = compose_runtime.stack_from_runtime(app)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        legacy_runtime = not getattr(app, "stack_services", None) and not compose_runtime.compose_path(app.id).is_file()
        if action == "stop":
            await _offline(db, domain)
            if legacy_runtime:
                await asyncio.to_thread(stack_runtime_service.stop_stack, app.id, stack)
            else:
                await asyncio.to_thread(compose_runtime.stop, app.id)
            app.status, app.last_error, app.health_state = "stopped", None, "unverified"
            app.health_detail = "Stack is stopped."
            return
        if legacy_runtime:
            for service_name in stack.startup_order:
                await _docker(["docker", action, stack_runtime_service.stack_container_name(app.id, service_name)])
        else:
            if action == "restart":
                await asyncio.to_thread(compose_runtime.stop, app.id)
            await asyncio.to_thread(compose_runtime.start, app.id)
        states = await asyncio.to_thread(stack_runtime_service.inspect_stack_services, app.id, stack)
        if any(item["status"] not in {"running", "restarting"} for item in states.values()):
            raise HTTPException(409, "Saved stack did not start all services. Open diagnostics before retrying.")
        health_path = (stack.web_health_path or "").strip()
        app.health_state, app.health_detail = "unverified", "No verified HTTP readiness endpoint is configured."
        if health_path:
            try:
                await container_app_deployment_progress_service.wait_for_http(
                    app.host_port, path=health_path, host_header=domain.name,
                    timeout_seconds=stack.startup_timeout_seconds,
                )
            except RuntimeError as exc:
                app.health_state, app.health_detail = "degraded", str(exc)[:1000]
            else:
                app.health_state, app.health_detail = "healthy", f"Private HTTP check passed on {health_path}."
        await publish(db, app, domain)
        app.status, app.last_error = "running", None
        return

    if action == "stop":
        await _offline(db, domain)
        await _docker(["docker", "stop", "--time", "20", app.container_name])
        app.status, app.last_error, app.health_state = "stopped", None, "unverified"
        app.health_detail = "Application is stopped."
        return
    try:
        await _docker(["docker", action, app.container_name])
        health_path = (getattr(app, "health_path", None) or "").strip()
        app.health_state, app.health_detail = "unverified", "No HTTP readiness endpoint is configured."
        if health_path.lower() not in {"disabled", "none", "skip", "off"}:
            try:
                await container_app_deployment_progress_service.wait_for_http(
                    app.host_port, path=health_path or "/", host_header=domain.name,
                    timeout_seconds=getattr(app, "startup_timeout_seconds", None) or 45,
                )
            except RuntimeError as exc:
                insp = container_app_service._run(["docker", "inspect", "--format", "{{.State.Status}}", app.container_name], timeout=10)
                if insp.returncode != 0 or (insp.stdout or "").strip().lower() != "running":
                    raise
                app.health_state, app.health_detail = "degraded", str(exc)[:1000]
            else:
                app.health_state, app.health_detail = "healthy", f"Private HTTP check passed on {health_path or '/'}."
    except (RuntimeError, HTTPException) as exc:
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
