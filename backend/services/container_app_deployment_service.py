"""Build, run, health-check, and roll back Railpack container applications."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import shlex
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
from services.apps_engine import build_secrets
from services.apps_engine import snapshots
from services.resource_guard_service import resource_guard_service
from services.resource_guard_operation_service import resource_guard_operation_service
from services.resource_guard_profiles import classify_deployment

_build_lock = asyncio.Lock()


async def recover_interrupted() -> None:
    """Do not leave a deployment permanently busy after a panel restart."""
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        deployments = (await db.scalars(select(ContainerAppDeployment).where(
            ContainerAppDeployment.status == "running",
        ))).all()
        now = datetime.utcnow()
        stale_ids = []
        for deployment in deployments:
            deployment.status = "failed"
            deployment.stage = "interrupted"
            deployment.error = "Panel restarted before this deployment completed. Redeploy to try again."
            deployment.finished_at = now
            stale_ids.append(deployment.id)
        apps = list((await db.scalars(select(ContainerApp).where(
            ContainerApp.status.in_(("pending", "deleting", "running")),
        ))).all())
        for app in apps:
            queued = await db.scalar(select(ContainerAppDeployment.id).where(
                ContainerAppDeployment.app_id == app.id,
                ContainerAppDeployment.status == "queued",
            ))
            if queued:
                continue
            live = await asyncio.to_thread(_container_running, app)
            if live:
                app.status, app.last_error = "running", None
            elif app.status == "deleting":
                app.status, app.last_error = "delete_failed", "Panel restarted while removal was in progress. Retry removal."
            elif app.status == "pending":
                app.status, app.last_error = "failed", "No running container was found after panel restart. Redeploy to recover."
        await db.commit()
    # Best-effort filesystem reclaim for interrupted builds (source + XDG workspace)
    for dep_id in stale_ids:
        await asyncio.to_thread(_cleanup_build_artifacts_sync, dep_id)
    # Also reap any 7d+ stale workspaces left from prior crashes
    try:
        await reap_stale_build_workspaces()
    except Exception:
        pass
    await advance_queue()


async def active_deployment(db: AsyncSession, app_id: int) -> ContainerAppDeployment | None:
    return await db.scalar(select(ContainerAppDeployment).where(
        ContainerAppDeployment.app_id == app_id,
        ContainerAppDeployment.status.in_(("queued", "running")),
    ).order_by(ContainerAppDeployment.id.desc()))


async def queue_deployment(
    db: AsyncSession, app: ContainerApp, action: str = "deploy", *, snapshot_id: int | None = None,
) -> ContainerAppDeployment:
    if action not in {"deploy", "redeploy", "retry", "rollback"}:
        raise HTTPException(400, "Unsupported deployment action.")
    active = await db.scalar(select(ContainerAppDeployment.id).where(
        ContainerAppDeployment.app_id == app.id,
        ContainerAppDeployment.status.in_(("queued", "running")),
    ))
    if active:
        raise HTTPException(409, "A deployment is already running for this app.")
    try:
        snapshot = await snapshots.get_snapshot(db, app, snapshot_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if snapshot.state == "discarded":
        raise HTTPException(409, "Discarded deployment snapshot cannot be deployed.")
    profile = classify_deployment(snapshots.runtime_app(app, snapshot))
    preflight = await resource_guard_service.preflight(db, profile)
    is_build_queue = "build is already running" in preflight["reason"].lower()
    if not preflight["ok"] and not is_build_queue:
        raise HTTPException(409, preflight["reason"])
    deployment = ContainerAppDeployment(
        app_id=app.id, action=action, profile=profile,
        snapshot_id=snapshot.id, snapshot_fingerprint=snapshot.fingerprint,
    )
    db.add(deployment)
    await db.flush()
    domain = await db.get(Domain, app.domain_id)
    priority = await resource_guard_service.priority(db, "container_app", str(app.id))

    def cancel() -> None:
        build_process.cancel(deployment.id)
        asyncio.create_task(_mark_queued_cancelled(deployment.id))

    operation = await resource_guard_operation_service.create(
        db,
        component_type="container_app",
        component_id=str(app.id),
        operation_type=action,
        priority=priority,
        label=f"Apps Engine: {domain.name if domain else app.id}",
        profile=profile,
        status="queued" if is_build_queue else "running",
        deployment_id=deployment.id,
        cancel=cancel,
    )
    if is_build_queue:
        return deployment
    token = resource_guard_service.register(
        "container_app", str(app.id), priority, operation.label,
        lambda: build_process.cancel(deployment.id), profile=profile,
    )
    asyncio.create_task(_deploy_after_commit(deployment.id, token, operation.id))
    return deployment


async def _deploy_after_commit(deployment_id: int, token: int, operation_id: int) -> None:
    from database import AsyncSessionLocal

    await asyncio.sleep(0.3)
    async with AsyncSessionLocal() as db:
        deployment = await db.get(ContainerAppDeployment, deployment_id)
        app = await db.get(ContainerApp, deployment.app_id) if deployment else None
        domain = await db.get(Domain, app.domain_id) if app else None
        if deployment is None or deployment.status != "queued" or app is None or domain is None:
            await resource_guard_operation_service.finish(
                db, operation_id, "cancelled" if deployment and deployment.status == "cancelled" else "failed"
            )
            resource_guard_service.unregister(token)
            await db.commit()
            await advance_queue()
            return
        try:
            snapshot = await snapshots.get_snapshot(db, app, deployment.snapshot_id)
            prior_snapshot = await snapshots.get_snapshot(db, app, app.active_snapshot_id)
            runtime = snapshots.runtime_app(app, snapshot)
            prior_runtime = snapshots.runtime_app(app, prior_snapshot)
            await snapshots.materialize_environment(db, app, snapshot)
        except Exception as exc:
            deployment.status, deployment.stage, deployment.error = "failed", "prepare", str(exc)
            app.last_error = str(exc)
            deployment.finished_at = datetime.utcnow()
            await resource_guard_operation_service.finish(db, operation_id, "failed")
            await db.commit()
            resource_guard_service.unregister(token)
            await advance_queue()
            return
        deployment.status, deployment.started_at = "running", datetime.utcnow()
        await progress.stage(db, deployment, "prepare", "Preparing deployment.")
        running_image, replacement_started = prior_runtime.image_digest or prior_runtime.image_reference, False
        try:
            if getattr(runtime, "deploy_type", None) == "official_stack":
                await _deploy_official_stack(db, app, domain, snapshot, prior_snapshot, runtime, prior_runtime, deployment)
            else:
                image = await _prepare_image(db, runtime, deployment)
                if runtime.source_type == "image":
                    snapshot.image_digest = runtime.image_digest
                else:
                    snapshot.source_revision = runtime.deployed_revision
                await progress.stage(db, deployment, "start", "Starting application container.")
                await asyncio.to_thread(_replace_container, runtime, image)
                replacement_started = True
                await progress.stage(db, deployment, "health", "Checking the private HTTP endpoint.")
                await progress.wait_for_http(
                    runtime.host_port,
                    path=runtime.health_path or "/",
                    timeout_seconds=runtime.startup_timeout_seconds or 45,
                )
                from services import container_app_control_service
                await progress.stage(db, deployment, "routing", "Publishing the application route.")
                await container_app_control_service.publish(db, runtime, domain)
                if runtime.preset == "wordpress":
                    from services import container_app_wordpress_service
                    await progress.stage(db, deployment, "wordpress", "Finishing WordPress setup.")
                    await asyncio.to_thread(container_app_wordpress_service.install_if_pending, runtime, domain)
                if runtime.ssl_requested:
                    from services import ssl_service
                    await progress.stage(db, deployment, "ssl", "Configuring HTTPS.")
                    await ssl_service.configure_container_app_ssl(db, runtime, domain)
                await snapshots.promote_snapshot(db, app, snapshot, runtime)
                app.status, app.last_error, app.deployed_at = "running", None, datetime.utcnow()
                deployment.status = "success"
                await progress.stage(db, deployment, "complete", "Deployment complete.")
        except build_process.BuildCancelled as exc:
            deployment.status, deployment.stage, deployment.error = "cancelled", "cancelled", str(exc)
            progress.append_log(deployment, "cancelled", str(exc))
            app.status, app.last_error = ("running", None) if await asyncio.to_thread(_container_running, app) else ("failed", str(exc))
        except Exception as exc:
            if getattr(runtime, "deploy_type", None) == "official_stack":
                await snapshots.mark_failed(snapshot, str(exc))
                app.status, app.last_error = "failed", str(exc)[:1000]
                deployment.status, deployment.error = "failed", str(exc)[:2000]
                deployment.output = (deployment.output + f"[error] {exc}\n")[-80_000:]
            else:
                deployment.output = (deployment.output + await asyncio.to_thread(progress.container_logs, runtime))[-80_000:]
                await snapshots.materialize_environment(db, app, prior_snapshot)
                restored = await _restore_previous(prior_runtime, domain, db, running_image, replacement_started, deployment)
                await snapshots.mark_failed(snapshot, str(exc))
                app.status, app.last_error = ("running", None) if restored else ("failed", str(exc)[:1000])
                deployment.status, deployment.error = "failed", str(exc)[:2000]
                deployment.output = (deployment.output + f"[error] {exc}\n")[-80_000:]
        deployment.finished_at = datetime.utcnow()
        outcome = {"success": "succeeded", "cancelled": "cancelled"}.get(deployment.status, "failed")
        await resource_guard_operation_service.finish(db, operation_id, outcome)
        await db.commit()
        resource_guard_service.unregister(token)
        # Final best-effort reclaim for this deployment's transient dirs (covers materialize/early failures)
        try:
            await asyncio.to_thread(_cleanup_build_artifacts_sync, deployment.id)
        except Exception:
            pass
        # Opportunistic TTL reap (non-blocking, ignore errors)
        try:
            await reap_stale_build_workspaces()
        except Exception:
            pass
    await advance_queue()


async def _mark_queued_cancelled(deployment_id: int) -> None:
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        deployment = await db.get(ContainerAppDeployment, deployment_id)
        if deployment and deployment.status == "queued":
            deployment.status, deployment.stage = "cancelled", "cancelled"
            deployment.error, deployment.finished_at = "Stopped by the user.", datetime.utcnow()
            await db.commit()


async def cancel_deployment(db: AsyncSession, app_id: int, deployment_id: int | None = None) -> ContainerAppDeployment | None:
    """Cancels a queued or running deployment and unlocks the application."""
    stmt = select(ContainerAppDeployment).where(
        ContainerAppDeployment.app_id == app_id,
        ContainerAppDeployment.status.in_(("queued", "running")),
    )
    if deployment_id:
        stmt = stmt.where(ContainerAppDeployment.id == deployment_id)
    deployment = await db.scalar(stmt.order_by(ContainerAppDeployment.id.desc()))
    if not deployment:
        return None

    # Cancel build subprocess if running
    build_process.cancel(deployment.id)

    # Cancel resource guard operation
    from models.guard_operation import GuardOperation
    op = await db.scalar(select(GuardOperation).where(
        GuardOperation.deployment_id == deployment.id,
        GuardOperation.status.in_(("queued", "running")),
    ))
    if op:
        await resource_guard_operation_service.finish(db, op.id, "cancelled")

    now = datetime.utcnow()
    deployment.status = "cancelled"
    deployment.stage = "cancelled"
    deployment.error = "Deployment cancelled by user."
    deployment.finished_at = now

    app = await db.get(ContainerApp, app_id)
    if app:
        live = await asyncio.to_thread(_container_running, app)
        app.status = "running" if live else "stopped"

    await db.commit()
    # Reclaim workspace/source for the cancelled deployment id
    try:
        await asyncio.to_thread(_cleanup_build_artifacts_sync, deployment.id)
    except Exception:
        pass
    await advance_queue()
    return deployment


async def advance_queue() -> None:
    """Start the oldest queued build once the current reservation ends."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        operation = await resource_guard_operation_service.next_queued(db)
        if operation is None:
            return
        preflight = await resource_guard_service.preflight(db, operation.profile)
        if not preflight["ok"]:
            return
        deployment = await db.get(ContainerAppDeployment, operation.deployment_id)
        app = await db.get(ContainerApp, deployment.app_id) if deployment else None
        domain = await db.get(Domain, app.domain_id) if app else None
        if deployment is None or deployment.status != "queued" or app is None or domain is None:
            await resource_guard_operation_service.finish(db, operation.id, "cancelled")
            await db.commit()
            return
        priority = await resource_guard_service.priority(db, "container_app", str(app.id))
        token = resource_guard_service.register(
            "container_app", str(app.id), priority, operation.label,
            lambda: build_process.cancel(deployment.id), profile=operation.profile,
        )
        await resource_guard_operation_service.start(db, operation)
        await db.commit()
        asyncio.create_task(_deploy_after_commit(deployment.id, token, operation.id))


async def _deploy_official_stack(
    db: AsyncSession, app: ContainerApp, domain: Domain, snapshot: ContainerAppSnapshot,
    prior_snapshot: ContainerAppSnapshot, runtime: SimpleNamespace, prior_runtime: SimpleNamespace,
    deployment: ContainerAppDeployment,
) -> None:
    from services.official_stacks import stack_runtime_service
    from services.official_stacks.catalog import get_stack
    stack_id = getattr(runtime, "stack_catalog_id", None)
    if not stack_id:
        raise RuntimeError(f"Application #{app.id} is missing a stack catalog identifier.")
    stack = get_stack(stack_id)
    if stack is None:
        raise RuntimeError(f"Official stack '{stack_id}' was not found in the panel catalog.")

    await progress.stage(db, deployment, "pull", f"Pulling {stack.display_name} container images.")
    await asyncio.to_thread(stack_runtime_service.pull_stack_images, stack)

    await progress.stage(db, deployment, "prepare", "Setting up stack network, configs, and volumes.")
    await asyncio.to_thread(stack_runtime_service.ensure_stack_network, app.id)
    await asyncio.to_thread(stack_runtime_service.ensure_stack_volumes, app.id, stack)
    await asyncio.to_thread(stack_runtime_service.materialize_stack_configs, app.id, stack)

    await progress.stage(db, deployment, "dependencies", "Starting dependency services.")
    for svc_name in stack.startup_order:
        svc = stack.services[svc_name]
        if svc.is_web_entrypoint:
            continue
        progress.append_log(deployment, "dependencies", f"Starting {svc_name} container.")
        await asyncio.to_thread(
            stack_runtime_service.start_service_container,
            app.id, stack, svc_name, Path(app.env_path),
        )
        await stack_runtime_service.wait_service_health(app.id, stack, svc_name)

    await progress.stage(db, deployment, "start", f"Starting {stack.display_name} web service.")
    await asyncio.to_thread(
        stack_runtime_service.start_service_container,
        app.id, stack, stack.web_service_name, Path(app.env_path), host_port=runtime.host_port,
    )

    await progress.stage(db, deployment, "health", "Checking private HTTP health probe.")
    await stack_runtime_service.wait_service_health(
        app.id, stack, stack.web_service_name, host_port=runtime.host_port,
    )

    from services import container_app_control_service
    await progress.stage(db, deployment, "routing", "Publishing reverse proxy route.")
    await container_app_control_service.publish(db, runtime, domain)

    if runtime.ssl_requested:
        from services import ssl_service
        await progress.stage(db, deployment, "ssl", "Configuring HTTPS.")
        await ssl_service.configure_container_app_ssl(db, runtime, domain)

    await snapshots.promote_snapshot(db, app, snapshot, runtime)
    app.status, app.last_error, app.deployed_at = "running", None, datetime.utcnow()
    deployment.status = "success"
    await progress.stage(db, deployment, "complete", f"{stack.display_name} deployment complete.")


async def _restore_previous(
    app: ContainerApp, domain: Domain, db: AsyncSession, image: str | None,
    replacement_started: bool, deployment: ContainerAppDeployment,
) -> bool:
    if not replacement_started or not image:
        return False
    try:
        await progress.stage(db, deployment, "rollback", "Restoring the previous image.")
        await asyncio.to_thread(_replace_container, app, image)
        await progress.wait_for_http(
            app.host_port,
            path=app.health_path or "/",
            timeout_seconds=app.startup_timeout_seconds or 45,
        )
        from services import container_app_control_service
        await container_app_control_service.publish(db, app, domain)
        app.image_digest = image
        return True
    except Exception as exc:
        progress.append_log(deployment, "rollback", f"Rollback failed: {exc}")
        return False


def _ensure_buildkit_daemon() -> None:
    inspect_res = apps._run(["docker", "inspect", "--format", "{{.HostConfig.ExtraHosts}}", "srv-panel-buildkit"], timeout=10)
    has_host_gateway = inspect_res.returncode == 0 and "host.docker.internal" in (inspect_res.stdout or "")
    running_res = apps._run(["docker", "inspect", "--format", "{{.State.Running}}", "srv-panel-buildkit"], timeout=10)
    is_running = running_res.returncode == 0 and (running_res.stdout or "").strip() == "true"

    if not is_running:
        if inspect_res.returncode == 0 and has_host_gateway:
            # Try to start existing container
            start_res = apps._run(["docker", "start", "srv-panel-buildkit"], timeout=15)
            if start_res.returncode == 0:
                is_running = True

    if not is_running or not has_host_gateway:
        apps._run(["docker", "rm", "-f", "srv-panel-buildkit"], timeout=15)
        create_res = apps._run([
            "docker", "run", "-d",
            "--name", "srv-panel-buildkit",
            "--restart", "unless-stopped",
            "--privileged",
            "--add-host", "host.docker.internal:host-gateway",
            "--label", "srv-panel.engine=railpack-buildkit",
            "moby/buildkit:buildx-stable-1",
        ], timeout=60)
        if create_res.returncode != 0:
            # Fallback to moby/buildkit:latest
            create_res = apps._run([
                "docker", "run", "-d",
                "--name", "srv-panel-buildkit",
                "--restart", "unless-stopped",
                "--privileged",
                "--add-host", "host.docker.internal:host-gateway",
                "--label", "srv-panel.engine=railpack-buildkit",
                "moby/buildkit:latest",
            ], timeout=60)
        if create_res.returncode != 0:
            err = (create_res.stderr or create_res.stdout or "Failed to start srv-panel-buildkit").strip()
            raise RuntimeError(f"Could not start BuildKit daemon (srv-panel-buildkit): {err}. Ensure Docker is running and server has sufficient RAM.")

    # Wait up to 10 seconds for BuildKit daemon socket inside container to become active
    import time
    for _ in range(10):
        test_res = apps._run(["docker", "exec", "srv-panel-buildkit", "buildctl", "debug", "workers"], timeout=5)
        if test_res.returncode == 0:
            break
        time.sleep(1)


def _ensure_buildx_builder(builder: str) -> str:
    if not builder:
        raise RuntimeError("Buildx builder name is not configured.")
    inspect_res = apps._run(["docker", "buildx", "inspect", builder], timeout=10)
    if inspect_res.returncode == 0:
        return builder
    create_res = apps._run([
        "docker", "buildx", "create",
        "--name", builder,
        "--driver", "docker-container",
        "--driver-opt", "default-load=true",
        "--bootstrap",
    ], timeout=45)
    if create_res.returncode == 0:
        return builder
    verify_res = apps._run(["docker", "buildx", "inspect", builder], timeout=10)
    if verify_res.returncode == 0:
        return builder
    err_detail = (create_res.stderr or create_res.stdout or "Unknown error").strip()
    raise RuntimeError(
        f"Buildx builder '{builder}' is missing and could not be created or booted: {err_detail}. "
        f"Repair with: docker buildx create --name {builder} --driver docker-container --bootstrap"
    )


def _read_app_env(app: ContainerApp) -> dict[str, str]:
    if not getattr(app, "env_path", None):
        return {}
    env_file = Path(app.env_path)
    if not env_file.exists():
        return {}
    result: dict[str, str] = {}
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if k:
                result[k] = v
    except Exception:
        pass
    return result


def _inject_railpack_secrets(build_root: Path, secret_names: list[str]) -> None:
    build_secrets.inject_railpack_secrets(build_root, secret_names)


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
    ref = getattr(app, "git_ref", None) or getattr(app, "branch", None) or "main"
    ref_type = getattr(app, "git_ref_type", None) or "branch"
    checkout = repository_service.clone(
        getattr(app, "repository_url", None) or "",
        ref,
        source,
        revision=getattr(app, "deployed_revision", None),
        git_ref_type=ref_type,
        ssh_key_path=getattr(app, "deploy_key_path", None),
        allow_default_branch=True,
    )
    checkout_branch = getattr(checkout, "branch", None)
    if checkout_branch and checkout_branch != app.branch:
        app.branch = checkout_branch
        app.git_ref = checkout_branch
    # revision may be missing on mocked checkouts — fall back safely
    rev = getattr(checkout, "revision", None)
    if rev is not None and getattr(rev, "sha", None):
        app.deployed_revision = rev.sha
    image = f"srv-panel/railpack-app:{app.id}-{deployment.id}"

    root_dir = (getattr(app, "root_directory", None) or "").strip().replace("\\", "/").strip("/")
    build_root = (source / root_dir).resolve() if root_dir else source.resolve()
    try:
        build_root.relative_to(source.resolve())
    except ValueError:
        raise RuntimeError("Root directory is outside repository.")
    if root_dir and (not build_root.exists() or not build_root.is_dir()):
        raise RuntimeError(f"Root directory '{root_dir}' does not exist in repository.")

    build_env: dict[str, str] | None = None
    if getattr(app, "build_mode", None) == "dockerfile":
        # Route Dockerfile builds through the panel-owned constrained Buildx builder.
        builder = _ensure_buildx_builder(config.BUILDX_BUILDER_NAME)
        dockerfile_rel = (getattr(app, "dockerfile_path", None) or "Dockerfile").strip().replace("\\", "/").lstrip("/")
        dockerfile_file = (build_root / dockerfile_rel).resolve()
        try:
            dockerfile_file.relative_to(build_root)
        except ValueError:
            raise RuntimeError("Dockerfile path is outside root directory.")
        command = [
            "docker", "buildx", "build",
            "--builder", builder,
            "--tag", image,
            "--load",  # export result to local Docker images
            "-f", str(dockerfile_file),
        ]
        build_args_val = getattr(app, "build_args", None)
        if build_args_val:
            try:
                args_obj = json.loads(build_args_val)
                if isinstance(args_obj, dict):
                    for k, v in args_obj.items():
                        command.extend(["--build-arg", f"{k}={v}"])
            except Exception:
                pass
        command.append(str(build_root))
    else:
        # Railpack: ensure BuildKit daemon is running with host.docker.internal gateway
        _ensure_buildkit_daemon()
        # Declare selected secret names. Values are private process environment only.
        env_vars = _read_app_env(app)
        if "PORT" not in env_vars:
            env_vars["PORT"] = str(getattr(app, "internal_port", None) or 3000)
        if "HOST" not in env_vars:
            env_vars["HOST"] = "0.0.0.0"
        if "NODE_ENV" not in env_vars:
            env_vars["NODE_ENV"] = "production"
        if "SKIP_DB_CHECK" not in env_vars:
            env_vars["SKIP_DB_CHECK"] = "1"

        try:
            secret_names = build_secrets.select_names(
                env_vars, getattr(app, "build_secret_keys", None),
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        declared_secrets = build_secrets.get_declared_secrets(build_root)
        all_secret_names = list(dict.fromkeys([*secret_names, *declared_secrets]))
        command = ["railpack", "build", "--name", image]
        build_env = {key: env_vars[key] for key in ("PORT", "HOST", "NODE_ENV", "SKIP_DB_CHECK") if key in env_vars}
        missing_secrets = [key for key in all_secret_names if not env_vars.get(key)]
        if missing_secrets:
            raise RuntimeError(f"Required build secrets are missing: {', '.join(missing_secrets)}.")
        if all_secret_names:
            _inject_railpack_secrets(build_root, all_secret_names)
            build_env.update({key: env_vars[key] for key in all_secret_names})
        command.append(str(build_root))

    progress.append_log(deployment, "build", "Building application image.")
    result = build_process.run(deployment.id, command, config.CONTAINER_APP_BUILD_TIMEOUT, env=build_env)
    deployment.output = (deployment.output + result.stdout + result.stderr)[-80_000:]
    if result.returncode:
        raise RuntimeError("Build failed. See deployment output.")
    app.image_digest = image
    return image


def _cleanup_build_artifacts_sync(deployment_id: int) -> None:
    """Synchronous best-effort removal of source clone + XDG workspace for a deployment."""
    # Source is per-app; we don't know app_id here without DB, so try to find via filesystem scan.
    # Preferred: if we have deployment_id, clean workspace at /var/lib/srv-panel/build/<id>
    try:
        ws_root = Path(config.CONTAINER_APP_ENV_ROOT).parent / "build" / str(deployment_id)
        if ws_root.exists() and not ws_root.is_symlink():
            shutil.rmtree(ws_root, ignore_errors=True)
        # Also clean any empty parent build dir remnants under container-apps
        # Scan container-apps/*/build/<deployment_id>
        base = Path(config.CONTAINER_APP_ROOT)
        if base.is_dir():
            for child in base.iterdir():
                cand = child / "build" / str(deployment_id)
                try:
                    if cand.is_dir() and not cand.is_symlink():
                        shutil.rmtree(cand, ignore_errors=True)
                    # Also clean legacy source path …/build/<dep>/source
                    src = cand / "source"
                    if src.exists() and not src.is_symlink():
                        shutil.rmtree(src, ignore_errors=True)
                except OSError:
                    pass
    except Exception:
        pass


async def _cleanup_build_artifacts(app_id: int, deployment_id: int) -> None:
    source = apps.root(app_id) / "build" / str(deployment_id)
    # Prefer granular source subdir, but also ensure parent build/<id> gone
    for p in (source / "source", source):
        if p.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, p, ignore_errors=True)
            except Exception:
                pass
    await asyncio.to_thread(_cleanup_build_artifacts_sync, deployment_id)


async def reap_stale_build_workspaces(ttl_days: int = 7) -> int:
    """Remove build workspaces and per-deployment build dirs older than ttl_days
    for deployments no longer running/queued. Returns number of dirs removed."""
    from database import AsyncSessionLocal
    import time as _time
    cutoff = datetime.utcnow().timestamp() - ttl_days * 86400
    removed = 0
    # Collect deployment ids that are eligible for reaping
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(select(ContainerAppDeployment).where(
            ContainerAppDeployment.status.in_(("failed", "cancelled", "success")),
        ))).all()
        eligible_ids = []
        for dep in rows:
            ts = None
            if getattr(dep, "finished_at", None):
                try:
                    ts = dep.finished_at.timestamp()
                except Exception:
                    ts = None
            # If no timestamp, use filesystem mtime check instead
            if ts is None or ts < cutoff:
                eligible_ids.append(dep.id)
    for dep_id in eligible_ids:
        # Check mtime of workspace before deleting (extra safety)
        ws = Path(config.CONTAINER_APP_ENV_ROOT).parent / "build" / str(dep_id)
        should_remove = False
        try:
            if ws.is_dir() and not ws.is_symlink():
                try:
                    mtime = ws.stat().st_mtime
                    if mtime < cutoff:
                        should_remove = True
                except OSError:
                    should_remove = True
                if should_remove:
                    await asyncio.to_thread(shutil.rmtree, ws, ignore_errors=True)
                    removed += 1
        except Exception:
            pass
        # Also per-app build dir
        base = Path(config.CONTAINER_APP_ROOT)
        if base.is_dir():
            try:
                for child in base.iterdir():
                    cand = child / "build" / str(dep_id)
                    try:
                        if cand.is_dir() and not cand.is_symlink():
                            try:
                                mtime = cand.stat().st_mtime
                                if mtime < cutoff:
                                    await asyncio.to_thread(shutil.rmtree, cand, ignore_errors=True)
                                    removed += 1
                            except OSError:
                                await asyncio.to_thread(shutil.rmtree, cand, ignore_errors=True)
                                removed += 1
                    except OSError:
                        continue
            except OSError:
                pass
    return removed


async def _prepare_image(db: AsyncSession, app: ContainerApp, deployment: ContainerAppDeployment) -> str:
    stage = "pull" if app.source_type == "image" else "build"
    message = "Pulling registry image." if stage == "pull" else "Preparing Git source and building application image."
    await progress.stage(db, deployment, stage, message)
    async with _build_lock:
        try:
            return await asyncio.to_thread(_build_or_pull, app, deployment)
        finally:
            # Always clean up the temporary source checkout + XDG workspace, even on failure/cancel.
            try:
                await _cleanup_build_artifacts(app.id, deployment.id)
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
    if getattr(app, "storage_mounts", None) and isinstance(app.storage_mounts, str):
        try:
            mounts = json.loads(app.storage_mounts)
        except Exception as exc:
            raise RuntimeError(f"Invalid storage mounts configuration: {exc}") from exc
        if not isinstance(mounts, list):
            raise RuntimeError("Storage mounts configuration must be a list.")
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            vol = mount.get("volume")
            path = mount.get("mount_path")
            if not vol or not path:
                raise RuntimeError(f"Storage mount is missing volume or path: {mount}")
            _require(
                apps._run([
                    "docker", "volume", "create",
                    "--label", "srv-panel.plugin=railpack_apps",
                    "--label", f"srv-panel.app-id={app.id}",
                    vol,
                ], timeout=30),
                f"Could not create storage volume {vol}.",
            )
            command.extend(["-v", f"{vol}:{path}"])
    elif getattr(app, "data_volume", None) and getattr(app, "data_mount_path", None) and isinstance(app.data_volume, str) and isinstance(app.data_mount_path, str):
        command.extend(["-v", f"{app.data_volume}:{app.data_mount_path}"])
    if getattr(app, "preset", None) == "wordpress" and getattr(app, "wordpress_content_volume", None) and isinstance(app.wordpress_content_volume, str):
        command.extend(["-v", f"{app.wordpress_content_volume}:/var/www/html/wp-content"])

    if getattr(app, "custom_start_command", None) and isinstance(app.custom_start_command, str) and app.custom_start_command.strip():
        cmd_str = app.custom_start_command.strip()
        if any(op in cmd_str for op in ("&&", "||", ";", "|", "\n")):
            command.extend(["--entrypoint", "/bin/sh"])
            command.append(image)
            command.extend(["-c", cmd_str])
        else:
            command.append(image)
            command.extend(shlex.split(cmd_str))
    else:
        command.append(image)
    _require(apps._run(command, timeout=60), "Container did not start.")


def _ensure_network(app: ContainerApp) -> None:
    name = apps.network_name(app.id)
    exists = apps._run(["docker", "network", "inspect", name], timeout=15)
    if exists.returncode:
        _require(apps._run([
            "docker", "network", "create", "--driver", "bridge", "--label", "srv-panel.plugin=railpack_apps",
            "--label", f"srv-panel.app-id={app.id}", name,
        ], timeout=30), "Could not create the private app network.")


def _container_running(app: ContainerApp) -> bool:
    try:
        result = apps._run(["docker", "inspect", "--format", "{{.State.Running}}", app.container_name], timeout=15)
        return result.returncode == 0 and result.stdout.strip() == "true"
    except Exception:
        return False


def _require(result, message: str) -> None:
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or message)[-1500:])
