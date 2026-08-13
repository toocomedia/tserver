"""Trusted administrator Python-app deployment orchestration."""
from __future__ import annotations
import os, secrets, shutil
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import config
from dependencies import dependency_manager
from dependencies.git import repository_service
from models.domain import Domain
from models.hosted_app import HostedApp
from services import nginx_service
from services import app_project_detector
from services import app_release_service
from services import app_runtime_service
from services import app_ownership_service
ROOT = Path(config.APP_HOSTING_ROOT)
ENV_ROOT = Path(config.APP_HOSTING_ENV_ROOT)
GIT_URL_RE = repository_service.GIT_URL_RE
BRANCH_RE = repository_service.BRANCH_RE

def _app_dir(app_id: int) -> Path: return ROOT / str(app_id)
def suggest_project(path: Path) -> dict[str, object]: return app_project_detector.detect_project(path)
def current_source(app: HostedApp) -> Path:
    pending = Path(app.work_dir) / "pending" / "source"
    if pending.is_dir():
        return pending
    active = app_release_service.active_release_path(app)
    return active / "source" if active else Path(app.work_dir) / "source"

def _ensure_runtime_dirs() -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        ENV_ROOT.mkdir(parents=True, exist_ok=True)
        ROOT.chmod(0o700)
        ENV_ROOT.chmod(0o700)
    except PermissionError as exc:
        raise HTTPException(500, "Python app storage is not ready. Run the panel update on the VPS.") from exc

async def _progress(reporter, stage: str, message: str) -> None:
    if reporter: await reporter(stage, message)

def inspect_repository(repository_url: str, branch: str) -> dict[str, object]:
    """Read project files from a temporary shallow clone; never run app code."""
    if not dependency_manager.is_healthy("git"):
        raise HTTPException(409, "Git & SSH dependency is required.")
    with repository_service.temporary_clone(
        repository_url, branch, allow_default_branch=True
    ) as checkout:
        project = suggest_project(checkout.path)
        project["repository_url"], project["branch"] = (
            checkout.repository_url, checkout.branch
        )
        project["revision"] = checkout.revision.sha
        if checkout.repository_url != repository_url:
            project["transport_note"] = (
                "SSH was unavailable, so this public GitHub repository will use HTTPS."
            )
        return project
async def next_port(db: AsyncSession) -> int:
    used = set((await db.scalars(select(HostedApp.port))).all())
    for port in range(config.APP_HOSTING_PORT_START, 65536):
        if port not in used:
            try:
                app_ownership_service.require_port_free(port)
                return port
            except HTTPException:
                continue
    raise HTTPException(409, "No private application ports are available.")


async def validate_port(db: AsyncSession, port: int) -> None:
    if not config.APP_HOSTING_PORT_START <= port <= 65535:
        raise HTTPException(400, f"Choose a private port from {config.APP_HOSTING_PORT_START} to 65535.")
    owner = await db.scalar(select(HostedApp.id).where(HostedApp.port == port))
    if owner:
        raise HTTPException(409, f"Private port {port} belongs to another Python app.")
    app_ownership_service.require_port_free(port)

async def create_app(db: AsyncSession, domain_id: int, source_type: str, repository_url: str | None, branch: str, build: str, start: str, ssl: bool, postgres_mode: str, external_url: str | None, supabase_project_id: int | None = None, port: int | None = None, database_url_scheme: str = "postgresql") -> HostedApp:
    if source_type != "git": raise HTTPException(409, "ZIP source is coming soon.")
    if postgres_mode not in {"none", "create", "external", "supabase"}: raise HTTPException(400, "Invalid app setup.")
    if source_type == "git" and (not repository_url or not dependency_manager.is_healthy("git")): raise HTTPException(409, "Git & SSH dependency is required.")
    if not dependency_manager.is_healthy("python"): raise HTTPException(409, "Python Runtime dependency is required.")
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.project_type != "python":
        raise HTTPException(409, "This domain is not available for Python hosting.")
    if await db.scalar(select(HostedApp.id).where(HostedApp.domain_id == domain_id)):
        raise HTTPException(409, "This domain already has Python app setup. Open it from the domain page.")
    app_runtime_service.validate_commands(build, start)
    _ensure_runtime_dirs()
    port = await next_port(db) if port is None else port
    await validate_port(db, port)
    app = HostedApp(domain_id=domain_id, source_type=source_type, repository_url=repository_url, branch=branch or "main", build_command=build, start_command=start, port=port, service_name="pending", work_dir="pending", env_path="pending", ssl_requested=ssl, postgres_mode=postgres_mode)
    if postgres_mode == "external" and not external_url: raise HTTPException(400, "DATABASE_URL is required for an external database.")
    if postgres_mode == "supabase" and not supabase_project_id: raise HTTPException(400, "Select a Supabase project.")
    db.add(app); await db.flush()
    app_ownership_service.apply_identity(app)
    if postgres_mode == "supabase":
        from plugins.supabase import service as supabase_service
        app.supabase_project_id = supabase_project_id
        app.database_name = app.database_user = f"app{app.id}_{secrets.token_hex(4)}"
        external_url = await supabase_service.provision_app_database(
            supabase_project_id, app.database_name, app.database_user,
            secrets.token_urlsafe(24), db,
        )
        external_url = app_runtime_service.database_url_with_scheme(
            external_url, database_url_scheme
        )
    if postgres_mode in {"external", "supabase"}:
        ENV_ROOT.mkdir(parents=True, exist_ok=True)
        Path(app.env_path).write_text(f"DATABASE_URL={external_url}\n", encoding="utf-8")
        os.chmod(app.env_path, 0o600)
    return app

async def deploy(
    app: HostedApp,
    domain_name: str,
    deployment_id: int,
    action: str = "deploy",
    target_revision: str | None = None,
    reporter=None,
) -> dict[str, str]:
    _ensure_runtime_dirs()
    app_ownership_service.apply_identity(app)
    app_ownership_service.assert_unit_owner(app)
    await _progress(reporter, "source", "Preparing application source.")
    prepared = await app_release_service.prepare(
        app, deployment_id, action, target_revision
    )
    try:
        await app_runtime_service.build_release(app, prepared.path, reporter)
        rollback_status = await app_release_service.cutover(app, prepared, reporter)
    except Exception:
        if app_release_service.active_release_path(app) != prepared.path:
            shutil.rmtree(prepared.path, ignore_errors=True)
        raise
    if not nginx_service.config_exists(domain_name):
        await _progress(reporter, "nginx", "Enabling the Nginx proxy.")
        await nginx_service.create_proxy(domain_name, "127.0.0.1", app.port, "http")
        await nginx_service.reload()
    app_release_service.finish_success(app, prepared)
    return {
        "revision": prepared.revision,
        "rollback_status": rollback_status,
    }
