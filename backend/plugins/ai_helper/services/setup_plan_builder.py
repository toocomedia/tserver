"""
services/setup_plan_builder.py — Deterministic, resilient setup plan builder for App Engine.
Constructs validated single-container and multi-container stack plans from inspection facts and AI inputs.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.services import action_plans
from services import container_app_image_inspect_service, container_app_inspection_service
from services.apps_engine import build_secrets
from services.apps_engine import database_provider_capabilities
from services.official_stacks.manifest_validator import compute_stack_manifest_hash
from services.official_stacks.proposal_manifest import stack_from_proposal, validate_stack_settings
from services.official_stacks.schema import stack_to_dict
from services.official_stacks.stack_synthesizer import (
    requires_multi_container_stack,
    synthesize_stack_from_compose,
    synthesize_stack_from_inspection,
)

logger = logging.getLogger(__name__)

_DATABASE_KIND_ALIASES = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "psql": "postgresql",
    "mysql": "mariadb",
    "mariadb": "mariadb",
    "mariadb/mysql": "mariadb",
    "redis": "redis",
    "valkey": "redis",
    "keydb": "redis",
    "mongodb": "mongodb",
    "mongo": "mongodb",
}


def normalize_database_kind(kind: str) -> str:
    cleaned = (kind or "").strip().lower()
    return _DATABASE_KIND_ALIASES.get(cleaned, cleaned)


def normalize_database_provider(provider: str, kind: str) -> str:
    return database_provider_capabilities.canonical_provider(kind, provider)


def normalize_port(port_val: Any, default: int = 3000) -> int:
    try:
        val = int(port_val)
        if 1 <= val <= 65535:
            return val
    except (ValueError, TypeError):
        pass
    return default


def normalize_health_path(raw_path: Any) -> str:
    path = str(raw_path or "disabled").strip()
    if path.lower() in {"", "disabled", "none", "skip", "off"}:
        return "disabled"
    if path.startswith("/") and len(path) <= 255 and not any(c in path for c in "\r\n\t"):
        return path
    return "disabled"


def build_single_app_payload(
    source_type: str,
    repository_url: str = "",
    branch: str = "main",
    image_reference: str = "",
    internal_port: int = 3000,
    build_mode: str = "railpack",
    custom_start_command: str = "",
    health_path: str = "disabled",
    environment_values: Optional[Dict[str, str]] = None,
    secret_requirements: Optional[List[Dict[str, Any]]] = None,
    database_attachments: Optional[List[Dict[str, str]]] = None,
    storage_mounts: Optional[List[Dict[str, str]]] = None,
    domain_name: str = "",
    setup_notes: Optional[List[str]] = None,
    admin_commands: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Constructs and normalizes a single-container App Engine plan payload."""
    norm_port = normalize_port(internal_port, 3000)
    clean_envs, auto_secrets = build_secrets.normalize_environment_map(environment_values)
    if "PORT" in clean_envs:
        clean_envs["PORT"] = str(norm_port)

    clean_dom = (domain_name or "").strip()
    context_text = f"{repository_url} {image_reference}".lower()
    if clean_dom:
        if any(k in context_text for k in ("django", "shynet")) or "ALLOWED_HOSTS" in clean_envs:
            clean_envs.setdefault("ALLOWED_HOSTS", f"{clean_dom},localhost,127.0.0.1")
            clean_envs.setdefault("HOSTNAME", clean_dom)
            clean_envs.setdefault("CSRF_TRUSTED_ORIGINS", f"https://{clean_dom}")
        elif any(k in context_text for k in ("laravel", "php")):
            clean_envs.setdefault("APP_URL", f"https://{clean_dom}")

    cleaned_secrets: List[Dict[str, Any]] = list(auto_secrets)
    known_secret_keys = {item["key"] for item in cleaned_secrets}

    if isinstance(secret_requirements, list):
        for item in secret_requirements:
            if not isinstance(item, dict):
                continue
            raw_key = build_secrets.normalize_environment_key(item.get("key") or "")
            if not raw_key or not build_secrets.ENV_KEY_RE.fullmatch(raw_key) or raw_key in known_secret_keys:
                continue
            purpose = str(item.get("purpose") or f"Generated {raw_key.lower().replace('_', ' ')}").strip()[:256]
            generator = str(item.get("generator") or build_secrets.infer_secret_generator(raw_key)).strip()
            if generator not in {"urlsafe64", "base64_32", "base64_48", "base64_64", "hex32", "hex64", "password"}:
                generator = build_secrets.infer_secret_generator(raw_key)
            cleaned_secrets.append({"key": raw_key, "purpose": purpose or "Application secret", "generator": generator})
            known_secret_keys.add(raw_key)

    clean_dbs: List[Dict[str, str]] = []
    if isinstance(database_attachments, list):
        for item in database_attachments:
            if isinstance(item, dict) and item.get("kind"):
                kind = normalize_database_kind(str(item.get("kind", "")))
                provider = normalize_database_provider(str(item.get("provider", "docker")), kind)
                database_provider_capabilities.require_available(kind, provider)
                clean_dbs.append({
                    "kind": kind,
                    "provider": provider,
                    "environment_key": str(item.get("environment_key", "DATABASE_URL")).strip() or "DATABASE_URL",
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

    stype = (source_type or "image").strip().lower()
    bmode = (build_mode or ("image" if stype == "image" else "railpack")).strip().lower()
    if stype == "image":
        bmode = "image"
    elif bmode not in {"railpack", "dockerfile"}:
        bmode = "railpack"

    payload: Dict[str, Any] = {
        "source_type": stype,
        "repository_url": repository_url.strip(),
        "branch": branch.strip() or "main",
        "image_reference": image_reference.strip(),
        "internal_port": norm_port,
        "build_mode": bmode,
        "custom_start_command": (custom_start_command or "").strip(),
        "health_path": normalize_health_path(health_path),
        "environment_values": clean_envs,
        "secret_requirements": cleaned_secrets,
        "database_attachments": clean_dbs,
        "storage_mounts": clean_mounts,
        "domain_name": domain_name.strip(),
    }
    if setup_notes:
        payload["setup_notes"] = list(setup_notes)
    if admin_commands:
        payload["admin_commands"] = list(admin_commands)
    return payload


async def build_stack_payload(
    stack_manifest: Dict[str, Any],
    domain_name: str = "",
    nonsecret_settings: Optional[Dict[str, str]] = None,
    evidence: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Resolves digests, validates manifest, and builds an official_stack proposal payload."""
    clean_evidence = [str(item).strip()[:512] for item in (evidence or []) if isinstance(item, str) and item.strip()][:12]
    manifest = await resolve_stack_manifest_images(stack_manifest)
    stack = stack_from_proposal(manifest, clean_evidence)
    clean_settings = validate_stack_settings(stack, nonsecret_settings)
    v = stack.default_version
    serialized = stack_to_dict(stack)

    return {
        "deploy_type": "official_stack",
        "stack_catalog_id": stack.catalog_id,
        "stack_version": v,
        "domain_name": domain_name.strip(),
        "nonsecret_settings": clean_settings,
        "stack_manifest": serialized,
        "manifest_hash": compute_stack_manifest_hash(stack, v),
        "evidence": clean_evidence,
        "stack_display_name": stack.display_name,
        "services_count": len(stack.services),
        "recommended_ram_mb": stack.recommended_ram_mb,
        "services": list(stack.services.keys()),
        "post_install_message": stack.post_install_message,
    }


async def resolve_stack_manifest_images(stack_manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Validates image references in stack manifest."""
    manifest = copy.deepcopy(stack_manifest)
    services = manifest.get("services")
    if not isinstance(services, list):
        return manifest
    for service in services:
        if not isinstance(service, dict):
            continue
        image = str(service.get("image") or "").strip()
        if image:
            container_app_image_inspect_service.validate_image_reference(image)
    return manifest


def build_stack_args_from_compose(
    inspection: Dict[str, Any],
    domain_name: str = "",
    repo_url: str = "",
) -> Dict[str, Any] | None:
    """Deterministically extracts a stack proposal manifest from source inspection facts."""
    return synthesize_stack_from_compose(inspection, domain_name=domain_name, repo_url=repo_url)


async def build_automatic_setup_plan(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: Optional[int],
    source_type: str = "git",
    repository_url: str = "",
    branch: str = "main",
    image_reference: str = "",
    domain_name: str = "",
    inspection_result: Optional[Dict[str, Any]] = None,
) -> action_plans.AiActionPlan:
    """
    Guarantees the creation of a valid AiActionPlan record using available or freshly collected inspection facts.
    """
    stype = (source_type or "").strip().lower()
    if not stype:
        stype = "image" if image_reference.strip() else "git"

    inspection: Dict[str, Any] = {}
    if inspection_result and isinstance(inspection_result, dict) and inspection_result.get("status") == "ok":
        inspection = inspection_result.get("inspection") or inspection_result
    else:
        try:
            from plugins.ai_helper.services import setup_handoff
            if stype == "git" and repository_url.strip():
                cached = setup_handoff.get_cached_inspection(session_id, repository_url.strip())
                if cached and isinstance(cached, dict):
                    inspection = cached.get("inspection") or cached
                if not inspection:
                    inspection = container_app_inspection_service.inspect_repository(repository_url.strip(), branch.strip() or "main")
            elif stype == "image" and image_reference.strip():
                cached = setup_handoff.get_cached_inspection(session_id, image_reference.strip())
                if cached and isinstance(cached, dict):
                    inspection = cached.get("inspection") or cached
                if not inspection:
                    inspection = await container_app_image_inspect_service.inspect_image(image_reference.strip())
        except Exception as exc:
            logger.warning("Automatic inspection failed during fallback: %s", exc)

    # Check if multi-container stack is needed (via Compose or multi-datastore / ClickHouse)
    if requires_multi_container_stack(inspection):
        stack_args = synthesize_stack_from_compose(inspection, domain_name=domain_name, repo_url=repository_url)
        if not stack_args:
            stack_args = synthesize_stack_from_inspection(inspection, domain_name=domain_name, repo_url=repository_url)
        if stack_args:
            from plugins.ai_helper.services.app_spec_plan_builder import build_payload
            payload = build_payload(
                stack_manifest=stack_args["stack_manifest"],
                domain_name=domain_name,
                nonsecret_settings=stack_args.get("nonsecret_settings"),
                evidence=stack_args.get("evidence"),
                repository_url=repository_url,
                source_type=stype,
            )
            return await action_plans.create_action_plan(
                db=db,
                session_id=session_id,
                action_type="app_spec_install",
                payload=payload,
                summary=stack_args.get("summary") or "Deploy application stack",
                confidence=0.9,
                reasoning=stack_args.get("reasoning") or "Automatic verified stack setup plan.",
                user_id=user_id,
            )

    # Single-container application setup
    detected_port = 3000
    if isinstance(inspection, dict):
        if inspection.get("internal_port"):
            detected_port = normalize_port(inspection["internal_port"], 3000)
        elif inspection.get("ports"):
            ports = inspection["ports"]
            if isinstance(ports, list) and ports:
                detected_port = normalize_port(ports[0], 3000)

    detected_dbs: List[Dict[str, str]] = []
    if isinstance(inspection, dict):
        for kind in (inspection.get("database_types") or []):
            k = normalize_database_kind(str(kind))
            detected_dbs.append({
                "kind": k,
                "provider": normalize_database_provider("docker", k),
                "environment_key": "DATABASE_URL",
            })

    env_values = {}
    if isinstance(inspection, dict) and isinstance(inspection.get("env_sample"), dict):
        env_values = inspection["env_sample"]

    bmode = "image" if stype == "image" else str((inspection.get("build_mode") if isinstance(inspection, dict) else None) or "railpack")
    payload = build_single_app_payload(
        source_type=stype,
        repository_url=repository_url,
        branch=branch,
        image_reference=image_reference,
        internal_port=detected_port,
        build_mode=bmode,
        environment_values=env_values,
        database_attachments=detected_dbs,
        domain_name=domain_name,
    )

    summary = f"Deploy {image_reference or repository_url.rsplit('/', 1)[-1].removesuffix('.git') or 'Application'}"
    return await action_plans.create_action_plan(
        db=db,
        session_id=session_id,
        action_type="app_install",
        payload=payload,
        summary=summary,
        confidence=0.9,
        reasoning="Deterministic application plan generated from server source inspection.",
        user_id=user_id,
    )
