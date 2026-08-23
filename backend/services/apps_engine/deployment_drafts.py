"""Validate AI proposals, then turn approved Railpack drafts into pending snapshots."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_database import ContainerAppDatabase
from models.container_app_snapshot import ContainerAppSnapshot
from plugins.ai_helper.services import action_plans
from services import container_app_database_service as databases
from services import container_app_service as apps
from services.apps_engine import build_secrets, snapshots


PATCH_FIELDS = {
    "git_ref", "git_ref_type", "build_mode", "image_reference", "root_directory", "dockerfile_path",
    "build_args", "build_secret_keys", "custom_start_command", "internal_port", "health_path",
    "startup_timeout_seconds", "storage_mounts",
}
SENSITIVE_PARTS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "PRIVATE_KEY", "SALT", "JWT", "KEY_BASE")


def source_identity(app: ContainerApp) -> str:
    if app.source_type == "image":
        return f"image:{app.image_digest or app.image_reference or ''}"
    return f"git:{app.repository_url or ''}@{app.deployed_revision or app.git_ref or app.branch or 'main'}"


def _normalize_patch(app: ContainerApp, patch: object) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise ValueError("AI proposal must contain at least one supported configuration change.")
    if set(patch) - PATCH_FIELDS:
        raise ValueError("AI proposal includes unsupported configuration fields.")
    result: dict[str, Any] = {}
    for key, raw in patch.items():
        if key == "git_ref":
            ref = str(raw or "").strip()
            apps.repository_service.validate_source(app.repository_url or "", ref, str(patch.get("git_ref_type") or app.git_ref_type or "branch"))
            result[key] = ref
        elif key == "git_ref_type":
            if raw not in {"branch", "tag", "commit"}:
                raise ValueError("Git reference type is invalid.")
            result[key] = raw
        elif key == "build_mode":
            if raw not in {"railpack", "dockerfile", "image"}:
                raise ValueError("Build mode is invalid.")
            result[key] = raw
        elif key == "image_reference":
            result[key] = apps.validate_image_reference(str(raw or ""))
        elif key == "root_directory":
            result[key] = apps.validate_root_directory(str(raw or ""))
        elif key == "dockerfile_path":
            result[key] = apps.validate_dockerfile_path(str(raw or ""))
        elif key == "build_args":
            result[key] = apps.parse_build_args(raw)
        elif key == "build_secret_keys":
            result[key] = apps.parse_build_secret_keys(raw)
        elif key == "custom_start_command":
            result[key] = apps.validate_custom_start_command(str(raw or ""))
        elif key == "internal_port":
            result[key] = apps.validate_port(int(raw))
        elif key == "health_path":
            result[key] = apps.validate_health_path(str(raw or ""))
        elif key == "startup_timeout_seconds":
            result[key] = apps.validate_startup_timeout(int(raw))
        elif key == "storage_mounts":
            result[key] = apps.parse_storage_mounts(app.id, raw)
    if result.get("build_mode") == "image" and app.source_type != "image":
        raise ValueError("Switching a Git app to registry-image mode requires a new App Engine app.")
    return result


def _nonsecret_environment(values: object) -> dict[str, str]:
    if values in (None, {}):
        return {}
    if not isinstance(values, dict) or len(values) > 64:
        raise ValueError("Non-secret environment values are invalid.")
    clean: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key).strip()
        if not build_secrets.ENV_KEY_RE.fullmatch(key):
            raise ValueError("Environment key is invalid.")
        if key == "DATABASE_URL" or any(part in key for part in SENSITIVE_PARTS):
            raise ValueError(f"{key} must be declared as a secret requirement, never sent to AI as a value.")
        value = str(raw_value)
        if len(value) > 4096 or "\n" in value or "\r" in value:
            raise ValueError("Environment value is invalid.")
        clean[key] = value
    return clean


def proposal_payload(
    app: ContainerApp, *, patch: object, environment_values: object,
    secret_requirements: object, evidence: object, confidence: float,
) -> dict[str, Any]:
    clean_patch = _normalize_patch(app, patch)
    clean_secrets = snapshots.normalize_secret_requirements(secret_requirements)
    if not isinstance(evidence, list) or not evidence or len(evidence) > 20:
        raise ValueError("AI proposal must include concise source or log evidence.")
    clean_evidence = [str(item)[:800] for item in evidence if isinstance(item, str) and item.strip()]
    if not clean_evidence:
        raise ValueError("AI proposal must include concise source or log evidence.")
    return {
        "app_id": app.id,
        "base_configuration_revision": int(app.configuration_revision or 1),
        "source_identity": source_identity(app),
        "patch": clean_patch,
        "environment_values": _nonsecret_environment(environment_values),
        "secret_requirements": clean_secrets,
        "evidence": clean_evidence,
        "confidence": max(0.0, min(1.0, float(confidence))),
    }


async def apply_plan(
    db: AsyncSession, app: ContainerApp, plan_id: str, user_id: int | None,
) -> tuple[int, list[dict[str, Any]]]:
    plan = await action_plans.get_action_plan(db, plan_id, user_id=user_id)
    if not plan or plan["action_type"] != "container_app_patch":
        raise ValueError("Deployment draft was not found or does not belong to you.")
    if plan["status"] != "awaiting_approval" or plan["is_expired"]:
        raise ValueError("Deployment draft is no longer awaiting approval.")
    payload = plan["payload"]
    if payload.get("app_id") != app.id:
        raise ValueError("Deployment draft belongs to another app.")
    if payload.get("base_configuration_revision") != int(app.configuration_revision or 1):
        raise ValueError("App settings changed after this draft. Review a new AI plan.")
    if payload.get("source_identity") != source_identity(app):
        raise ValueError("Selected source changed after this draft. Review a new AI plan.")
    patch = _normalize_patch(app, payload.get("patch"))
    environment = _nonsecret_environment(payload.get("environment_values"))

    raw_attachments = payload.get("database_attachments") or []
    if raw_attachments:
        parsed = databases.parse_specs(raw_attachments)
        existing = list((await db.scalars(select(ContainerAppDatabase).where(
            ContainerAppDatabase.app_id == app.id,
        ))).all())
        existing_kinds = {item.kind for item in existing}
        create = [item for item in parsed if item["kind"] not in existing_kinds]
        if create:
            await databases.create_attachments(db, app, create)
            merged = await databases.attachments_for(db, app.id)
            current = databases.read_app_environment(app)
            databases.rebuild_environment(app, merged, current)

    snapshot, statuses = await snapshots.create_snapshot(
        db, app, config_patch=patch, environment_patch=environment,
        secret_requirements=payload.get("secret_requirements") or [], plan_id=plan_id,
        created_by_user_id=user_id,
    )
    repeated_failure = await db.scalar(select(ContainerAppSnapshot.id).where(
        ContainerAppSnapshot.app_id == app.id,
        ContainerAppSnapshot.state == "failed",
        ContainerAppSnapshot.fingerprint == snapshot.fingerprint,
    ))
    if repeated_failure:
        snapshot.state = "discarded"
        app.pending_snapshot_id = None
        raise ValueError("AI draft matches a previously failed snapshot. Review new evidence or change settings before deploying.")
    await action_plans.mark_plan_applied(
        db, plan_id, user_id, expected_hash=plan["payload_hash"], expected_action_type="container_app_patch",
    )
    return snapshot.id, statuses
