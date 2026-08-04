"""Lightweight in-process hosted-app deployments with persisted progress."""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models.app_deployment import AppDeployment
from models.domain import Domain
from models.hosted_app import HostedApp
from models.ssl_cert import SslCert
from services import app_hosting_service
from services import app_dependency_service
from services import app_lifecycle_service
from services import ssl_service
from services.resource_guard_service import resource_guard_service

async def start(
    db: AsyncSession,
    app: HostedApp,
    *,
    action: str = "deploy",
    source_revision: str | None = None,
) -> AppDeployment:
    if action not in {"deploy", "update", "redeploy"}:
        raise HTTPException(400, "Invalid deployment action.")
    if app.status in {"deleting", "delete_failed"}:
        raise HTTPException(409, "Finish or retry deletion before deploying this app.")
    app_dependency_service.require_available(app)
    try:
        await resource_guard_service.allow_start(db)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    app_lifecycle_service.ensure_available(app.id)
    await ensure_idle(db, app.id)
    deployment = AppDeployment(
        app_id=app.id,
        action=action,
        source_revision=source_revision,
        previous_revision=app.deployed_revision,
    )
    db.add(deployment)
    await db.flush()
    asyncio.create_task(_run_after_commit(deployment.id))
    return deployment

async def latest(db: AsyncSession, app_id: int) -> AppDeployment | None:
    return await db.scalar(select(AppDeployment).where(
        AppDeployment.app_id == app_id,
    ).order_by(desc(AppDeployment.id)))

async def ensure_idle(db: AsyncSession, app_id: int) -> None:
    active = await db.scalar(select(AppDeployment.id).where(
        AppDeployment.app_id == app_id,
        AppDeployment.status.in_(("queued", "running")),
    ))
    if active:
        raise HTTPException(409, "A deployment is already running for this app.")

async def get(db: AsyncSession, app_id: int, deployment_id: int) -> AppDeployment:
    deployment = await db.get(AppDeployment, deployment_id)
    if deployment is None or deployment.app_id != app_id:
        raise HTTPException(404, "Deployment not found.")
    return deployment

async def cancel(db: AsyncSession, app: HostedApp) -> None:
    active = (await db.scalars(select(AppDeployment).where(
        AppDeployment.app_id == app.id,
        AppDeployment.status.in_(("queued", "running")),
    ))).all()
    for deployment in active:
        deployment.status, deployment.stage = "cancelled", "cancelled"
        deployment.error = "Stopped by the user."
        deployment.output = (deployment.output + "[cancelled] Stop requested by the user.\n")[-80_000:]
        deployment.finished_at = datetime.utcnow()
    await db.commit()
async def recover_interrupted() -> None:
    async with AsyncSessionLocal() as db:
        stale = (await db.scalars(select(AppDeployment).where(
            AppDeployment.status.in_(("queued", "running")),
        ))).all()
        for deployment in stale:
            deployment.status, deployment.stage = "failed", "interrupted"
            deployment.error = "Panel restarted before this deployment finished."
            deployment.finished_at = datetime.utcnow()
        deleting = (await db.scalars(select(HostedApp).where(
            HostedApp.status == "deleting",
        ))).all()
        for app in deleting:
            app.status = "delete_failed"
            app.last_error = "Panel restarted before deletion finished. Retry deletion."
        if stale or deleting:
            await db.commit()

async def _run_after_commit(deployment_id: int) -> None:
    task = asyncio.current_task()
    app_id: int | None = None
    guard_token: int | None = None
    try:
        await asyncio.sleep(0.5)
        async with AsyncSessionLocal() as db:
            deployment = await db.get(AppDeployment, deployment_id)
            if deployment is None or deployment.status != "queued":
                return
            app = await db.get(HostedApp, deployment.app_id)
            domain = await db.get(Domain, app.domain_id) if app else None
            if app is None or domain is None:
                await _finish(db, deployment, "failed", "setup", "App domain no longer exists.")
                return
            app_id = app.id
            priority = await resource_guard_service.priority(db, "hosted_app", str(app.id))
            guard_token = resource_guard_service.register(
                "hosted_app", str(app.id), priority, f"Python app: {domain.name}",
                lambda: asyncio.create_task(app_lifecycle_service.cancel_deployment(app.id)),
            )
            if task:
                app_lifecycle_service.register_deployment(app_id, task)
            await app_lifecycle_service.run(
                app_id, lambda: _run_deployment(db, deployment, app, domain), wait=True
            )
    except asyncio.CancelledError:
        await _mark_cancelled(deployment_id)
        raise
    finally:
        if guard_token is not None:
            resource_guard_service.unregister(guard_token)
        if app_id is not None and task:
            app_lifecycle_service.unregister_deployment(app_id, task)

async def _run_deployment(
    db: AsyncSession, deployment: AppDeployment, app: HostedApp, domain: Domain
) -> None:
    await db.refresh(deployment)
    if deployment.status != "queued":
        return
    deployment.status, deployment.started_at = "running", datetime.utcnow()
    deployment.stage = "deploy"
    deployment.output = (
        f"[deploy] Preparing {deployment.action} release, dependencies, "
        "service, and health check.\n"
    )
    await db.commit()
    was_running = app.status == "running"
    try:
        result = await app_hosting_service.deploy(
            app, domain.name, deployment.id, deployment.action,
            deployment.source_revision, _reporter(db, deployment),
        )
        deployment.source_revision = result["revision"]
        deployment.rollback_status = result["rollback_status"]
        existing_cert = await db.scalar(
            select(SslCert.id).where(SslCert.full_domain == domain.name)
        )
        if deployment.action == "deploy" and (app.ssl_requested or existing_cert):
            deployment.stage = "ssl"
            deployment.output = (deployment.output + "[ssl] Configuring HTTPS proxy.\n")[-80_000:]
            await db.commit()
            await ssl_service.configure_hosted_app_ssl(db, app, domain)
        await app_dependency_service.publish_app(db, app, domain)
        app.status, app.last_error = "running", None
        app.paused_by_dependency = None
        await _finish(db, deployment, "success", "complete", None)
    except Exception as exc:
        await db.refresh(deployment)
        await db.refresh(app)
        if deployment.status == "cancelled":
            if app.status != "deleting":
                app.status = "stopped"
            await db.commit()
            return
        rollback = getattr(exc, "rollback_status", None)
        if rollback:
            deployment.rollback_status = rollback
            deployment.output = (deployment.output + f"[rollback] Automatic rollback: {rollback}.\n")[-80_000:]
        app.status = "running" if was_running and rollback in (None, "not_needed", "succeeded") else "failed"
        app.last_error = str(exc)[:1000]
        await _finish(db, deployment, "failed", deployment.stage, str(exc))


async def _mark_cancelled(deployment_id: int) -> None:
    async with AsyncSessionLocal() as db:
        deployment = await db.get(AppDeployment, deployment_id)
        if deployment is None:
            return
        if deployment.status in {"queued", "running"}:
            deployment.status, deployment.stage = "cancelled", "cancelled"
            deployment.error = "Stopped by the user."
            deployment.finished_at = datetime.utcnow()
        app = await db.get(HostedApp, deployment.app_id)
        if app and app.status != "deleting":
            app.status = "stopped"
        await db.commit()


def _reporter(db: AsyncSession, deployment: AppDeployment):
    async def report(stage: str, message: str) -> None:
        await db.refresh(deployment)
        if deployment.status == "cancelled":
            raise HTTPException(409, "Deployment was stopped by the user.")
        deployment.stage = stage
        deployment.output = (deployment.output + f"[{stage}] {message}\n")[-80_000:]
        await db.commit()
    return report


async def _finish(db: AsyncSession, deployment: AppDeployment, status: str, stage: str, error: str | None) -> None:
    deployment.status, deployment.stage = status, stage
    deployment.error, deployment.finished_at = (error or None), datetime.utcnow()
    if error:
        deployment.output = (deployment.output + f"[{stage}] {error}\n")[-80_000:]
    await db.commit()
