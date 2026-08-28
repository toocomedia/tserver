"""Deploy an AI-reviewed App Engine setup plan after one explicit user click."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app_deployment import ContainerAppDeployment
from models.container_app_snapshot import ContainerAppSnapshot
from models.domain import Domain
from models.ssl_cert import SslCert
from plugins.ai_helper.services import action_plans
from services import container_app_deployment_service, container_app_inspection_service, container_app_service
from services.apps_engine import database_provider_capabilities, secret_vault, snapshots
from services.official_stacks import compose_runtime
from services.official_stacks.manifest_validator import compute_stack_manifest_hash, validate_stack_manifest
from services.official_stacks.schema import stack_from_dict


async def deploy_plan(
    db: AsyncSession, plan_id: str, *, user_id: int | None, ssl_requested: bool = True,
) -> tuple[int, int]:
    """Validate, consume, create candidate snapshot, and queue deployment once."""
    plan = await action_plans.get_action_plan(db, plan_id, user_id=user_id)
    if not plan or not action_plans.payload_is_intact(plan):
        raise HTTPException(400, "Reviewed setup plan is unavailable or invalid.")
    if plan["status"] in {"executing", "applied"}:
        existing = await _existing_result(db, plan_id)
        if existing:
            return existing
        raise HTTPException(409, "Reviewed setup plan is already executing.")
    if plan["status"] != "awaiting_approval":
        raise HTTPException(400, "Reviewed setup plan is expired or already used.")
    action_type = str(plan.get("action_type") or "")
    if action_type == "container_app_patch":
        return await _deploy_patch(db, plan, user_id=user_id)
    allowed = {"app_install", "stack_install", "official_stack_install", "app_spec_install"}
    if action_type not in allowed:
        raise HTTPException(400, "Reviewed setup plan type is not deployable.")
    try:
        await action_plans.begin_plan_execution(
            db, plan_id, user_id=user_id, expected_hash=plan["payload_hash"],
            expected_action_types=allowed,
        )
        if action_type in {"stack_install", "official_stack_install"}:
            result = await _deploy_stack(db, plan, user_id=user_id, ssl_requested=ssl_requested)
        elif action_type == "app_spec_install":
            result = await _deploy_app_spec(db, plan, user_id=user_id, ssl_requested=ssl_requested)
        else:
            result = await _deploy_app(db, plan, user_id=user_id, ssl_requested=ssl_requested)
        await action_plans.finish_plan_execution(
            db, plan_id, user_id=user_id, expected_hash=plan["payload_hash"],
        )
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def _existing_result(db: AsyncSession, plan_id: str) -> tuple[int, int] | None:
    snapshot = await db.scalar(select(ContainerAppSnapshot).where(
        ContainerAppSnapshot.plan_id == plan_id,
    ).order_by(ContainerAppSnapshot.id.desc()))
    if snapshot is None:
        return None
    deployment = await db.scalar(select(ContainerAppDeployment).where(
        ContainerAppDeployment.snapshot_id == snapshot.id,
    ).order_by(ContainerAppDeployment.id.desc()))
    return (snapshot.app_id, deployment.id) if deployment else None


async def _deploy_patch(db: AsyncSession, plan: dict[str, Any], *, user_id: int | None) -> tuple[int, int]:
    from models.container_app import ContainerApp
    from services.apps_engine import deployment_drafts
    payload = plan.get("payload") or {}
    app_id = payload.get("app_id")
    if not app_id:
        raise HTTPException(400, "Patch plan is missing app ID.")
    app = await db.get(ContainerApp, int(app_id))
    if not app:
        raise HTTPException(404, "App for this patch was not found.")
    snapshot_id, _ = await deployment_drafts.apply_plan(db, app, plan["plan_id"], user_id)
    deployment = await container_app_deployment_service.queue_deployment(
        db, app, action="deploy", snapshot_id=snapshot_id,
    )
    await db.commit()
    return app.id, deployment.id


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
    return app.id, deployment.id


async def _deploy_app_spec(
    db: AsyncSession, plan: dict[str, Any], *, user_id: int | None, ssl_requested: bool,
) -> tuple[int, int]:
    import hashlib
    import json

    from services.apps_engine.app_spec_codec import app_spec_to_dict
    from services.apps_engine.security_policy import validate_app_spec

    payload = plan.get("payload") or {}
    domain = await _domain_from_payload(db, payload)
    try:
        spec = validate_app_spec(payload.get("app_spec") or {})
        normalized = json.dumps(app_spec_to_dict(spec), sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(normalized.encode("utf-8")).hexdigest() != payload.get("app_spec_hash"):
            raise ValueError("AppSpec plan hash is invalid.")
        environment = _string_map(payload.get("environment_values"), "AppSpec environment")
        secret_vault.encrypt("")
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    has_certificate = await _has_certificate(db, domain)
    web_service = spec.services[spec.web_service_name]
    app = await container_app_service.create_app(
        db,
        domain=domain,
        source_type="image",
        build_mode="image",
        deploy_type="app_spec",
        stack_catalog_id=spec.name,
        stack_version=spec.default_version,
        image_reference=web_service.pinned_digest or web_service.image_reference,
        internal_port=spec.web_port,
        ssl_requested=ssl_requested and not has_certificate,
        environment_values=environment,
        health_path=spec.web_health_path or "disabled",
        startup_timeout_seconds=spec.startup_timeout_seconds,
    )
    await snapshots.create_snapshot(
        db,
        app,
        app_spec=spec,
        environment_patch=environment,
        plan_id=plan["plan_id"],
        created_by_user_id=user_id,
    )
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
    return app.id, deployment.id


async def _deploy_app(
    db: AsyncSession, plan: dict[str, Any], *, user_id: int | None, ssl_requested: bool,
) -> tuple[int, int]:
    payload = plan.get("payload") or {}
    domain = await _domain_from_payload(db, payload)
    source_type = str(payload.get("source_type") or "image")
    build_mode = str(payload.get("build_mode") or ("image" if source_type == "image" else "railpack"))
    _reject_unsafe_single_app_source(payload)
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
        database_attachments=_database_attachments(payload.get("database_attachments")),
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
    return app.id, deployment.id


def _database_attachments(raw: Any) -> list[dict[str, str]]:
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise HTTPException(400, "Database attachments are invalid.")
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(400, "Database attachments are invalid.")
        kind = _database_kind(str(item.get("kind") or "").strip().lower())
        provider = _database_provider(str(item.get("provider") or "docker").strip().lower(), kind)
        result.append({
            "kind": kind,
            "provider": provider,
            "environment_key": str(item.get("environment_key") or "").strip(),
            "external_url": str(item.get("external_url") or "").strip(),
            "supabase_project_id": str(item.get("supabase_project_id") or "").strip(),
        })
    return result


def _database_kind(kind: str) -> str:
    return {
        "postgres": "postgresql",
        "postgresql": "postgresql",
        "mysql": "mariadb",
        "mariadb": "mariadb",
        "mariadb/mysql": "mariadb",
        "mongo": "mongodb",
        "mongodb": "mongodb",
        "redis": "redis",
    }.get(kind, kind)


def _database_provider(provider: str, kind: str) -> str:
    try:
        return database_provider_capabilities.canonical_provider(kind, provider)
    except database_provider_capabilities.ProviderChoiceRequired as exc:
        raise HTTPException(400, str(exc)) from exc


def _reject_unsafe_single_app_source(payload: dict[str, Any]) -> None:
    if str(payload.get("source_type") or "").strip().lower() != "git":
        return
    repo = str(payload.get("repository_url") or "").strip()
    if not repo:
        return
    try:
        inspection = container_app_inspection_service.inspect_repository(repo, str(payload.get("branch") or "main").strip() or "main")
    except Exception:
        return
    compose_services = (inspection.get("compose_info") or {}).get("services") if isinstance(inspection, dict) else None
    kinds = _inspection_database_kinds(inspection)
    unsupported = sorted(kinds & {"clickhouse"})
    if compose_services or unsupported:
        reason = "Compose services" if compose_services else f"unsupported datastore services ({', '.join(unsupported)})"
        raise HTTPException(
            400,
            f"Reviewed setup plan is a single-app plan, but source inspection found {reason}. Create a restricted stack setup plan instead.",
        )


def _inspection_database_kinds(inspection: dict[str, Any]) -> set[str]:
    result = {str(item).strip().lower() for item in inspection.get("database_types") or []}
    for key in ("database_detections", "database_suggestions"):
        for item in inspection.get(key) or []:
            if isinstance(item, dict):
                result.add(str(item.get("kind") or "").strip().lower())
    return {item for item in result if item}
