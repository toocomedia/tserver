"""Immutable configuration snapshots for Railpack build-then-swap deployments."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import secrets
import shutil
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_database import ContainerAppDatabase
from models.container_app_secret import ContainerAppCredential
from models.container_app_snapshot import ContainerAppSnapshot
from services import container_app_service as apps
from services import container_app_image_inspect_service
from services.apps_engine import build_secrets, secret_vault, snapshot_envelope
from services.apps_engine.app_spec import AppSpec
from services.apps_engine.app_spec_codec import app_spec_to_dict
from dependencies.git import repository_service


CONFIG_FIELDS = (
    "source_type", "build_mode", "repository_url", "branch", "image_reference", "internal_port",
    "data_volume", "data_mount_path", "storage_mounts", "git_ref", "git_ref_type", "deploy_key_path",
    "root_directory", "dockerfile_path", "build_args", "build_secret_keys", "custom_start_command",
    "health_path", "startup_timeout_seconds", "database_mode", "database_provider", "database_name",
    "database_user", "preset", "wordpress_content_volume", "wordpress_site_title", "wordpress_admin_user",
    "wordpress_admin_email", "cpu_limit", "memory_limit_mb", "pid_limit", "ssl_requested",
    "deploy_type", "stack_catalog_id", "stack_version", "stack_services",
)


def _read_environment(app: ContainerApp) -> dict[str, str]:
    path = Path(app.env_path)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip():
            values[key.strip()] = value.strip()
    return values


def _source_identity(config: dict[str, Any]) -> str:
    if config.get("deploy_type") == "official_stack" or config.get("stack_catalog_id"):
        manifest = str(config.get("stack_services") or "")
        manifest_hash = hashlib.sha256(manifest.encode("utf-8")).hexdigest()[:16] if manifest else "legacy"
        return f"stack:{config.get('stack_catalog_id')}@{config.get('stack_version') or 'default'}:{manifest_hash}"
    if config.get("source_type") == "image":
        return f"image:{config.get('image_reference') or ''}"
    ref = config.get("git_ref") or config.get("branch") or "main"
    return f"git:{config.get('repository_url') or ''}@{ref}"


def _resolve_git_revision(app: ContainerApp, config: dict[str, Any]) -> str:
    ref = str(config.get("git_ref") or config.get("branch") or "main")
    ref_type = str(config.get("git_ref_type") or "branch")
    stable_current = (
        app.deployed_revision and ref == (app.git_ref or app.branch or "main")
        and ref_type == (app.git_ref_type or "branch")
    )
    if stable_current:
        return app.deployed_revision
    target = apps.root(app.id) / "snapshots" / f"resolve-{secrets.token_hex(8)}" / "source"
    try:
        checkout = repository_service.clone(
            str(config.get("repository_url") or ""), ref, target,
            git_ref_type=ref_type, ssh_key_path=app.deploy_key_path, allow_default_branch=False,
        )
        return checkout.revision.sha
    finally:
        shutil.rmtree(target.parent, ignore_errors=True)


def _fingerprint(config: dict[str, Any], environment: dict[str, str], versions: dict[str, int]) -> str:
    private_hash = hashlib.sha256(json.dumps(environment, sort_keys=True).encode("utf-8")).hexdigest()
    data = {"config": config, "environment_hash": private_hash, "secret_versions": versions}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _clean_requirement(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    key = str(item.get("key") or "").strip()
    if not key or not build_secrets.ENV_KEY_RE.fullmatch(key):
        raise ValueError("Secret requirements must use safe uppercase environment names.")
    purpose = str(item.get("purpose") or "Application secret").strip()[:255]
    if not item.get("generator"):
        raise ValueError("Secret requirement generator is required.")
    generator = str(item.get("generator")).strip()
    if generator not in {"urlsafe64", "base64_32", "base64_48", "base64_64", "hex32", "hex64", "password"}:
        raise ValueError("Secret requirement generator is not supported.")
    result: dict[str, Any] = {"key": key, "purpose": purpose, "generator": generator, "rotate": bool(item.get("rotate"))}
    credential = item.get("credential")
    if credential is not None:
        if not isinstance(credential, dict):
            raise ValueError("Credential metadata is invalid.")
        username = str(credential.get("username") or "").strip()
        label = str(credential.get("label") or "Access credentials").strip()
        if not username:
            raise ValueError("Generated access credential requires a documented username.")
        result["credential"] = {"username": username[:255], "label": label[:128]}
    return result


def normalize_secret_requirements(values: object) -> list[dict[str, Any]]:
    if values in (None, []):
        return []
    if not isinstance(values, list) or len(values) > 32:
        raise ValueError("Secret requirements must be a list of at most 32 names.")
    return [item for raw in values if (item := _clean_requirement(raw)) is not None]


async def create_snapshot(
    db: AsyncSession,
    app: ContainerApp,
    *,
    config_patch: dict[str, Any] | None = None,
    environment_patch: dict[str, str] | None = None,
    secret_requirements: list[dict[str, Any]] | None = None,
    state: str = "pending",
    plan_id: str | None = None,
    created_by_user_id: int | None = None,
    app_spec: AppSpec | None = None,
) -> tuple[ContainerAppSnapshot, list[dict[str, Any]]]:
    """Capture complete deployment input. Generated values remain encrypted server-side."""
    if app_spec is not None and config_patch:
        raise ValueError("Compose AppSpec snapshots cannot include legacy configuration patches.")
    config = {field: copy.deepcopy(getattr(app, field, None)) for field in CONFIG_FIELDS}
    for key, value in (config_patch or {}).items():
        if key not in CONFIG_FIELDS:
            raise ValueError(f"Unsupported App Engine configuration field: {key}.")
        config[key] = value
    attachments = list((await db.scalars(select(ContainerAppDatabase).where(
        ContainerAppDatabase.app_id == app.id,
    ).order_by(ContainerAppDatabase.id))).all())
    config["database_attachments"] = [
        {"id": item.id, "kind": item.kind, "provider": item.provider, "environment_key": item.environment_key,
         "database_name": item.database_name, "username": item.username, "status": item.status}
        for item in attachments
    ]
    environment = _read_environment(app)
    for key, value in (environment_patch or {}).items():
        if not build_secrets.ENV_KEY_RE.fullmatch(key) or not isinstance(value, str):
            raise ValueError("Environment patch contains an invalid value.")
        if value == "" or value.lower() in ("__delete__", "__unset__", "null", "none"):
            environment.pop(key, None)
        else:
            environment[key] = value

    if app_spec is not None:
        from services.apps_engine.security_policy import validate_app_spec
        app_spec = validate_app_spec(app_spec)
        secret_requirements = [
            {
                "key": item.key,
                "purpose": item.purpose,
                "generator": item.generator,
                "rotate": item.rotate,
            }
            for item in app_spec.required_secrets
        ]
    requirements = normalize_secret_requirements(secret_requirements)
    versions: dict[str, int] = {}
    statuses = [{"key": item["key"], "purpose": item["purpose"], "status": "pending_approval"} for item in requirements]

    source_revision = None
    image_digest = None
    if app_spec is None and config.get("source_type") == "git":
        source_revision = await asyncio.to_thread(_resolve_git_revision, app, config)
    elif app_spec is None and config.get("source_type") == "image":
        if config.get("deploy_type") == "official_stack" and config.get("stack_catalog_id"):
            from services.official_stacks.catalog import get_stack
            from services.official_stacks.manifest_validator import compute_stack_manifest_hash
            stk = get_stack(config.get("stack_catalog_id"))
            if stk:
                image_digest = compute_stack_manifest_hash(stk, str(config.get("stack_version") or stk.default_version))
        elif app.image_digest and config.get("image_reference") == app.image_reference:
            image_digest = app.image_digest
        else:
            try:
                insp = await container_app_image_inspect_service.inspect_image(
                    str(config.get("image_reference") or ""),
                )
                image_digest = insp.get("digest") if isinstance(insp, dict) else None
            except Exception:
                image_digest = None
    revision = int(getattr(app, "configuration_revision", 1) or 1) + (1 if state == "pending" else 0)
    stored_config = snapshot_envelope.compose_envelope(app_spec) if app_spec is not None else config
    fingerprint = _fingerprint(stored_config, environment, versions)
    snapshot = ContainerAppSnapshot(
        app_id=app.id,
        state=state,
        configuration_revision=revision,
        source_identity=f"appspec:{app_spec.name}" if app_spec is not None else _source_identity(config),
        source_revision=source_revision,
        image_digest=image_digest,
        config_json=json.dumps(stored_config, sort_keys=True),
        environment_encrypted=secret_vault.encrypt(json.dumps(environment, sort_keys=True)),
        secret_versions_json=json.dumps(versions, sort_keys=True),
        secret_requirements_json=json.dumps(requirements, sort_keys=True),
        fingerprint=fingerprint,
        plan_id=plan_id,
        created_by_user_id=created_by_user_id,
    )
    db.add(snapshot)
    await db.flush()
    if state == "pending":
        app.pending_snapshot_id = snapshot.id
    elif state == "active":
        app.active_snapshot_id = snapshot.id
    return snapshot, statuses


async def baseline_snapshot(db: AsyncSession, app: ContainerApp) -> ContainerAppSnapshot:
    if app.active_snapshot_id:
        snapshot = await db.get(ContainerAppSnapshot, app.active_snapshot_id)
        if snapshot:
            return snapshot
    snapshot, _ = await create_snapshot(db, app, state="active")
    return snapshot


async def get_snapshot(db: AsyncSession, app: ContainerApp, snapshot_id: int | None = None) -> ContainerAppSnapshot:
    target = snapshot_id or app.pending_snapshot_id or app.active_snapshot_id
    snapshot = await db.get(ContainerAppSnapshot, target) if target else None
    if snapshot is None or snapshot.app_id != app.id:
        if snapshot_id:
            raise ValueError("Deployment snapshot was not found for this app.")
        snapshot = await baseline_snapshot(db, app)
    return snapshot


def runtime_app(app: ContainerApp, snapshot: ContainerAppSnapshot) -> SimpleNamespace:
    """Detached runtime view prevents an unverified candidate changing active settings."""
    values = {column.name: copy.deepcopy(getattr(app, column.name)) for column in ContainerApp.__table__.columns}
    stored = snapshot_envelope.decode(snapshot.config_json)
    is_compose = snapshot_envelope.runtime_kind(snapshot.config_json) == snapshot_envelope.COMPOSE_RUNTIME_KIND
    if is_compose:
        spec = snapshot_envelope.app_spec(snapshot.config_json)
        values.update({
            "deploy_type": "app_spec",
            "stack_catalog_id": spec.name,
            "stack_version": spec.default_version,
            "stack_services": json.dumps(app_spec_to_dict(spec), sort_keys=True),
            "source_type": "image",
            "build_mode": "image",
            "image_reference": spec.services[spec.web_service_name].pinned_digest
                or spec.services[spec.web_service_name].image_reference,
            "internal_port": spec.web_port,
            "health_path": spec.web_health_path or "disabled",
            "startup_timeout_seconds": spec.startup_timeout_seconds,
            "image_digest": snapshot.image_digest,
        })
    else:
        values.update(stored)
    if not is_compose and values.get("source_type") == "git":
        values["deployed_revision"] = snapshot.source_revision
    elif not is_compose and snapshot.image_digest:
        values["image_reference"] = snapshot.image_digest
        values["image_digest"] = snapshot.image_digest
    return SimpleNamespace(**values)


async def materialize_environment(db: AsyncSession, app: ContainerApp, snapshot: ContainerAppSnapshot) -> None:
    environment = json.loads(secret_vault.decrypt(snapshot.environment_encrypted))
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
        raise RuntimeError("Snapshot environment is invalid.")
    apps.write_env(Path(app.env_path), environment)


async def bind_deferred_secrets(
    db: AsyncSession, app: ContainerApp, snapshot: ContainerAppSnapshot,
) -> list[dict[str, Any]]:
    """Generate named values only in the approved deployment worker, then bind them to this snapshot."""
    requirements = normalize_secret_requirements(json.loads(snapshot.secret_requirements_json or "[]"))
    if not requirements:
        return []
    environment = json.loads(secret_vault.decrypt(snapshot.environment_encrypted))
    versions = json.loads(snapshot.secret_versions_json or "{}")
    statuses: list[dict[str, Any]] = []
    for item in requirements:
        key = item["key"]
        if key in versions and key in environment:
            statuses.append({"key": key, "purpose": item["purpose"], "status": "bound", "version": versions[key]})
            continue
        record, created = await secret_vault.ensure_secret(
            db, app.id, key, item["purpose"], rotate=item["rotate"], generator=item["generator"],
        )
        environment[key] = await secret_vault.secret_value(db, record.id)
        versions[key] = record.version
        statuses.append({"key": key, "purpose": item["purpose"], "status": "created" if created else "reused", "version": record.version})
        if item.get("credential") and created:
            credential = item["credential"]
            await secret_vault.create_credential(db, app.id, credential["label"], credential["username"], record.id)
    snapshot.environment_encrypted = secret_vault.encrypt(json.dumps(environment, sort_keys=True))
    snapshot.secret_versions_json = json.dumps(versions, sort_keys=True)
    refresh_fingerprint(snapshot)
    return statuses


def bind_stack_manifest(snapshot: ContainerAppSnapshot, runtime: SimpleNamespace, manifest: str) -> None:
    """Persist a resolved Compose manifest before it can be retried or rolled back."""
    config = json.loads(snapshot.config_json)
    config["stack_services"] = manifest
    snapshot.config_json = json.dumps(config, sort_keys=True)
    snapshot.source_identity = _source_identity(config)
    refresh_fingerprint(snapshot)
    runtime.stack_services = manifest


def refresh_fingerprint(snapshot: ContainerAppSnapshot) -> None:
    config = json.loads(snapshot.config_json)
    environment = json.loads(secret_vault.decrypt(snapshot.environment_encrypted))
    versions = json.loads(snapshot.secret_versions_json or "{}")
    snapshot.fingerprint = _fingerprint(config, environment, versions)


async def credentials_for(db: AsyncSession, app_id: int) -> list[ContainerAppCredential]:
    return list((await db.scalars(select(ContainerAppCredential).where(
        ContainerAppCredential.app_id == app_id,
    ).order_by(ContainerAppCredential.id.desc()))).all())
