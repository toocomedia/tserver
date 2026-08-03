"""Validation and persistent setup for one Railpack container application."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from dependencies import dependency_manager
from dependencies.git import repository_service
from models.container_app import ContainerApp
from models.domain import Domain

IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._.-]*)?(?:@[A-Za-z0-9:+._-]+)?$")


def root(app_id: int) -> Path:
    return Path(config.CONTAINER_APP_ROOT) / str(app_id)


def env_path(app_id: int) -> Path:
    return Path(config.CONTAINER_APP_ENV_ROOT) / f"{app_id}.env"


def network_name(app_id: int) -> str:
    return f"srv-container-net-{app_id}"


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    environment = os.environ.copy()
    if command and command[0] == "railpack":
        environment["BUILDKIT_HOST"] = "docker-container://srv-panel-buildkit"
    return subprocess.run([*prefix, *command], capture_output=True, text=True, timeout=timeout, check=False, shell=False, env=environment)


async def next_host_port(db: AsyncSession) -> int:
    used = set((await db.scalars(select(ContainerApp.host_port))).all())
    return next(port for port in range(config.CONTAINER_APP_PORT_START, 65536) if port not in used)


def validate_image_reference(value: str) -> str:
    image = value.strip()
    if not image or not IMAGE_RE.fullmatch(image) or image.startswith(("/", ".", "-")):
        raise HTTPException(400, "Enter a valid registry image reference.")
    return image


def validate_port(value: int) -> int:
    if value < 1 or value > 65535:
        raise HTTPException(400, "Container HTTP port must be between 1 and 65535.")
    return value


async def create_app(
    db: AsyncSession, *, domain: Domain, source_type: str, build_mode: str,
    repository_url: str | None, branch: str | None, image_reference: str | None,
    internal_port: int, ssl_requested: bool, environment_values: dict[str, str],
    database_mode: str = "none", database_url: str | None = None,
    database_attachments: list[dict[str, str]] | None = None,
) -> ContainerApp:
    _validate_source(domain, source_type, build_mode, repository_url, branch, image_reference)
    if await db.scalar(select(ContainerApp.id).where(ContainerApp.domain_id == domain.id)):
        raise HTTPException(409, "This domain already has a container app.")
    is_image = source_type == "image"
    port = validate_port(internal_port)
    database_values = dict(environment_values)
    attachment_specs = database_attachments
    if attachment_specs is None:
        attachment_specs = _legacy_attachment_specs(database_mode, database_url)
    from services import container_app_database_service as databases
    attachment_specs = databases.parse_specs(attachment_specs)
    for spec in attachment_specs:
        if spec["provider"] == "external":
            database_values[spec["environment_key"]] = spec["external_url"]
    app = ContainerApp(
        domain_id=domain.id, source_type=source_type,
        build_mode="image" if is_image else build_mode,
        repository_url=None if is_image else repository_url,
        branch=None if is_image else (branch or "main"),
        image_reference=validate_image_reference(image_reference or "") if is_image else None,
        container_name="pending", internal_port=port,
        host_port=await next_host_port(db), env_path="pending", ssl_requested=ssl_requested,
        database_mode="none", database_provider=None,
    )
    db.add(app)
    await db.flush()
    app.container_name, app.env_path = f"srv-container-app-{app.id}", str(env_path(app.id))
    attachments = await databases.create_attachments(db, app, attachment_specs)
    databases.rebuild_environment(app, attachments, database_values)
    return app


def _validate_source(
    domain: Domain, source_type: str, build_mode: str, repository_url: str | None,
    branch: str | None, image_reference: str | None,
) -> None:
    if not dependency_manager.is_healthy("docker"):
        raise HTTPException(409, "Docker daemon is not available.")
    if domain.project_type not in {"static", "container"}:
        raise HTTPException(409, "This domain is already used by another hosting feature.")
    if source_type == "image":
        validate_image_reference(image_reference or "")
        return
    if source_type != "git" or build_mode not in {"railpack", "dockerfile"}:
        raise HTTPException(400, "Choose a Git build mode or registry image.")
    if not dependency_manager.is_healthy("git"):
        raise HTTPException(409, "Git & SSH dependency is required.")
    repository_service.validate_source(repository_url or "", branch or "main")


def write_env(path: Path, values: dict[str, str]) -> None:
    key_re = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
    if len(values) > 128 or any(
        not isinstance(key, str) or not isinstance(value, str) or not key_re.fullmatch(key)
        or len(value) > 8192 or "\n" in value or "\r" in value
        for key, value in values.items()
    ):
        raise HTTPException(400, "Environment values must use safe uppercase names and one-line values.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def environment_for_port(values: dict[str, str], port: int) -> dict[str, str]:
    configured = values.get("PORT")
    if configured is not None and configured != str(port):
        raise HTTPException(400, "PORT must match the selected app HTTP port.")
    return {"PORT": str(port), **values}


def database_environment(mode: str, database_url: str | None, values: dict[str, str]) -> dict[str, str]:
    if mode == "none":
        return dict(values)
    if mode == "panel_postgres":
        if "DATABASE_URL" in values or database_url:
            raise HTTPException(400, "Panel PostgreSQL creates this app's DATABASE_URL automatically.")
        return dict(values)
    if mode != "external" or not database_url:
        raise HTTPException(400, "Choose no database or provide an external DATABASE_URL.")
    parsed = urlsplit(database_url)
    if not parsed.scheme or "\n" in database_url or "\r" in database_url:
        raise HTTPException(400, "Enter a valid external database URL.")
    existing = values.get("DATABASE_URL")
    if existing is not None and existing != database_url:
        raise HTTPException(400, "DATABASE_URL must match the selected database URL.")
    return {"DATABASE_URL": database_url, **values}


def _legacy_attachment_specs(mode: str, database_url: str | None) -> list[dict[str, str]]:
    if mode == "none":
        return []
    if mode == "panel_postgres":
        return [{"kind": "postgresql", "provider": "panel_postgres", "environment_key": "DATABASE_URL"}]
    if mode == "external" and database_url:
        return [{"kind": "postgresql", "provider": "external", "environment_key": "DATABASE_URL", "external_url": database_url}]
    raise HTTPException(400, "Choose no database or provide an external DATABASE_URL.")


# Backwards-compatible private aliases used by cleanup and focused tests.
_root = root
_env_path = env_path
_write_env = write_env
