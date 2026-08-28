"""Generic snapshot promotion and failure state transitions."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from services.apps_engine.snapshots import CONFIG_FIELDS


async def promote_snapshot(
    db: AsyncSession, app: ContainerApp, snapshot: ContainerAppSnapshot, runtime: SimpleNamespace,
) -> None:
    old = await db.get(ContainerAppSnapshot, app.active_snapshot_id) if app.active_snapshot_id else None
    if old and old.id != snapshot.id:
        old.state = "superseded"
    for field in CONFIG_FIELDS:
        setattr(app, field, getattr(runtime, field, None))
    app.image_digest = getattr(runtime, "image_digest", app.image_digest)
    app.deployed_revision = getattr(runtime, "deployed_revision", app.deployed_revision)
    app.configuration_revision = snapshot.configuration_revision
    app.active_snapshot_id = snapshot.id
    app.pending_snapshot_id = None if app.pending_snapshot_id == snapshot.id else app.pending_snapshot_id
    snapshot.state = "active"
    if runtime.source_type == "image":
        snapshot.image_digest = runtime.image_digest
    else:
        snapshot.source_revision = runtime.deployed_revision


async def mark_failed(snapshot: ContainerAppSnapshot, error: str) -> None:
    snapshot.state = "failed"
    snapshot.failure_fingerprint = hashlib.sha256(error[:2000].encode("utf-8")).hexdigest()
