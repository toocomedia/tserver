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
from services import container_app_deployment_progress_service as progress
from services import container_app_build_process_service as build_process
from services.resource_guard_service import resource_guard_service
from services.resource_guard_profiles import classify_deployment

_build_lock = asyncio.Lock()


async def recover_interrupted() -> None:
    """Do not leave a deployment permanently busy after a panel restart."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        deployments = (await db.scalars(select(ContainerAppDeployment).where(
            ContainerAppDeployment.status.in_(("queued", "running")),
        ))).all()
        now = datetime.utcnow()
        for deployment in deployments:
            deployment.status = "failed"
            deployment.stage = "interrupted"
            deployment.error = "Panel restarted before this deployment completed. Redeploy to try again."
            deployment.finished_at = now
        apps = list((await db.scalars(select(ContainerApp).where(
            ContainerApp.status.in_(("pending", "deleting", "running")),
        ))).all())
        for app in apps:
            live = await asyncio.to_thread(_container_running, app)
            if live:
                app.status, app.last_error = "running", None
            elif app.status == "deleting":
                app.status, app.last_error = "delete_failed", "Panel restarted while removal was in progress. Retry removal."
            elif app.status == "pending":
                app.status, app.last_error = "failed", "No running container was found after panel restart. Redeploy to recover."
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
    profile = classify_deployment(app)
    preflight = await resource_guard_service.preflight(db, profile)
    if not preflight["ok"]:
        raise HTTPException(409, preflight["reason"])
    deployment = ContainerAppDeployment(app_id=app.id, action=action, profile=profile)
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
        deployment.status, deployment.started_at = "running", datetime.utcnow()
        priority = await resource_guard_service.priority(db, "container_app", str(app.id))
        profile = deployment.profile or "build_large"
        token = resource_guard_service.register(
            "container_app", str(app.id), priority,
            f"Apps Engine: {domain.name}",
            lambda: build_process.cancel(deployment.id),
            profile=profile,
        )
        await progress.stage(db, deployment, "prepare", "Preparing deployment.")
        running_image, replacement_started = app.image_digest or app.image_reference, False
        try:
            image = await _prepare_image(db, app, deployment)
            await progress.stage(db, deployment, "start", "Starting application container.")
            await asyncio.to_thread(_replace_container, app, image)
            replacement_started = True
            await progress.stage(db, deployment, "health", "Checking the private HTTP endpoint.")
            await progress.wait_for_http(app.host_port)
            from services import container_app_control_service
            await progress.stage(db, deployment, "routing", "Publishing the application route.")
            await container_app_control_service.publish(db, app, domain)
            if app.preset == "wordpress":
                from services import container_app_wordpress_service
                await progress.stage(db, deployment, "wordpress", "Finishing WordPress setup.")
                await asyncio.to_thread(container_app_wordpress_service.install_if_pending, app, domain)
            if app.ssl_requested:
                from services import ssl_service
                await progress.stage(db, deployment, "ssl", "Configuring HTTPS.")
                await ssl_service.configure_container_app_ssl(db, app, domain)
            app.status, app.last_error, app.deployed_at = "running", None, datetime.utcnow()
            deployment.status = "success"
            await progress.stage(db, deployment, "complete", "Deployment complete.")
        except build_process.BuildCancelled as exc:
            deployment.status, deployment.stage, deployment.error = "cancelled", "cancelled", str(exc)
            progress.append_log(deployment, "cancelled", str(exc))
            app.status, app.last_error = ("running", None) if await asyncio.to_thread(_container_running, app) else ("failed", str(exc))
        except Exception as exc:
            deployment.output = (deployment.output + await asyncio.to_thread(progress.container_logs, app))[-80_000:]
            restored = await _restore_previous(app, domain, db, running_image, replacement_started, deployment)
            app.status, app.last_error = ("running", None) if restored else ("failed", str(exc)[:1000])
            deployment.status, deployment.error = "failed", str(exc)[:2000]
            deployment.output = (deployment.output + f"[error] {exc}\n")[-80_000:]
        deployment.finished_at = datetime.utcnow()
        await db.commit()
        resource_guard_service.unregister(token)


async def _restore_previous(
    app: ContainerApp, domain: Domain, db: AsyncSession, image: str | None,
    replacement_started: bool, deployment: ContainerAppDeployment,
) -> bool:
    if not replacement_started or not image:
        return False
    try:
        await progress.stage(db, deployment, "rollback", "Restoring the previous image.")
        await asyncio.to_thread(_replace_container, app, image)
        await progress.wait_for_http(app.host_port)
        from services import container_app_control_service
        await container_app_control_service.publish(db, app, domain)
        app.image_digest = image
        return True
    except Exception as exc:
        progress.append_log(deployment, "rollback", f"Rollback failed: {exc}")
        return False


def _build_or_pull(app: ContainerApp, deployment: ContainerAppDeployment) -> str:
    if app.source_type == "image":
        progress.append_log(deployment, "pull", f"Pulling {app.image_reference}.")
        result = build_process.run(deployment.id, ["docker", "pull", app.image_reference or ""], config.CONTAINER_APP_BUILD_TIMEOUT)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Image pull failed.")[-1500:])
        digest = apps._run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", app.image_reference or ""], timeout=20)
        app.image_digest = digest.stdout.strip() or app.image_reference
        return app.image_reference or ""
    source = apps.root(app.id) / "build" / str(deployment.id) / "source"
    if source.exists():
        shutil.rmtree(source)
    progress.append_log(deployment, "source", "Cloning selected Git revision.")
    checkout = repository_service.clone(app.repository_url or "", app.branch or "main", source)
    app.deployed_revision = checkout.revision.sha
    image = f"srv-panel/railpack-app:{app.id}-{deployment.id}"
    if app.build_mode == "dockerfile":
        # Route Dockerfile builds through the panel-owned constrained Buildx builder.
        builder = config.BUILDX_BUILDER_NAME
        command = [
            "docker", "buildx", "build",
            "--builder", builder,
            "--tag", image,
            "--load",  # export result to local Docker images
            str(source),
        ]
    else:
        # Railpack — BUILDKIT_HOST is set inside build_process.run() already
        command = ["railpack", "build", "--name", image, str(source)]
    progress.append_log(deployment, "build", "Building application image.")
    result = build_process.run(deployment.id, command, config.CONTAINER_APP_BUILD_TIMEOUT)
    deployment.output = (deployment.output + result.stdout + result.stderr)[-80_000:]
    if result.returncode:
        raise RuntimeError("Build failed. See deployment output.")
    app.image_digest = image
    return image


async def _prepare_image(db: AsyncSession, app: ContainerApp, deployment: ContainerAppDeployment) -> str:
    stage = "pull" if app.source_type == "image" else "build"
    message = "Pulling registry image." if stage == "pull" else "Preparing Git source and building application image."
    await progress.stage(db, deployment, stage, message)
    async with _build_lock:
        try:
            return await asyncio.to_thread(_build_or_pull, app, deployment)
        finally:
            # Always clean up the temporary source checkout.
            source = apps.root(app.id) / "build" / str(deployment.id) / "source"
            if source.exists():
                try:
                    await asyncio.to_thread(shutil.rmtree, source, ignore_errors=True)
                except Exception:
                    pass


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
    command.extend(["--add-host", "host.docker.internal:host-gateway"])
    if app.data_volume and app.data_mount_path:
        command.extend(["-v", f"{app.data_volume}:{app.data_mount_path}"])
    if app.preset == "wordpress" and app.wordpress_content_volume:
        command.extend(["-v", f"{app.wordpress_content_volume}:/var/www/html/wp-content"])
    _require(apps._run([*command, image], timeout=60), "Container did not start.")


def _ensure_network(app: ContainerApp) -> None:
    name = apps.network_name(app.id)
    exists = apps._run(["docker", "network", "inspect", name], timeout=15)
    if exists.returncode:
        _require(apps._run([
            "docker", "network", "create", "--driver", "bridge", "--label", "srv-panel.plugin=railpack_apps",
            "--label", f"srv-panel.app-id={app.id}", name,
        ], timeout=30), "Could not create the private app network.")


def _container_running(app: ContainerApp) -> bool:
    result = apps._run(["docker", "inspect", "--format", "{{.State.Running}}", app.container_name], timeout=15)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _require(result, message: str) -> None:
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or message)[-1500:])
