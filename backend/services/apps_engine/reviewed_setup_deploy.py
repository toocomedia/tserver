"""Deploy an AI-reviewed App Engine setup plan after one explicit user click."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Domain
from models.ssl_cert import SslCert
from plugins.ai_helper.services import action_plans
from services import container_app_deployment_service, container_app_service
from services.apps_engine import secret_vault, snapshots
from services.official_stacks import compose_runtime
from services.official_stacks.manifest_validator import compute_stack_manifest_hash, validate_stack_manifest
from services.official_stacks.schema import stack_from_dict


async def deploy_plan(
    db: AsyncSession, plan_id: str, *, user_id: int | None, ssl_requested: bool = True,
) -> tuple[int, int]:
    """Validate, consume, create candidate snapshot, and queue deployment once."""
    plan = await action_plans.get_action_plan(db, plan_id, user_id=user_id)
    if not plan or not action_plans.payload_is_intact(plan) or plan["status"] != "awaiting_approval":
        raise HTTPException(400, "Reviewed setup plan is unavailable, expired, or already used.")
    action_type = str(plan.get("action_type") or "")
    if action_type in {"stack_install", "official_stack_install"}:
        return await _deploy_stack(db, plan, user_id=user_id, ssl_requested=ssl_requested)
    if action_type == "app_install":
        return await _deploy_app(db, plan, user_id=user_id, ssl_requested=ssl_requested)
    raise HTTPException(400, "Reviewed setup plan type is not deployable.")


async def _domain_from_payload(db: AsyncSession, payload: dict[str, Any]) -> Domain:
    domain_name = str(payload.get("domain_name") or "").strip().lower()
    if not domain_name:
        raise HTTPException(400, "Reviewed setup plan has no target domain.")
    domain = await db.scalar(select(Domain).where(func.lower(Domain.name) == domain_name))
    if domain is None:
        raise HTTPException(404, f"Target domain '{domain_name}' was not found.")
    return domain


async def _has_certificate(db: AsyncSession, domain: Domain) -> bool:
    return await db.scalar(select(SslCert.id).where(SslCert.full_domain == domain.name)) is not None


def _string_map(raw: Any, label: str, *, allowed: set[str] | None = None) -> dict[str, str]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(400, f"{label} must be a key/value object.")
    values: dict[str, str] = {}
    for key, value in raw.items():
        clean_key = str(key)
        if allowed is not None and clean_key not in allowed:
            continue
        values[clean_key] = str(value) if value is not None else ""
    return values


async def _deploy_stack(
    db: AsyncSession, plan: dict[str, Any], *, user_id: int | None, ssl_requested: bool,
) -> tuple[int, int]:
    payload = plan.get("payload") or {}
    domain = await _domain_from_payload(db, payload)
    try:
        stack = validate_stack_manifest(stack_from_dict(payload.get("stack_manifest") or {}))
        version = str(payload.get("stack_version") or stack.default_version)
        if payload.get("stack_catalog_id") != stack.catalog_id:
            raise ValueError("Stack review plan does not match its server manifest.")
        if payload.get("manifest_hash") != compute_stack_manifest_hash(stack, version):
            raise ValueError("Stack review plan manifest hash is invalid.")
        settings = _string_map(payload.get("nonsecret_settings"), "Stack settings", allowed=set(stack.allowed_nonsecret_settings))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        secret_vault.encrypt("")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc

    has_certificate = await _has_certificate(db, domain)
    app = await container_app_service.create_app(
        db, domain=domain, source_type="image", build_mode="image",
        deploy_type="official_stack", stack_catalog_id=stack.catalog_id, stack_version=version,
        stack_services=compose_runtime.manifest_json(stack),
        repository_url=stack.official_repositories[0] if stack.official_repositories else None,
        branch=version, image_reference=stack.services[stack.web_service_name].image_reference,
        internal_port=stack.web_internal_port,
        ssl_requested=ssl_requested and not has_certificate,
        environment_values=settings,
        health_path=stack.web_health_path or "disabled",
        startup_timeout_seconds=stack.startup_timeout_seconds,
    )
    container_app_service.write_env(Path(app.env_path), settings)
    await snapshots.create_snapshot(
        db, app, environment_patch=settings, plan_id=plan["plan_id"],
        created_by_user_id=user_id,
    )
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
    try:
        await action_plans.mark_plan_applied(
            db, plan["plan_id"], user_id=user_id, expected_hash=plan["payload_hash"],
            expected_action_type=plan["action_type"],
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return app.id, deployment.id


async def _deploy_app(
    db: AsyncSession, plan: dict[str, Any], *, user_id: int | None, ssl_requested: bool,
) -> tuple[int, int]:
    payload = plan.get("payload") or {}
    domain = await _domain_from_payload(db, payload)
    source_type = str(payload.get("source_type") or "image")
    build_mode = str(payload.get("build_mode") or ("image" if source_type == "image" else "railpack"))
    try:
        port = int(payload.get("internal_port") or 3000)
        requested_secrets = snapshots.normalize_secret_requirements(payload.get("secret_requirements") or [])
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if requested_secrets:
        try:
            secret_vault.encrypt("")
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    has_certificate = await _has_certificate(db, domain)
    app = await container_app_service.create_app(
        db, domain=domain, source_type=source_type, build_mode=build_mode,
        repository_url=str(payload.get("repository_url") or "").strip() or None,
        branch=str(payload.get("branch") or "main").strip() or "main",
        image_reference=str(payload.get("image_reference") or "").strip() or None,
        internal_port=port,
        ssl_requested=ssl_requested and not has_certificate,
        environment_values=_string_map(payload.get("environment_values"), "Environment values"),
        database_attachments=payload.get("database_attachments") or [],
        custom_start_command=str(payload.get("custom_start_command") or "").strip() or None,
        storage_mounts=payload.get("storage_mounts") or None,
        health_path=str(payload.get("health_path") or "disabled").strip() or "disabled",
    )
    await snapshots.create_snapshot(
        db, app, secret_requirements=requested_secrets, plan_id=plan["plan_id"],
        created_by_user_id=user_id,
    )
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
    try:
        await action_plans.mark_plan_applied(
            db, plan["plan_id"], user_id=user_id,
            expected_hash=plan["payload_hash"], expected_action_type="app_install",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return app.id, deployment.id
