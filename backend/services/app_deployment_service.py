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
from services import ssl_service


async def start(db: AsyncSession, app: HostedApp) -> AppDeployment:
    active = await db.scalar(select(AppDeployment.id).where(
        AppDeployment.app_id == app.id,
        AppDeployment.status.in_(("queued", "running")),
    ))
    if active:
        raise HTTPException(409, "A deployment is already running for this app.")
    deployment = AppDeployment(app_id=app.id)
    db.add(deployment)
    await db.flush()
    asyncio.create_task(_run_after_commit(deployment.id))
    return deployment


async def latest(db: AsyncSession, app_id: int) -> AppDeployment | None:
    return await db.scalar(select(AppDeployment).where(
        AppDeployment.app_id == app_id,
    ).order_by(desc(AppDeployment.id)))


async def get(db: AsyncSession, app_id: int, deployment_id: int) -> AppDeployment:
    deployment = await db.get(AppDeployment, deployment_id)
    if deployment is None or deployment.app_id != app_id:
        raise HTTPException(404, "Deployment not found.")
    return deployment


async def recover_interrupted() -> None:
    async with AsyncSessionLocal() as db:
        stale = (await db.scalars(select(AppDeployment).where(
            AppDeployment.status.in_(("queued", "running")),
        ))).all()
        for deployment in stale:
            deployment.status, deployment.stage = "failed", "interrupted"
            deployment.error = "Panel restarted before this deployment finished."
            deployment.finished_at = datetime.utcnow()
        if stale:
            await db.commit()


async def _run_after_commit(deployment_id: int) -> None:
    await asyncio.sleep(0.5)
    async with AsyncSessionLocal() as db:
        deployment = await db.get(AppDeployment, deployment_id)
        if deployment is None:
            return
        app = await db.get(HostedApp, deployment.app_id)
        domain = await db.get(Domain, app.domain_id) if app else None
        if app is None or domain is None:
            await _finish(db, deployment, "failed", "setup", "App domain no longer exists.")
            return
        deployment.status, deployment.started_at = "running", datetime.utcnow()
        deployment.stage = "deploy"
        deployment.output = "[deploy] Preparing source, dependencies, service, and health check.\n"
        await db.commit()
        try:
            await app_hosting_service.deploy(app, domain.name, _reporter(db, deployment))
            existing_cert = await db.scalar(
                select(SslCert.id).where(SslCert.full_domain == domain.name)
            )
            if app.ssl_requested or existing_cert:
                deployment.stage = "ssl"
                deployment.output = (deployment.output + "[ssl] Configuring HTTPS proxy.\n")[-80_000:]
                await db.commit()
                await ssl_service.configure_hosted_app_ssl(db, app, domain)
            app.status = "running"
            await _finish(db, deployment, "success", "complete", None)
        except Exception as exc:
            app.status, app.last_error = "failed", str(exc)[:1000]
            await _finish(db, deployment, "failed", deployment.stage, str(exc))


def _reporter(db: AsyncSession, deployment: AppDeployment):
    async def report(stage: str, message: str) -> None:
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
