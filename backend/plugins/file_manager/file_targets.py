"""Discovery and ownership checks for File Manager target types."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.container_app import ContainerApp
from models.domain import Domain
from models.hosted_app import HostedApp
from plugins.file_manager import file_service
from services import app_ownership_service


TARGET_ID = re.compile(r"^(container|python|static):([1-9][0-9]*)$")


@dataclass(frozen=True)
class FileTarget:
    kind: str
    resource_id: int
    domain: str | None
    label: str
    status: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.resource_id}"

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id, "target_type": self.kind, "domain": self.domain,
            "preset": self.label, "status": self.status,
        }


def parse_target(value: str) -> FileTarget:
    match = TARGET_ID.fullmatch(value or "")
    if not match:
        raise HTTPException(404, "Managed application target not found.")
    return FileTarget(match.group(1), int(match.group(2)), None, "", "")


async def list_targets(db: AsyncSession) -> list[dict[str, Any]]:
    targets: list[FileTarget] = []
    targets.extend(await _container_targets(db))
    targets.extend(await _python_targets(db))
    targets.extend(await _static_targets(db))
    return [target.payload() for target in sorted(targets, key=lambda item: ((item.domain or "").lower(), item.kind, item.resource_id))]


async def roots_for(db: AsyncSession, target: FileTarget) -> list[dict[str, Any]]:
    if target.kind == "container":
        return await file_service.roots_for(db, target.resource_id)
    context_roots = await _roots(db, target)
    return [root.payload() for root in context_roots]


async def resolve_context(db: AsyncSession, target: FileTarget, root_id: str) -> file_service.FileContext:
    if target.kind == "container":
        return await file_service.resolve_context(db, target.resource_id, root_id)
    roots = await _roots(db, target)
    root = next((item for item in roots if item.id == root_id), None)
    if root is None:
        raise HTTPException(404, "File root is not available for this application.")
    app = await _host_owner(db, target)
    return file_service.FileContext(app=app, container_name=None, root=root)


async def _roots(db: AsyncSession, target: FileTarget) -> list[file_service.FileRoot]:
    if target.kind == "python":
        app = await _host_owner(db, target)
        return _python_roots(app)
    domain = await _host_owner(db, target)
    return _static_roots(domain)


async def _container_targets(db: AsyncSession) -> list[FileTarget]:
    rows = (await db.execute(
        select(ContainerApp, Domain.name).outerjoin(Domain, Domain.id == ContainerApp.domain_id)
        .where(ContainerApp.status == "running")
    )).all()
    return [FileTarget("container", app.id, domain, app.preset or "Railpack app", app.status) for app, domain in rows]


async def _python_targets(db: AsyncSession) -> list[FileTarget]:
    rows = (await db.execute(
        select(HostedApp, Domain.name).join(Domain, Domain.id == HostedApp.domain_id)
        .where(HostedApp.status.not_in(("deleting", "delete_failed")))
    )).all()
    return [FileTarget("python", app.id, domain, "Python app", app.status) for app, domain in rows if _python_roots(app)]


async def _static_targets(db: AsyncSession) -> list[FileTarget]:
    domains = list((await db.scalars(select(Domain).where(
        Domain.project_type == "static", Domain.nginx_active.is_(True),
    ))).all())
    return [FileTarget("static", domain.id, domain.name, "Static site", "ready") for domain in domains if _static_roots(domain)]


async def _host_owner(db: AsyncSession, target: FileTarget) -> HostedApp | Domain:
    if target.kind == "python":
        app = await db.get(HostedApp, target.resource_id)
        if app is None or app.status in {"deleting", "delete_failed"}:
            raise HTTPException(404, "Python app not found.")
        from services import app_deployment_service, app_lifecycle_service
        app_lifecycle_service.ensure_available(app.id)
        await app_deployment_service.ensure_idle(db, app.id)
        return app
    domain = await db.get(Domain, target.resource_id)
    if domain is None or domain.project_type != "static" or not domain.nginx_active:
        raise HTTPException(404, "Static site not found.")
    return domain


def _python_roots(app: HostedApp) -> list[file_service.FileRoot]:
    expected = app_ownership_service.work_dir(app.id)
    if Path(app.work_dir) != expected:
        return []
    release = _active_python_release(app)
    if release is None or not _inside(release, expected / "releases"):
        return []
    roots = _host_root("application", "Python application files", release / "source", False)
    data = _host_root("data", "Persistent application data", expected / "data", True)
    return [root for root in (roots, data) if root]


def _static_roots(domain: Domain) -> list[file_service.FileRoot]:
    expected = Path(config.NGINX_WEBROOT) / domain.name / "public"
    if domain.webroot_path != str(expected):
        return []
    root = _host_root("application", "Website files", expected, True)
    return [root] if root else []


def _host_root(root_id: str, label: str, path: Path, persistent: bool) -> file_service.FileRoot | None:
    try:
        path.lstat()
    except OSError:
        return None
    if not path.is_dir() or path.is_symlink():
        return None
    return file_service.FileRoot(root_id, label, str(path), "host", persistent)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
        return True
    except OSError:
        return False


def _active_python_release(app: HostedApp) -> Path | None:
    from services import app_release_service
    return app_release_service.active_release_path(app)
