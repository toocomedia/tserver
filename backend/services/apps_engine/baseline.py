"""Create encrypted recovery snapshots for existing Apps Engine apps without deployment."""
from __future__ import annotations

import logging

import config
from sqlalchemy import select

from models.container_app import ContainerApp
from services.apps_engine import snapshots


logger = logging.getLogger(__name__)


async def create_missing_baselines(session_factory) -> None:
    """Best-effort migration. Never stops, rebuilds, or changes a running container."""
    if getattr(config, "_SECRET_KEY_EPHEMERAL", False) or not config.SECRET_KEY:
        logger.warning("App Engine snapshot baseline skipped: persistent SECRET_KEY is not configured.")
        return
    async with session_factory() as db:
        apps = list((await db.scalars(select(ContainerApp).where(
            ContainerApp.active_snapshot_id.is_(None),
        ))).all())
        for app in apps:
            if app.source_type == "git" and not app.deployed_revision:
                logger.warning("App Engine baseline deferred for app %s: no deployed Git SHA yet.", app.id)
                continue
            if app.source_type == "image" and not app.image_digest:
                logger.warning("App Engine baseline deferred for app %s: no deployed image digest yet.", app.id)
                continue
            try:
                await snapshots.baseline_snapshot(db, app)
            except Exception as exc:
                # A broken or inaccessible source must not block panel startup or change its live app.
                logger.warning("App Engine baseline skipped for app %s: %s", app.id, exc)
        await db.commit()
