"""Source-aware update checks and validation for hosted Python apps."""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies import dependency_manager
from dependencies.git import repository_service
from models.app_deployment import AppDeployment
from models.hosted_app import HostedApp


async def check_git_update(db: AsyncSession, app: HostedApp):
    _require_source(app, "git")
    await assert_idle(db, app.id)
    if not dependency_manager.is_healthy("git"):
        raise HTTPException(409, "Git & SSH dependency is required.")
    revision = await asyncio.to_thread(
        repository_service.remote_revision,
        app.repository_url or "",
        app.branch or "main",
    )
    app.source_checked_at = datetime.utcnow()
    app.available_revision = revision.sha
    app.available_revision_message = revision.message
    app.available_revision_at = (
        revision.committed_at.replace(tzinfo=None) if revision.committed_at else None
    )
    await db.flush()
    return revision


async def assert_update_ready(db: AsyncSession, app: HostedApp) -> str:
    _require_source(app, "git")
    await assert_idle(db, app.id)
    if not app.available_revision:
        raise HTTPException(409, "No update is ready; check Git first.")
    if app.available_revision == app.deployed_revision:
        raise HTTPException(409, "The app is already on this source revision.")
    return app.available_revision


async def assert_idle(db: AsyncSession, app_id: int) -> None:
    active = await db.scalar(select(AppDeployment.id).where(
        AppDeployment.app_id == app_id,
        AppDeployment.status.in_(("queued", "running")),
    ))
    if active:
        raise HTTPException(409, "A deployment is already running for this app.")


def has_update(app: HostedApp) -> bool:
    return bool(
        app.available_revision
        and app.available_revision != app.deployed_revision
    )


def _require_source(app: HostedApp, source_type: str) -> None:
    if app.source_type != source_type:
        raise HTTPException(400, f"This action is only available for {source_type.upper()} apps.")
