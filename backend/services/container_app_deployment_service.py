"""Build, run, health-check, and roll back Railpack container applications."""
from __future__ import annotations

import asyncio
from datetime import datetime
import shutil

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from dependencies.git import repository_service
from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.domain import Domain
from services import container_app_service as apps

_build_lock = asyncio.Lock()


async def recover_interrupted() -> None:
    """Do not leave a deployment permanently busy after a panel restart."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        deployments = (await db.scalars(select(ContainerAppDeployment).where(
            ContainerAppDeployment.status.in_(("queued", "running")),
        ))).all()
        if not deployments:
            return
        now = datetime.utcnow()
        for deployment in deployments:
            deployment.status = "failed"
            deployment.stage = "interrupted"
            deployment.error = "Panel restarted before this deployment completed. Redeploy to try again."
            deployment.finished_at = now
        await db.commit()


async def active_deployment(db: AsyncSession, app_id: int) -> ContainerAppDeployment | None:
    return await db.scalar(select(ContainerAppDeployment).where(
        ContainerAppDeployment.app_id == app_id,
        ContainerAppDeployment.status.in_(("queued", "running")),
    ).order_by(ContainerAppDeployment.id.desc()))


async def queue_deployment(db: AsyncSession, app: ContainerApp, action: str = "deploy") -> ContainerAppDeployment:
    if action not in {"deploy", "redeploy"}:
        raise HTTPException(400, "Unsupported deployment action.")
    active = await db.scalar(select(ContainerAppDeployment.id).where(
        ContainerAppDeployment.app_id == app.id,
        ContainerAppDeployment.status.in_(("queued", "running")),
    ))
    if active:
        raise HTTPException(409, "A deployment is already running for this app.")
    deployment = ContainerAppDeployment(app_id=app.id, action=action)
    db.add(deployment)
    await db.flush()
    asyncio.create_task(_deploy_after_commit(deployment.id))
    return deployment


async def _deploy_after_commit(deployment_id: int) -> None:
    from database import AsyncSessionLocal

    await asyncio.sleep(0.3)
    async with AsyncSessionLocal() as db:
        deployment = await db.get(ContainerAppDeployment, deployment_id)
        app = await db.get(ContainerApp, deployment.app_id) if deployment else None
        domain = await db.get(Domain, app.domain_id) if app else None
        if deployment is None or deployment.status != "queued" or app is None or domain is None:
            return
        deployment.status, deployment.started_at, deployment.stage = "running", datetime.utcnow(), "prepare"
        await db.commit()
        running_image, replacement_started = app.image_digest or app.image_reference, False
        try:
            async with _build_lock:
                image = await asyncio.to_thread(_build_or_pull, app, deployment)
            await asyncio.to_thread(_replace_container, app, image)
            replacement_started = True
            await wait_for_http(app.host_port)
            from services import container_app_control_service
            await container_app_control_service.publish(db, app, domain)
            if app.ssl_requested:
                from services import ssl_service
                await ssl_service.configure_container_app_ssl(db, app, domain)
            app.status, app.last_error, app.deployed_at = "running", None, datetime.utcnow()
            deployment.status, deployment.stage = "success", "complete"
        except Exception as exc:
            deployment.output = (deployment.output + await asyncio.to_thread(_container_logs, app))[-80_000:]
            restored = await _restore_previous(app, domain, db, running_image, replacement_started, deployment)
            app.status, app.last_error = ("running", None) if restored else ("failed", str(exc)[:1000])
            deployment.status, deployment.error = "failed", str(exc)[:2000]
            deployment.output = (deployment.output + f"[error] {exc}\n")[-80_000:]
        deployment.finished_at = datetime.utcnow()
        await db.commit()


async def _restore_previous(
    app: ContainerApp, domain: Domain, db: AsyncSession, image: str | None,
    replacement_started: bool, deployment: ContainerAppDeployment,
) -> bool:
    if not replacement_started or not image:
        return False
    try:
        _log(deployment, "rollback", "Restoring the previous image.")
        await asyncio.to_thread(_replace_container, app, image)
        await wait_for_http(app.host_port)
        from services import container_app_control_service
        await container_app_control_service.publish(db, app, domain)
        app.image_digest = image
        return True
    except Exception as exc:
        _log(deployment, "rollback", f"Rollback failed: {exc}")
        return False


def _build_or_pull(app: ContainerApp, deployment: ContainerAppDeployment) -> str:
    if app.source_type == "image":
        _log(deployment, "pull", f"Pulling {app.image_reference}.")
        result = apps._run(["docker", "pull", app.image_reference or ""], timeout=config.CONTAINER_APP_BUILD_TIMEOUT)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Image pull failed.")[-1500:])
        digest = apps._run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", app.image_reference or ""], timeout=20)
        app.image_digest = digest.stdout.strip() or app.image_reference
        return app.image_reference or ""
    source = apps.root(app.id) / "build" / str(deployment.id) / "source"
    if source.exists():
        shutil.rmtree(source)
    _log(deployment, "source", "Cloning selected Git revision.")
    checkout = repository_service.clone(app.repository_url or "", app.branch or "main", source)
    app.deployed_revision = checkout.revision.sha
    image = f"srv-panel/railpack-app:{app.id}-{deployment.id}"
    command = ["docker", "build", "--tag", image, str(source)] if app.build_mode == "dockerfile" else ["railpack", "build", "--name", image, str(source)]
    _log(deployment, "build", "Building application image.")
    result = apps._run(command, timeout=config.CONTAINER_APP_BUILD_TIMEOUT)
    deployment.output = (deployment.output + result.stdout + result.stderr)[-80_000:]
    if result.returncode:
        raise RuntimeError("Build failed. See deployment output.")
    app.image_digest = image
    return image


def _replace_container(app: ContainerApp, image: str) -> None:
    previous = apps._run(["docker", "inspect", "--format", "{{.Config.Image}}", app.container_name], timeout=15)
    if previous.returncode == 0:
        app.previous_image = previous.stdout.strip() or app.previous_image
        _require(apps._run(["docker", "rm", "-f", app.container_name], timeout=30), "Could not replace existing app container.")
    _ensure_network(app)
    command = [
        "docker", "run", "-d", "--name", app.container_name, "--restart", "unless-stopped",
        "--label", "srv-panel.plugin=railpack_apps", "--label", f"srv-panel.app-id={app.id}",
        "--memory", f"{app.memory_limit_mb}m", "--memory-swap", f"{app.memory_limit_mb}m",
        "--cpus", app.cpu_limit, "--pids-limit", str(app.pid_limit), "--security-opt", "no-new-privileges",
        "--network", apps.network_name(app.id), "-p", f"127.0.0.1:{app.host_port}:{app.internal_port}",
        "--env-file", app.env_path,
    ]
    if app.database_mode == "panel_postgres":
        command.extend(["--add-host", "host.docker.internal:host-gateway"])
    if app.data_volume and app.data_mount_path:
        command.extend(["-v", f"{app.data_volume}:{app.data_mount_path}"])
    _require(apps._run([*command, image], timeout=60), "Container did not start.")


def _ensure_network(app: ContainerApp) -> None:
    name = apps.network_name(app.id)
    exists = apps._run(["docker", "network", "inspect", name], timeout=15)
    if exists.returncode:
        _require(apps._run([
            "docker", "network", "create", "--driver", "bridge", "--label", "srv-panel.plugin=railpack_apps",
            "--label", f"srv-panel.app-id={app.id}", name,
        ], timeout=30), "Could not create the private app network.")


def _require(result, message: str) -> None:
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or message)[-1500:])


def _container_logs(app: ContainerApp) -> str:
    result = apps._run(["docker", "logs", "--tail", "120", app.container_name], timeout=20)
    if result.returncode:
        return ""
    output = (result.stdout + result.stderr).strip()
    return f"\n[runtime logs]\n{output}\n" if output else ""


async def wait_for_http(port: int) -> None:
    for _ in range(20):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=1)
            writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            writer.close()
            await writer.wait_closed()
            if line.startswith(b"HTTP/") and int(line.split()[1]) < 500:
                return
        except (OSError, asyncio.TimeoutError, ValueError, IndexError):
            pass
        await asyncio.sleep(1)
    raise RuntimeError("Container did not return a healthy HTTP response on its private port.")


def _log(deployment: ContainerAppDeployment, stage: str, message: str) -> None:
    deployment.stage = stage
    deployment.output = (deployment.output + f"[{stage}] {message}\n")[-80_000:]
