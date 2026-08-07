"""Immutable hosted-app release preparation, cutover, and rollback."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import shutil

from fastapi import HTTPException

from dependencies.git import repository_service
from models.hosted_app import HostedApp
from services import app_runtime_service
from services import app_hosting_health_service
from services import app_ownership_service
class ReleaseFailure(Exception):
    def __init__(self, message: str, rollback_status: str = "not_needed"):
        super().__init__(message)
        self.rollback_status = rollback_status
@dataclass(frozen=True)
class PreparedRelease:
    name: str
    path: Path
    revision: str
async def prepare(
    app: HostedApp,
    deployment_id: int,
    action: str,
    target_revision: str | None,
) -> PreparedRelease:
    name = str(deployment_id)
    _discard_stale_releases(app)
    release = releases_dir(app) / name
    shutil.rmtree(release, ignore_errors=True)
    source = release / "source"
    release.mkdir(parents=True, exist_ok=True)
    try:
        revision = await _prepare_source(app, action, target_revision, source)
    except Exception:
        shutil.rmtree(release, ignore_errors=True)
        raise
    return PreparedRelease(name, release, revision)
async def cutover(app: HostedApp, prepared: PreparedRelease, reporter=None) -> str:
    old_release = active_release_path(app)
    was_running = app.status == "running" or old_release is not None
    environment = app_runtime_service.snapshot_environment(app)
    restore_source = (
        old_release / "source" if old_release is not None else prepared.path / "source"
    )
    try:
        await app_runtime_service.prepare_environment(app, prepared.path / "source")
    except Exception:
        app_runtime_service.restore_environment(app, environment, restore_source)
        raise
    try:
        await _progress(reporter, "service", "Switching to the prepared release.")
    except Exception:
        app_runtime_service.restore_environment(app, environment, restore_source)
        raise
    if was_running:
        await app_runtime_service.stop(app)
    app_ownership_service.require_port_free(app.port)
    if old_release is None:
        old_release = _move_legacy_release(app)
    _switch_current(app, prepared.path)
    try:
        await app_runtime_service.install_unit(app, prepared.path)
        await app_runtime_service.start(app)
        await _progress(reporter, "listener", "Validating the updated app locally.")
        await app_hosting_health_service.wait_for_listener(app.port)
    except Exception as exc:
        app_runtime_service.restore_environment(app, environment, restore_source)
        restart_previous = was_running
        try:
            await _progress(reporter, "rollback", "Restoring the previous release.")
        except HTTPException as report_error:
            if report_error.status_code == 409:
                restart_previous = False
            else:
                raise
        status = await _rollback(app, old_release, restart_previous)
        raise ReleaseFailure(str(exc), status) from exc
    app.previous_release = _release_name(old_release)
    app.active_release = prepared.name
    return "not_needed"
def finish_success(app: HostedApp, prepared: PreparedRelease) -> None:
    app.deployed_revision = prepared.revision
    app.deployed_at = datetime.utcnow()
    app.available_revision = None
    app.available_revision_message = None
    app.available_revision_at = None
    _retain_releases(app)
def active_release_path(app: HostedApp) -> Path | None:
    current = Path(app.work_dir) / "current"
    if current.is_symlink():
        try:
            target = current.resolve(strict=True)
            if _release_is_ready(target):
                return target
        except OSError:
            return None
    if app.active_release:
        target = releases_dir(app) / app.active_release
        if _release_is_ready(target):
            return target
    return None
def releases_dir(app: HostedApp) -> Path:
    return Path(app.work_dir) / "releases"
async def _prepare_source(
    app: HostedApp, action: str, target_revision: str | None, source: Path
) -> str:
    if action == "redeploy":
        active = active_release_path(app)
        legacy = Path(app.work_dir) / "source"
        origin = (active / "source") if active else legacy
        if origin.is_dir():
            await asyncio.to_thread(shutil.copytree, origin, source)
            return app.deployed_revision or "legacy"
        # A first deployment can fail before a current release exists. Git apps
        # can recover by fetching their configured branch again.
    if app.source_type != "git":
        raise HTTPException(409, "ZIP updates are coming soon.")
    checkout = await asyncio.to_thread(
        repository_service.clone,
        app.repository_url or "",
        app.branch or "main",
        source,
        revision=target_revision,
    )
    return checkout.revision.sha


def _move_legacy_release(app: HostedApp) -> Path | None:
    source, venv = Path(app.work_dir) / "source", Path(app.work_dir) / ".venv"
    if not source.is_dir() or not venv.is_dir():
        return None
    legacy = releases_dir(app) / "legacy"
    shutil.rmtree(legacy, ignore_errors=True)
    legacy.mkdir(parents=True, exist_ok=True)
    source.replace(legacy / "source")
    venv.replace(legacy / ".venv")
    return legacy


async def _rollback(
    app: HostedApp, old_release: Path | None, was_running: bool
) -> str:
    try:
        await app_runtime_service.stop(app)
        if not _release_is_ready(old_release):
            return "unavailable"
        _switch_current(app, old_release)
        await app_runtime_service.install_unit(app, old_release)
        if was_running:
            await app_runtime_service.start(app)
            await app_hosting_health_service.wait_for_listener(app.port)
        return "succeeded"
    except Exception:
        return "failed"


def _switch_current(app: HostedApp, target: Path) -> None:
    current = Path(app.work_dir) / "current"
    temporary = Path(app.work_dir) / ".current-new"
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary)
    os.replace(temporary, current)


def _release_is_ready(release: Path | None) -> bool:
    return bool(
        release
        and release.is_dir()
        and (release / "source").is_dir()
        and (release / ".venv").is_dir()
    )


def _retain_releases(app: HostedApp) -> None:
    keep = {app.active_release, app.previous_release}
    root = releases_dir(app)
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


def _discard_stale_releases(app: HostedApp) -> None:
    keep = {app.active_release, app.previous_release, "legacy"}
    root = releases_dir(app)
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


def _release_name(path: Path | None) -> str | None:
    return path.name if path else None


async def _progress(reporter, stage: str, message: str) -> None:
    if reporter:
        await reporter(stage, message)
