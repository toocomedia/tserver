"""Validation and persistent setup for one Railpack container application."""
from __future__ import annotations

import os
from pathlib import Path
import posixpath
import re
import shlex
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


def run_binary(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
    """Run an owned Docker command without decoding file content as text."""
    prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    return subprocess.run(
        [*prefix, *command], capture_output=True, timeout=timeout, check=False,
        shell=False,
    )


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


LABEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def validate_root_directory(value: str | None) -> str:
    if not value:
        return ""
    val = value.strip().replace("\\", "/").strip("/")
    if ".." in val.split("/"):
        raise HTTPException(400, "Root directory must not contain path traversal.")
    if len(val) > 255:
        raise HTTPException(400, "Root directory length must not exceed 255 characters.")
    return val


def validate_dockerfile_path(value: str | None) -> str:
    if not value:
        return "Dockerfile"
    val = value.strip().replace("\\", "/").lstrip("/")
    if ".." in val.split("/") or not val:
        raise HTTPException(400, "Dockerfile path must not contain path traversal.")
    if len(val) > 255:
        raise HTTPException(400, "Dockerfile path length must not exceed 255 characters.")
    return val


def validate_health_path(value: str | None) -> str:
    if not value:
        return "/"
    val = value.strip()
    if not val.startswith("/") or len(val) > 255 or any(c in val for c in ["\r", "\n", "\t"]):
        raise HTTPException(400, "Health check path must start with '/' and be a single line under 255 characters.")
    return val


def validate_startup_timeout(value: int | None) -> int:
    if value is None:
        return 45
    try:
        val = int(value)
    except (ValueError, TypeError):
        raise HTTPException(400, "Startup timeout must be an integer.")
    if val < 10 or val > 300:
        raise HTTPException(400, "Startup timeout must be between 10 and 300 seconds.")
    return val


def parse_build_args(value: str | dict | None) -> str | None:
    if not value:
        return None
    import json as _json
    if isinstance(value, str):
        try:
            value = _json.loads(value)
        except _json.JSONDecodeError as exc:
            raise HTTPException(400, "Build args must be a valid JSON object.") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "Build args must be key-value pairs.")
    arg_key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    cleaned = {}
    for k, v in value.items():
        if not isinstance(k, str) or not arg_key_re.fullmatch(k):
            raise HTTPException(400, f"Invalid build argument name '{k}'.")
        cleaned[k] = str(v) if v is not None else ""
    return _json.dumps(cleaned) if cleaned else None


def parse_storage_mounts(app_id: int, items: list[dict] | str | None) -> str | None:
    if not items:
        return None
    import json as _json
    if isinstance(items, str):
        try:
            items = _json.loads(items)
        except _json.JSONDecodeError as exc:
            raise HTTPException(400, "Storage mounts must be a valid JSON list.") from exc
    if not isinstance(items, list):
        raise HTTPException(400, "Storage mounts must be a list.")
    if len(items) > 16:
        raise HTTPException(400, "Maximum 16 storage mounts allowed per application.")

    parsed = []
    used_labels = set()
    used_paths = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_label = str(item.get("label", "")).strip()
        raw_path = str(item.get("mount_path", "") or item.get("path", "")).strip()
        if not raw_label and not raw_path:
            continue
        if not LABEL_RE.fullmatch(raw_label):
            raise HTTPException(400, f"Storage mount label '{raw_label}' must be 1-32 lowercase alphanumeric characters, dashes, or underscores.")
        if raw_label in used_labels:
            raise HTTPException(400, f"Duplicate storage mount label '{raw_label}'.")
        norm_path = posixpath.normpath(raw_path)
        if not norm_path.startswith("/") or ".." in norm_path or len(norm_path) > 512:
            raise HTTPException(400, f"Storage mount path '{raw_path}' must be a clean absolute POSIX path.")
        if norm_path in {"/", "/proc", "/sys", "/dev", "/etc", "/var", "/tmp"}:
            raise HTTPException(400, f"Cannot mount storage directly to system root path '{norm_path}'.")
        for existing in used_paths:
            if norm_path == existing:
                raise HTTPException(400, f"Duplicate storage mount path '{norm_path}'.")
            if norm_path.startswith(existing.rstrip("/") + "/") or existing.startswith(norm_path.rstrip("/") + "/"):
                raise HTTPException(400, f"Storage mount path '{norm_path}' overlaps with '{existing}'. Nested mounts are not allowed.")
        used_labels.add(raw_label)
        used_paths.add(norm_path)
        volume_name = f"srv-container-app-{app_id}-vol-{raw_label}"
        parsed.append({
            "label": raw_label,
            "volume": volume_name,
            "mount_path": norm_path,
        })
    return _json.dumps(parsed) if parsed else None


def validate_custom_start_command(command: str | None) -> str | None:
    if not command or not command.strip():
        return None
    val = command.strip()
    try:
        tokens = shlex.split(val)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid custom start command syntax: {exc}") from exc
    if not tokens:
        return None
    return val


def parse_build_secret_keys(value: str | list[str] | None) -> str | None:
    import json as _json
    from services.apps_engine import build_secrets
    try:
        keys = build_secrets.parse_requested_keys(value)
    except (TypeError, ValueError, _json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _json.dumps(keys) if keys is not None else None


async def create_app(
    db: AsyncSession, *, domain: Domain, source_type: str, build_mode: str,
    repository_url: str | None, branch: str | None, image_reference: str | None,
    internal_port: int, ssl_requested: bool, environment_values: dict[str, str],
    database_mode: str = "none", database_url: str | None = None,
    database_attachments: list[dict[str, str]] | None = None,
    git_ref: str | None = None, git_ref_type: str = "branch",
    draft_key_id: str | None = None, root_directory: str = "",
    dockerfile_path: str = "Dockerfile", build_args: str | dict | None = None,
    custom_start_command: str | None = None, storage_mounts: list[dict] | str | None = None,
    health_path: str = "/", startup_timeout_seconds: int = 45,
    build_secret_keys: str | list[str] | None = None,
    deploy_type: str = "railpack", stack_catalog_id: str | None = None,
    stack_version: str | None = None,
) -> ContainerApp:
    ref_val = (git_ref or branch or "main").strip()
    ref_type_val = (git_ref_type or "branch").strip().lower()
    existing_app = await db.scalar(select(ContainerApp).where(ContainerApp.domain_id == domain.id))
    if existing_app is not None:
        if existing_app.status in ("pending", "failed", "delete_failed", "data_preserved", "deleting"):
            from services import container_app_cleanup_service
            await container_app_cleanup_service.delete_app(db, existing_app, keep_database_ids=[], keep_app_volume=False)
            await db.flush()
        else:
            raise HTTPException(409, "This domain already has an active container app.")
    # Docker resources cannot participate in the database transaction below.
    # Refuse an unsafe deployment before creating managed services, so a guard
    # rejection cannot leave a container whose app row was rolled back.
    from services.resource_guard_service import resource_guard_service
    profile = "image_pull" if source_type == "image" or deploy_type == "official_stack" else "build_large"
    preflight = await resource_guard_service.preflight(db, profile)
    if not preflight["ok"] and "build is already running" not in preflight["reason"].lower():
        raise HTTPException(409, f"Resource Guard blocked deployment before creating resources: {preflight['reason']}")
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
        deploy_type=deploy_type,
        stack_catalog_id=stack_catalog_id,
        stack_version=stack_version,
        repository_url=None if is_image else repository_url,
        branch=None if is_image else ref_val,
        git_ref=None if is_image else ref_val,
        git_ref_type="branch" if is_image else ref_type_val,
        image_reference=validate_image_reference(image_reference or "") if is_image else None,
        container_name="pending", internal_port=port,
        host_port=await next_host_port(db), env_path="pending", ssl_requested=ssl_requested,
        database_mode="none", database_provider=None,
        root_directory=validate_root_directory(root_directory),
        dockerfile_path=validate_dockerfile_path(dockerfile_path),
        build_args=parse_build_args(build_args),
        build_secret_keys=parse_build_secret_keys(build_secret_keys),
        custom_start_command=validate_custom_start_command(custom_start_command),
        health_path=validate_health_path(health_path),
        startup_timeout_seconds=validate_startup_timeout(startup_timeout_seconds),
    )
    db.add(app)
    await db.flush()
    app.container_name, app.env_path = f"srv-container-app-{app.id}", str(env_path(app.id))
    app.storage_mounts = parse_storage_mounts(app.id, storage_mounts)
    if draft_key_id:
        pub_key, key_path = repository_service.attach_deploy_key(draft_key_id, app.id)
        app.deploy_key_public = pub_key
        app.deploy_key_path = str(key_path)
    try:
        attachments = await databases.create_attachments(db, app, attachment_specs)
        databases.rebuild_environment(app, attachments, database_values)
    except Exception:
        if draft_key_id:
            repository_service.delete_deploy_key(app.id)
        raise
    return app


def _validate_source(
    domain: Domain, source_type: str, build_mode: str, repository_url: str | None,
    branch: str | None, image_reference: str | None, git_ref_type: str = "branch",
    has_deploy_key: bool = False,
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
    repository_service.validate_source(repository_url or "", branch or "main", git_ref_type)
    if has_deploy_key:
        repo = (repository_url or "").strip()
        if not (repo.startswith("git@") or repo.startswith("ssh://")):
            raise HTTPException(400, "SSH deploy keys require an SSH repository URL (e.g. git@github.com:owner/repo.git).")


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
    if mode == "panel_mariadb":
        if "MYSQL_URL" in values or database_url:
            raise HTTPException(400, "Panel MariaDB creates this app's MYSQL_URL automatically.")
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
    if mode == "panel_mariadb":
        return [{"kind": "mariadb", "provider": "panel_mariadb", "environment_key": "MYSQL_URL"}]
    if mode == "external" and database_url:
        return [{"kind": "postgresql", "provider": "external", "environment_key": "DATABASE_URL", "external_url": database_url}]
    raise HTTPException(400, "Choose no database or provide an external DATABASE_URL.")


def clear_auto_detected_fields(
    app: "ContainerApp",
    new_repository_url: str | None,
    new_branch: str | None,
) -> None:
    """Clear auto-detected fields when the source URL or branch changes.

    Fields set explicitly by the user (tracked externally) are NOT cleared.
    Low-confidence database specs in pending_database_specs are removed.
    """
    import json as _json

    source_changed = (new_repository_url and new_repository_url != app.repository_url)
    branch_changed = (new_branch and new_branch != app.branch)

    if not source_changed and not branch_changed:
        return

    # Clear fields that were populated by auto-detection
    if source_changed:
        # Build mode should be re-detected from the new source
        app.build_mode = "railpack"

    # Clear any pending database specs with LOW confidence (source-change invalidates them)
    if app.pending_database_specs:
        try:
            specs = _json.loads(app.pending_database_specs)
            if isinstance(specs, list):
                high_specs = [
                    s for s in specs
                    if isinstance(s, dict) and s.get("confidence", "HIGH") in ("HIGH", "MEDIUM")
                ]
                app.pending_database_specs = _json.dumps(high_specs) if high_specs else None
        except (ValueError, TypeError):
            app.pending_database_specs = None


# Backwards-compatible private aliases used by cleanup and focused tests.
_root = root
_env_path = env_path
_write_env = write_env
