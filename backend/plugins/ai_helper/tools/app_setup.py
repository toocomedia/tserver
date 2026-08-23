"""
tools/app_setup.py — Application source inspection and proposal generator for App Engine.
Generates immutable server-side AiActionPlan records for wizard autofill.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.services import action_plans
from models.container_app import ContainerApp
from services import container_app_image_inspect_service, container_app_inspection_service
from services.apps_engine import deployment_drafts, source_access

logger = logging.getLogger(__name__)

_SUPPORTED_GIT_BUILD_MODES = {"railpack", "dockerfile"}
_SUPPORTED_DATABASE_KINDS = {"postgres", "postgresql", "mariadb", "mysql", "redis", "mongodb", "sqlite", "supabase"}


def _install_mode(source_type: str, build_mode: str) -> tuple[str, str] | None:
    """Accept only deployment modes the single-container App Engine can create."""
    source = (source_type or "").strip().lower()
    mode = (build_mode or "railpack").strip().lower()
    if source == "image":
        return source, "image"
    if source == "git" and mode in _SUPPORTED_GIT_BUILD_MODES:
        return source, mode
    return None


async def inspect_app_source(
    db: AsyncSession,
    source_type: str = "image",
    repository_url: str = "",
    branch: str = "main",
    image_reference: str = "",
    app_id: int | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Runs native panel repository or registry image inspection.
    Detects runtime, build mode, internal ports, environment variables, and database needs.
    """
    if app_id is not None:
        app = await db.get(ContainerApp, app_id)
        if app is None:
            return {"status": "error", "message": "App Engine app was not found."}
        try:
            return source_access.inspect(app)
        except Exception as exc:
            return {"status": "error", "message": f"Source inspection failed: {exc}"}
    stype = (source_type or "").lower().strip()
    if stype == "git":
        repo = repository_url.strip()
        if not repo:
            return {"status": "error", "message": "Repository URL is required for Git inspection."}
        from services.official_stacks.source_detector import detect_official_stack
        stack_info = detect_official_stack(repo)
        if stack_info.get("is_official_stack"):
            return {
                "status": "ok",
                "source_type": "official_stack",
                "official_stack": stack_info,
                "message": f"{stack_info['name']} requires an Official Stack deployment ({stack_info['services_count']} services, {stack_info['recommended_ram_mb'] // 1024} GB RAM recommended).",
            }
        try:
            res = container_app_inspection_service.inspect_repository(repo, branch.strip() or "main")
            return {"status": "ok", "source_type": "git", "inspection": res}
        except Exception as exc:
            return {"status": "error", "message": f"Git inspection failed: {str(exc)}"}

    elif stype == "image":
        image = image_reference.strip()
        if not image:
            return {"status": "error", "message": "Image reference is required for Docker inspection."}
        from services.official_stacks.source_detector import detect_official_stack
        stack_info = detect_official_stack(image)
        if stack_info.get("is_official_stack"):
            return {
                "status": "ok",
                "source_type": "official_stack",
                "official_stack": stack_info,
                "message": f"{stack_info['name']} requires an Official Stack deployment ({stack_info['services_count']} services, {stack_info['recommended_ram_mb'] // 1024} GB RAM recommended).",
            }
        try:
            res = await container_app_image_inspect_service.inspect_image(image)
            return {"status": "ok", "source_type": "image", "inspection": res}
        except Exception as exc:
            return {"status": "error", "message": f"Docker image inspection failed: {str(exc)}"}

    return {"status": "error", "message": f"Unsupported source type '{source_type}'. Must be 'git' or 'image'."}


async def search_app_source(
    db: AsyncSession, app_id: int, query: str, max_results: int = 20, **kwargs: Any,
) -> Dict[str, Any]:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        return {"status": "error", "message": "App Engine app was not found."}
    try:
        return source_access.search(app, query, max_results)
    except Exception as exc:
        return {"status": "error", "message": f"Source search failed: {exc}"}


async def read_app_source_file(
    db: AsyncSession, app_id: int, file_path: str, max_chars: int = 12000, **kwargs: Any,
) -> Dict[str, Any]:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        return {"status": "error", "message": "App Engine app was not found."}
    try:
        return source_access.read_file(app, file_path, max_chars)
    except Exception as exc:
        return {"status": "error", "message": f"Source file read failed: {exc}"}


async def inspect_official_image(
    db: AsyncSession, image_reference: str, **kwargs: Any,
) -> Dict[str, Any]:
    try:
        inspection = await container_app_image_inspect_service.inspect_image(image_reference)
    except Exception as exc:
        return {"status": "error", "message": f"Image inspection failed: {exc}"}
    reference = str(inspection.get("reference") or "").lower().split("@", 1)[0].split(":", 1)[0]
    verified = reference in {"docker.umami.is/umami-software/umami"}
    return {
        "status": "ok", "inspection": inspection,
        "official_image": {
            "verified": verified,
            "evidence": [f"Registry digest: {inspection.get('digest')}"] + (
                ["Panel official-image allowlist matched."] if verified else
                ["No server-verifiable official provenance found. Do not prefill Image mode automatically."]
            ),
            "approval_required": True,
        },
    }


async def propose_container_app_patch(
    db: AsyncSession,
    app_id: int,
    patch: Dict[str, Any],
    evidence: List[str],
    environment_values: Optional[Dict[str, str]] = None,
    secret_requirements: Optional[List[Dict[str, Any]]] = None,
    database_attachments: Optional[List[Dict[str, str]]] = None,
    summary: str = "",
    confidence: float = 0.0,
    reasoning: str = "",
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    if user_id is None:
        return {"status": "error", "message": "AI deployment drafts require an authenticated panel user."}
    app = await db.get(ContainerApp, app_id)
    if app is None:
        return {"status": "error", "message": "App Engine app was not found."}
    try:
        payload = deployment_drafts.proposal_payload(
            app, patch=patch, environment_values=environment_values,
            secret_requirements=secret_requirements or [], evidence=evidence, confidence=confidence,
        )
        if database_attachments:
            payload["database_attachments"] = database_attachments
        plan = await action_plans.create_action_plan(
            db=db, session_id=session_id or "default_session", action_type="container_app_patch",
            payload=payload, summary=summary or f"Deployment changes for App Engine app {app.id}",
            confidence=confidence, reasoning=reasoning, user_id=user_id,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Could not create deployment draft: {exc}"}
    return {
        "status": "ok", "plan_id": plan.plan_id, "summary": plan.summary,
        "confidence": plan.confidence, "message": "Deployment draft saved. User must review and apply it from App page.",
    }


async def propose_app_install(
    db: AsyncSession,
    session_id: Optional[str] = None,
    source_type: str = "image",
    repository_url: str = "",
    branch: str = "main",
    image_reference: str = "",
    internal_port: int = 3000,
    build_mode: str = "railpack",
    custom_start_command: str = "",
    environment_values: Optional[Dict[str, str]] = None,
    secret_requirements: Optional[List[Dict[str, Any]]] = None,
    database_attachments: Optional[List[Dict[str, str]]] = None,
    storage_mounts: Optional[List[Dict[str, str]]] = None,
    domain_name: str = "",
    summary: str = "",
    confidence: float = 1.0,
    reasoning: str = "",
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Creates and saves a validated server-side AiActionPlan for application installation.
    Returns the opaque plan_id for UI action rendering.
    """
    if user_id is None:
        return {"status": "error", "message": "AI setup drafts require an authenticated panel user."}

    source_and_mode = _install_mode(source_type, build_mode)
    if source_and_mode is None:
        return {
            "status": "unsupported",
            "message": "App Engine supports one registry image or one Git app built by Railpack or Dockerfile. Docker Compose and multi-service stacks need a separate supported Compose deployment path.",
        }
    source_type, build_mode = source_and_mode

    try:
        port = int(internal_port)
        if port < 1 or port > 65535:
            port = 3000
    except (ValueError, TypeError):
        port = 3000

    clean_envs: Dict[str, str] = {}
    if isinstance(environment_values, dict):
        for k, v in environment_values.items():
            if isinstance(k, str) and k.strip():
                clean_envs[k.strip()] = str(v) if v is not None else ""

    # AI may name secret requirements, but must never receive or generate their values.
    cleaned_secrets = [
        {"key": key, "purpose": "Application secret"}
        for key in list(clean_envs)
        if any(s in key.upper() for s in ("SECRET", "SALT", "KEY_BASE", "JWT", "PASSWORD", "AUTH_KEY"))
    ]
    for item in cleaned_secrets:
        clean_envs.pop(item["key"], None)

    if isinstance(secret_requirements, list):
        known_secret_keys = {item["key"] for item in cleaned_secrets}
        for item in secret_requirements:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) or key in known_secret_keys:
                continue
            purpose = str(item.get("purpose") or "Application secret").strip()[:256]
            cleaned_secrets.append({"key": key, "purpose": purpose or "Application secret"})
            known_secret_keys.add(key)

    clean_dbs: List[Dict[str, str]] = []
    if isinstance(database_attachments, list):
        for item in database_attachments:
            if isinstance(item, dict) and item.get("kind"):
                kind = str(item.get("kind", "")).strip().lower()
                if kind not in _SUPPORTED_DATABASE_KINDS:
                    return {
                        "status": "unsupported",
                        "message": f"App Engine cannot attach '{kind}' to a single-container Railpack app. No setup draft was created.",
                    }
                clean_dbs.append({
                    "kind": kind,
                    "provider": str(item.get("provider", "docker")).strip().lower(),
                    "environment_key": str(item.get("environment_key", "DATABASE_URL")).strip(),
                })

    clean_mounts: List[Dict[str, str]] = []
    if isinstance(storage_mounts, list):
        for item in storage_mounts:
            if isinstance(item, dict) and item.get("mount_path"):
                raw_lbl = str(item.get("label", "data")).strip().lower()
                clean_lbl = re.sub(r"[^a-z0-9_-]+", "-", raw_lbl).strip("-_")[:32] or "data"
                clean_mounts.append({
                    "label": clean_lbl,
                    "mount_path": str(item.get("mount_path", "")).strip(),
                })

    payload = {
        "source_type": (source_type or "image").strip().lower(),
        "repository_url": repository_url.strip(),
        "branch": branch.strip() or "main",
        "image_reference": image_reference.strip(),
        "internal_port": port,
        "build_mode": (build_mode or "railpack").strip().lower(),
        "custom_start_command": (custom_start_command or "").strip(),
        "environment_values": clean_envs,
        "secret_requirements": cleaned_secrets,
        "database_attachments": clean_dbs,
        "storage_mounts": clean_mounts,
        "domain_name": domain_name.strip(),
    }

    sess_id = session_id or "default_session"
    plan_summary = summary or f"Install {image_reference or repository_url or 'Application'}"

    plan = await action_plans.create_action_plan(
        db=db,
        session_id=sess_id,
        action_type="app_install",
        payload=payload,
        summary=plan_summary,
        confidence=confidence,
        reasoning=reasoning,
        user_id=user_id,
    )

    return {
        "status": "ok",
        "plan_id": plan.plan_id,
        "summary": plan.summary,
        "confidence": plan.confidence,
        "message": "Setup draft created. Server will offer a safe setup handoff; it does not deploy the app.",
    }


async def propose_official_stack_install(
    db: AsyncSession,
    catalog_id: str = "",
    display_name: str = "",
    version: str = "",
    domain_name: str = "",
    services: Optional[Dict[str, Any]] = None,
    startup_order: Optional[List[str]] = None,
    web_service_name: str = "",
    web_internal_port: int = 8000,
    web_health_path: str = "/api/health",
    required_secrets: Optional[List[Dict[str, Any]]] = None,
    url_templates: Optional[Dict[str, str]] = None,
    default_environment: Optional[Dict[str, str]] = None,
    nonsecret_settings: Optional[Dict[str, str]] = None,
    recommended_ram_mb: int = 2048,
    post_install_message: str = "",
    session_id: Optional[str] = None,
    summary: str = "",
    confidence: float = 1.0,
    reasoning: str = "",
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Creates an immutable reviewed plan for a multi-container stack deployment dynamically analyzed by AI."""
    if user_id is None:
        return {"status": "error", "message": "AI setup drafts require an authenticated panel user."}

    import uuid
    from services.official_stacks.catalog import get_stack, register_stack
    from services.official_stacks.schema import stack_from_dict
    from services.official_stacks.manifest_validator import validate_stack_request

    cat_id = (catalog_id or "").strip() or f"stack_{uuid.uuid4().hex[:8]}"

    if services:
        stack_data = {
            "catalog_id": cat_id,
            "display_name": display_name.strip() or f"Stack ({cat_id})",
            "vendor_name": kwargs.get("vendor_name", "Vendor"),
            "description": kwargs.get("description", ""),
            "official_repositories": kwargs.get("official_repositories") or [],
            "allowed_versions": [version.strip()] if version.strip() else ["latest"],
            "default_version": version.strip() or "latest",
            "services": services,
            "startup_order": startup_order or list(services.keys()),
            "web_service_name": web_service_name or (startup_order[-1] if startup_order else list(services.keys())[0]),
            "web_internal_port": web_internal_port,
            "web_health_path": web_health_path,
            "required_secrets": required_secrets or [],
            "url_templates": url_templates or {},
            "default_environment": default_environment or {},
            "allowed_nonsecret_settings": list((nonsecret_settings or {}).keys()) or kwargs.get("allowed_nonsecret_settings", []),
            "recommended_ram_mb": recommended_ram_mb,
            "post_install_message": post_install_message,
        }
        stack = stack_from_dict(stack_data)
        register_stack(stack)
    else:
        stack = get_stack(cat_id)
        if stack is None:
            return {"status": "error", "message": f"Stack '{cat_id}' is not defined. Please provide the 'services' configuration."}

    v = version.strip() or stack.default_version
    clean_settings = dict(nonsecret_settings or {})
    if stack.allowed_nonsecret_settings:
        clean_settings = {k: val for k, val in clean_settings.items() if k in stack.allowed_nonsecret_settings}

    payload = {
        "deploy_type": "official_stack",
        "stack_catalog_id": stack.catalog_id,
        "stack_version": v,
        "domain_name": domain_name.strip(),
        "nonsecret_settings": clean_settings,
        "services_count": len(stack.services),
        "recommended_ram_mb": stack.recommended_ram_mb,
        "services": list(stack.services.keys()),
        "post_install_message": stack.post_install_message,
    }

    sess_id = session_id or "default_session"
    plan_summary = summary or f"Deploy Stack: {stack.display_name} ({v})"

    plan = await action_plans.create_action_plan(
        db=db,
        session_id=sess_id,
        action_type="official_stack_install",
        payload=payload,
        summary=plan_summary,
        confidence=confidence,
        reasoning=reasoning,
        user_id=user_id,
    )

    return {
        "status": "ok",
        "plan_id": plan.plan_id,
        "summary": plan.summary,
        "confidence": plan.confidence,
        "message": f"Stack proposal created for {stack.display_name}. User can review and deploy from wizard.",
    }
