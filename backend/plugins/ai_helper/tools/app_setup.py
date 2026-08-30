"""
tools/app_setup.py — Application source inspection and proposal generator for App Engine.
Generates immutable server-side AiActionPlan records for wizard autofill.
"""
from __future__ import annotations

import asyncio
import logging
import re
import copy
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.services import action_plans, setup_handoff
from models.container_app import ContainerApp
from services import container_app_image_inspect_service, container_app_inspection_service
from services.apps_engine import build_secrets, database_provider_capabilities, deployment_drafts, source_access

logger = logging.getLogger(__name__)

_SUPPORTED_GIT_BUILD_MODES = {"railpack", "dockerfile"}
_SUPPORTED_DATABASE_KINDS = {"postgresql", "mariadb", "redis", "mongodb"}
# Docker Compose and multi-service stacks are deployed via reviewed AppSpec plans.


def _needs_digest_resolution(image: str) -> bool:
    tail = (image or "").rsplit("/", 1)[-1]
    return "@sha256:" not in image and (":" not in tail or tail.endswith(":latest"))
_DATABASE_KIND_ALIASES = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "mysql": "mariadb",
    "mariadb": "mariadb",
    "mariadb/mysql": "mariadb",
    "redis": "redis",
    "mongodb": "mongodb",
    "mongo": "mongodb",
}
_UNSUPPORTED_SINGLE_APP_DATASTORES = {"clickhouse"}
_STACK_DB_DEFAULTS = {
    "postgres": {
        "ports": [5432],
        "volume": "/var/lib/postgresql/data",
        "env": {"POSTGRES_USER": "postgres", "POSTGRES_DB": "app"},
        "secret": ("POSTGRES_PASSWORD", "PostgreSQL password", "password"),
        "url": ("DATABASE_URL", "postgresql://postgres:{POSTGRES_PASSWORD}@{service}:5432/app"),
    },
    "mysql": {
        "ports": [3306],
        "volume": "/var/lib/mysql",
        "env": {"MYSQL_DATABASE": "app", "MYSQL_USER": "app"},
        "secret": ("MYSQL_PASSWORD", "MySQL password", "password"),
        "url": ("DATABASE_URL", "mysql://app:{MYSQL_PASSWORD}@{service}:3306/app"),
    },
    "mariadb": {
        "ports": [3306],
        "volume": "/var/lib/mysql",
        "env": {"MARIADB_DATABASE": "app", "MARIADB_USER": "app"},
        "secret": ("MARIADB_PASSWORD", "MariaDB password", "password"),
        "url": ("DATABASE_URL", "mysql://app:{MARIADB_PASSWORD}@{service}:3306/app"),
    },
    "clickhouse": {
        "ports": [8123, 9000],
        "volume": "/var/lib/clickhouse",
        "env": {
            "CLICKHOUSE_DB": "{CLICKHOUSE_DB}",
            "CLICKHOUSE_USER": "default",
            "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT": "1",
        },
        "secret": ("CLICKHOUSE_PASSWORD", "ClickHouse password", "password"),
        "url": ("CLICKHOUSE_DATABASE_URL", "http://default:{CLICKHOUSE_PASSWORD}@{service}:8123/{CLICKHOUSE_DB}"),
    },
    "redis": {"ports": [6379], "volume": "/data", "env": {}, "url": ("REDIS_URL", "redis://{service}:6379/0")},
    "mongo": {"ports": [27017], "volume": "/data/db", "env": {}, "url": ("MONGODB_URL", "mongodb://{service}:27017/app")},
    "redpanda": {
        "ports": [9092, 9644],
        "volume": "/var/lib/redpanda/data",
        "env": {},
        "command": [
            "redpanda", "start",
            "--smp", "1",
            "--memory", "512M",
            "--reserve-memory", "0M",
            "--overprovisioned",
            "--kafka-addr", "0.0.0.0:9092",
            "--advertise-kafka-addr", "{service}:9092",
        ],
    },
    "kafka": {"ports": [9092], "volume": "/var/lib/kafka/data", "env": {}},
}


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
                "message": f"{stack_info['name']} requires a reviewed multi-service stack deployment ({stack_info['services_count']} services, {stack_info['recommended_ram_mb'] // 1024} GB RAM recommended).",
            }
        try:
            res = await asyncio.to_thread(container_app_inspection_service.inspect_repository, repo, branch.strip() or "main")
            source_kind = "compose_stack" if (res.get("compose_info") or {}).get("services") else "git"
            from services.apps_engine.source_image_advisor import advise_official_image
            advice = advise_official_image(repo, str(res.get("framework") or ""))
            response: Dict[str, Any] = {"status": "ok", "source_type": source_kind, "inspection": res}
            if advice:
                response["official_image_recommendation"] = advice
            return response
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
                "message": f"{stack_info['name']} requires a reviewed multi-service stack deployment ({stack_info['services_count']} services, {stack_info['recommended_ram_mb'] // 1024} GB RAM recommended).",
            }
        try:
            res = await container_app_image_inspect_service.inspect_image(image)
            if res.get("requires_multi_container") or "clickhouse" in (res.get("database_types") or []):
                return {
                    "status": "ok",
                    "source_type": "official_stack",
                    "requires_multi_container": True,
                    "database_types": res.get("database_types", []),
                    "inspection": res,
                    "message": f"{res.get('runtime', 'Application')} requires a multi-container stack deployment (requires: {', '.join(res.get('database_types', []))}). Propose a validated stack via propose_app_spec_plan.",
                }
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
    has_digest = bool(inspection.get("digest"))
    has_source = bool(inspection.get("source_repository"))
    verified = has_digest or has_source
    evidence = []
    if inspection.get("digest"):
        evidence.append(f"Registry digest: {inspection.get('digest')}")
    if inspection.get("source_repository"):
        evidence.append(f"Upstream repository: {inspection.get('source_repository')}")
    if not evidence:
        evidence.append("Registry image inspected. Review settings before deployment.")

    return {
        "status": "ok", "inspection": inspection,
        "official_image": {
            "verified": verified,
            "evidence": evidence,
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


def _normalize_port(port_val: Any, default: int = 3000) -> int:
    try:
        val = int(port_val)
        if 1 <= val <= 65535:
            return val
    except (ValueError, TypeError):
        pass
    return default


def _normalize_health_path(raw_path: Any) -> str:
    path = str(raw_path or "disabled").strip()
    if path.lower() in {"", "disabled", "none", "skip", "off"}:
        return "disabled"
    if path.startswith("/") and len(path) <= 255 and not any(c in path for c in "\r\n\t"):
        return path
    return "disabled"


def _build_single_app_payload(
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
    norm_port = _normalize_port(internal_port, 3000)
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
                kind = _normalize_database_kind(str(item.get("kind", "")))
                provider = _normalize_database_provider(str(item.get("provider", "docker")), kind)
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

    return {
        "source_type": stype,
        "repository_url": repository_url.strip(),
        "branch": branch.strip() or "main",
        "image_reference": image_reference.strip(),
        "internal_port": norm_port,
        "build_mode": bmode,
        "custom_start_command": (custom_start_command or "").strip(),
        "health_path": _normalize_health_path(health_path),
        "environment_values": clean_envs,
        "secret_requirements": cleaned_secrets,
        "database_attachments": clean_dbs,
        "storage_mounts": clean_mounts,
        "domain_name": clean_dom.lower(),
        "setup_notes": list(setup_notes or []),
        "admin_commands": list(admin_commands or []),
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
    health_path: str = "disabled",
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

    stype = (source_type or ("image" if image_reference.strip() else "git")).strip().lower()
    bmode = (build_mode or ("image" if stype == "image" else "railpack")).strip().lower()

    if not domain_name.strip() and session_id:
        from models.ai_helper import AiChatSession
        stmt = select(AiChatSession.target_domain).where(AiChatSession.session_id == session_id)
        stored_domain = (await db.execute(stmt)).scalar_one_or_none()
        if stored_domain:
            domain_name = stored_domain.strip()

    # If source (git or image) requires multi-service compose stack or multi-datastore requirement, promote to stack install seamlessly
    inspection = None
    target_repo = repository_url.strip()
    try:
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
            target_repo = str(inspection.get("source_repository") or "").strip()
    except Exception as exc:
        logger.warning("Inspection during propose_app_install failed: %s", exc)

    discovered_notes = list(kwargs.get("setup_notes") or [])
    discovered_cmds = list(kwargs.get("admin_commands") or [])
    if inspection:
        hints = (inspection.get("documentation_evidence") or {}).get("setup_hints") or {}
        for c in hints.get("admin_commands") or []:
            c_text = c.get("command") if isinstance(c, dict) else str(c)
            if c_text and c_text not in discovered_cmds:
                discovered_cmds.append(c_text)

        # If caller omitted database attachments, populate dynamically discovered databases
        if not database_attachments and inspection.get("database_types"):
            database_attachments = []
            for kind in inspection["database_types"]:
                k = _normalize_database_kind(str(kind))
                database_attachments.append({
                    "kind": k,
                    "provider": _normalize_database_provider("docker", k),
                })

    try:
        payload = _build_single_app_payload(
            source_type=stype,
            repository_url=repository_url,
            branch=branch,
            image_reference=image_reference,
            internal_port=internal_port,
            build_mode=bmode,
            custom_start_command=custom_start_command,
            health_path=health_path,
            environment_values=environment_values,
            secret_requirements=secret_requirements,
            database_attachments=database_attachments,
            storage_mounts=storage_mounts,
            domain_name=domain_name,
            setup_notes=discovered_notes,
            admin_commands=discovered_cmds,
        )
    except database_provider_capabilities.ProviderChoiceRequired as exc:
        return {
            "status": "provider_choice_required",
            "message": str(exc),
            "kind": exc.kind,
            "provider": exc.provider,
            "provider_state": exc.state,
            "dependency_id": exc.dependency_id,
            "provider_capabilities": database_provider_capabilities.provider_capabilities(),
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
        "message": "Reviewed setup plan created. The user can deploy it with the server-rendered Deploy reviewed setup action.",
    }


def _normalize_database_kind(kind: str) -> str:
    return _DATABASE_KIND_ALIASES.get(kind, kind)


def _normalize_database_provider(provider: str, kind: str) -> str:
    from services.apps_engine import database_provider_capabilities
    return database_provider_capabilities.canonical_provider(kind, provider)


def _single_app_source_error(source_type: str, repository_url: str, branch: str) -> str:
    if source_type != "git" or not repository_url.strip():
        return ""
    try:
        inspection = container_app_inspection_service.inspect_repository(repository_url.strip(), branch.strip() or "main")
    except Exception:
        return ""
    compose_services = (inspection.get("compose_info") or {}).get("services") if isinstance(inspection, dict) else None
    if compose_services:
        return "This repository contains Compose service evidence, so a single-app plan was rejected. Use a restricted stack setup plan."
    kinds = _inspection_database_kinds(inspection)
    unsupported = sorted(kinds & _UNSUPPORTED_SINGLE_APP_DATASTORES)
    if unsupported:
        return (
            "This repository needs unsupported single-app datastore services "
            f"({', '.join(unsupported)}). Use a restricted stack setup plan with private internal services."
        )
    return ""


def _inspection_database_kinds(inspection: Dict[str, Any]) -> set[str]:
    result = set()
    for key in ("database_types",):
        for item in inspection.get(key) or []:
            result.add(str(item).strip().lower())
    for key in ("database_detections", "database_suggestions"):
        for item in inspection.get(key) or []:
            if isinstance(item, dict):
                result.add(str(item.get("kind") or "").strip().lower())
    return {item for item in result if item}


def stack_plan_args_from_inspection(
    source_result: Dict[str, Any],
    *,
    domain_name: str = "",
) -> Dict[str, Any] | None:
    """Build a restricted stack proposal from server source inspection facts."""
    if not isinstance(source_result, dict) or source_result.get("status") != "ok":
        return None
    inspection = source_result.get("inspection")
    if not isinstance(inspection, dict):
        return None
    compose_info = inspection.get("compose_info")
    if not isinstance(compose_info, dict):
        return None
    raw_services = compose_info.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        return None

    service_map: dict[str, dict[str, Any]] = {}
    for item in raw_services[:8]:
        if not isinstance(item, dict):
            continue
        image = str(item.get("image") or "").strip()
        if not image:
            continue
        name = _stack_name(str(item.get("name") or image.rsplit("/", 1)[-1].split(":", 1)[0]))
        if not name or name in service_map:
            continue
        kind = _stack_service_kind(name, image)
        ports = [int(port) for port in item.get("internal_ports") or [] if _valid_port(port)]
        if not ports:
            ports = list((_STACK_DB_DEFAULTS.get(kind) or {}).get("ports") or [])
        service_map[name] = _stack_service_from_evidence(name, image, ports, kind)

    if not service_map:
        return None

    repo = str(inspection.get("repository_url") or source_result.get("repository_url") or "").strip()
    web_service = _choose_web_service(service_map, inspection)
    if not web_service:
        # All services are backing infrastructure; synthesize application container
        app_name = _stack_name(repo.rsplit("/", 1)[-1].removesuffix(".git") if repo else "app") or "app"
        app_port = 8000
        if inspection.get("internal_port"):
            try:
                p = int(inspection["internal_port"])
                if 1 <= p <= 65535:
                    app_port = p
            except (ValueError, TypeError):
                pass
        else:
            r = str(inspection.get("runtime") or "").lower()
            if "node" in r or "javascript" in r or "typescript" in r:
                app_port = 3000
            elif "go" in r or "java" in r:
                app_port = 8080

        norm_repo = repo.lower().removesuffix(".git").rstrip("/")
        parts = [p for p in norm_repo.split("/") if p and not p.endswith(":")]
        tag = str(inspection.get("branch") or "latest").strip() or "latest"
        web_image = f"{parts[-2]}/{parts[-1]}:{tag}" if len(parts) >= 2 else f"{app_name}:{tag}"
        service_map[app_name] = {
            "name": app_name,
            "image": web_image,
            "ports": [app_port],
            "depends_on": list(service_map.keys()),
            "environment": {},
            "volumes": [],
            "resources": {"memory_mb": 512, "cpu": "1.0"},
        }
        web_service = app_name

    web_port = service_map[web_service]["ports"][0]
    for name, service in service_map.items():
        if name != web_service and name not in service_map[web_service]["depends_on"]:
            service_map[web_service]["depends_on"].append(name)

    env_sample = inspection.get("env_sample") if isinstance(inspection.get("env_sample"), dict) else {}
    default_environment = _nonsecret_env_defaults(env_sample)
    allowed_settings = sorted(set(default_environment) | {"BASE_URL", "KAFKA_BROKERS", "CLICKHOUSE_DB"} | {k for s in service_map.values() for k in s.get("environment", {})})

    stack_name = _stack_name(repo.rsplit("/", 1)[-1].removesuffix(".git") if repo else "source-stack")

    # Derive dynamic ClickHouse database name
    ch_db = ""
    if isinstance(env_sample, dict):
        ch_db = str(env_sample.get("CLICKHOUSE_DB") or env_sample.get("CLICKHOUSE_DATABASE") or "").strip()
    if not ch_db:
        for s in service_map.values():
            val = str(s.get("environment", {}).get("CLICKHOUSE_DB") or "").strip()
            if val and val != "{CLICKHOUSE_DB}":
                ch_db = val
                break
    if not ch_db:
        for n, s in service_map.items():
            if _stack_service_kind(n, s.get("image", "")) == "clickhouse" and n.endswith(("_db", "_events_db")):
                ch_db = n
                break
    if not ch_db:
        s_clean = re.sub(r"[^a-zA-Z0-9_]+", "_", stack_name or "").strip("_")
        ch_db = f"{s_clean}_db" if s_clean and s_clean != "stack" else "events_db"

    # Substitute dynamic ClickHouse DB into service environments
    for n, s in service_map.items():
        if "CLICKHOUSE_DB" in s.get("environment", {}):
            s["environment"]["CLICKHOUSE_DB"] = s["environment"]["CLICKHOUSE_DB"].replace("{CLICKHOUSE_DB}", ch_db)

    # Auto-wire Redpanda / Kafka broker to Redpanda Console
    broker_svc = ""
    for n, s in service_map.items():
        text = f"{n} {s['image']}".lower()
        if any(k in text for k in ("op-rp", "redpanda", "kafka")) and "console" not in text:
            broker_svc = n
            if "redpanda" in text or "op-rp" in text:
                if not s.get("command"):
                    s["command"] = [
                        "redpanda", "start",
                        "--smp", "1",
                        "--memory", "512M",
                        "--reserve-memory", "0M",
                        "--overprovisioned",
                        "--kafka-addr", "0.0.0.0:9092",
                        "--advertise-kafka-addr", f"{n}:9092",
                    ]
            break
    if broker_svc:
        for n, s in service_map.items():
            text = f"{n} {s['image']}".lower()
            if "console" in text or "op-rp-console" in text:
                s["environment"].setdefault("KAFKA_BROKERS", f"{broker_svc}:9092")

    secrets = _stack_secrets(service_map, web_service, env_sample)
    url_templates = _stack_url_templates(service_map, clickhouse_db=ch_db)

    manifest = {
        "name": stack_name or "source-stack",
        "display_name": f"{(stack_name or 'Source stack').replace('-', ' ').replace('_', ' ').title()} Stack",
        "vendor_name": "",
        "source_repositories": [repo] if repo.startswith(("https://", "http://", "git@", "ssh://")) else [],
        "version": str(inspection.get("branch") or "source-inspection"),
        "services": list(service_map.values()),
        "startup_order": [name for name in service_map if name != web_service] + [web_service],
        "web_service": web_service,
        "web_port": web_port,
        "startup_timeout_seconds": 120,
        "recommended_ram_mb": 2048 if any(_stack_service_kind(n, s["image"]) == "clickhouse" for n, s in service_map.items()) else 1024,
        "minimum_ram_mb": 1024,
        "allowed_nonsecret_settings": allowed_settings,
        "default_environment": default_environment,
        "url_templates": url_templates,
        "secrets": secrets,
        "post_install_message": (
            f"Initial Administrator Setup: docker exec -it {{web_service}} {inspection.get('documentation_evidence', {}).get('setup_hints', {}).get('admin_commands', [{}])[0].get('command')}"
            if isinstance(inspection.get("documentation_evidence"), dict)
            and isinstance(inspection.get("documentation_evidence", {}).get("setup_hints"), dict)
            and inspection.get("documentation_evidence", {}).get("setup_hints", {}).get("admin_commands")
            and isinstance(inspection.get("documentation_evidence", {}).get("setup_hints", {}).get("admin_commands"), list)
            and inspection.get("documentation_evidence", {}).get("setup_hints", {}).get("admin_commands")[0].get("command")
            else ""
        ),
    }
    settings = {"BASE_URL": f"https://{domain_name.strip()}"} if domain_name.strip() else {}
    evidence = list(compose_info.get("evidence") or [])
    evidence.append("Panel source inspection generated this restricted stack plan; repository Compose was not executed.")
    return {
        "stack_manifest": manifest,
        "domain_name": domain_name.strip(),
        "nonsecret_settings": settings,
        "evidence": evidence[:12],
        "summary": f"Deploy restricted stack for {repo or stack_name}",
        "confidence": 0.72,
        "reasoning": "Server fallback created a restricted stack plan from bounded Compose source evidence.",
    }


def _stack_service_from_evidence(name: str, image: str, ports: list[int], kind: str) -> dict[str, Any]:
    defaults = _STACK_DB_DEFAULTS.get(kind) or {}
    service = {
        "name": name,
        "image": image,
        "ports": ports or [8000],
        "depends_on": [],
        "environment": dict(defaults.get("env") or {}),
        "volumes": [],
        "resources": {"memory_mb": 768 if kind == "clickhouse" else 256 if defaults else 512, "cpu": "1.0"},
    }
    if defaults.get("volume"):
        service["volumes"].append({"name": f"{name}-data", "mount_path": defaults["volume"]})
    if defaults.get("command"):
        service["command"] = [c.replace("{service}", name) for c in defaults["command"]]
    return service


def _choose_web_service(services: dict[str, dict[str, Any]], inspection: dict[str, Any]) -> str:
    repo = str(inspection.get("repository_url") or "").strip()
    repo_base = repo.rsplit("/", 1)[-1].removesuffix(".git").lower() if repo else ""
    infra_keywords = (
        "postgres", "postgresql", "mariadb", "mysql", "clickhouse", "redis", "mongo", "mongodb",
        "redpanda", "kafka", "zookeeper", "rabbitmq", "nats", "op-rp", "memcached", "minio",
    )

    # Priority 1: Match repository base name
    if repo_base:
        for name, service in services.items():
            text = f"{name} {service.get('image', '')}".lower()
            if (name.lower() == repo_base or repo_base in name.lower()) and not any(k in text for k in infra_keywords):
                if not service["ports"] and _valid_port(inspection.get("internal_port")):
                    service["ports"] = [int(inspection["internal_port"])]
                return name

    # Priority 2: Match app/web keywords
    for name, service in services.items():
        text = f"{name} {service.get('image', '')}".lower()
        if any(k in name.lower() for k in ("app", "web", "frontend", "server", "api")) and not any(k in text for k in infra_keywords):
            if not service["ports"] and _valid_port(inspection.get("internal_port")):
                service["ports"] = [int(inspection["internal_port"])]
            return name

    # Priority 3: Any non-infrastructure service
    for name, service in services.items():
        text = f"{name} {service.get('image', '')}".lower()
        if not any(k in text for k in infra_keywords):
            if not service["ports"] and _valid_port(inspection.get("internal_port")):
                service["ports"] = [int(inspection["internal_port"])]
            return name
    return ""


def _stack_service_kind(name: str, image: str) -> str:
    text = f"{name} {image}".lower()
    for kind in ("clickhouse", "postgres", "mariadb", "mysql", "redis", "mongo", "redpanda", "kafka"):
        if kind in text:
            return kind
    return "web"


def _stack_secrets(services: dict[str, dict[str, Any]], web_service: str, env_sample: dict[str, Any]) -> list[dict[str, str]]:
    secrets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, service in services.items():
        defaults = _STACK_DB_DEFAULTS.get(_stack_service_kind(name, service["image"])) or {}
        secret = defaults.get("secret")
        if secret:
            key, purpose, generator = secret
            _append_stack_secret(secrets, seen, key, purpose, generator, name, key)
    for raw_key in env_sample:
        key = str(raw_key).strip().upper()
        if any(token in key for token in ("SECRET", "KEY_BASE", "TOKEN", "PRIVATE_KEY", "API_KEY")):
            generator = "base64_48" if key == "SECRET_KEY_BASE" else "urlsafe64"
            _append_stack_secret(secrets, seen, key, "Application secret", generator, web_service, key)
    return secrets


def _append_stack_secret(
    secrets: list[dict[str, str]], seen: set[tuple[str, str]], key: str, purpose: str,
    generator: str, service: str, environment: str,
) -> None:
    target = (service, environment)
    if target in seen:
        return
    seen.add(target)
    secrets.append({"key": key, "purpose": purpose, "generator": generator, "service": service, "environment": environment})


def _stack_url_templates(services: dict[str, dict[str, Any]], clickhouse_db: str = "events_db") -> dict[str, str]:
    templates: dict[str, str] = {}
    for name, service in services.items():
        defaults = _STACK_DB_DEFAULTS.get(_stack_service_kind(name, service["image"])) or {}
        url = defaults.get("url")
        if not url:
            continue
        key, template = url
        rendered = template.replace("{service}", f"{{{name}}}").replace("{CLICKHOUSE_DB}", clickhouse_db)
        templates.setdefault(key, rendered)
    return templates


def _nonsecret_env_defaults(env_sample: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in env_sample.items():
        key = str(raw_key).strip().upper()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", key):
            continue
        if any(token in key for token in ("PASSWORD", "SECRET", "TOKEN", "PRIVATE_KEY", "API_KEY", "KEY_BASE")):
            continue
        value = str(raw_value or "")
        if len(value) <= 4096 and "\n" not in value and "{" not in value:
            result[key] = value
    return result


def _stack_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")
    if not name or not re.match(r"^[a-z]", name):
        name = f"stack-{name}" if name else "source-stack"
    return name[:48]


def _valid_port(raw: Any) -> bool:
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return False
    return 1 <= port <= 65535


async def propose_stack_install(
    db: AsyncSession,
    stack_manifest: Optional[Dict[str, Any]] = None,
    domain_name: str = "",
    nonsecret_settings: Optional[Dict[str, str]] = None,
    evidence: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    summary: str = "",
    confidence: float = 1.0,
    reasoning: str = "",
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Forward stack proposals directly to propose_app_spec_plan."""
    from plugins.ai_helper.tools.app_spec_setup import propose_app_spec_plan
    return await propose_app_spec_plan(
        db=db,
        app_spec=stack_manifest or {},
        domain_name=domain_name,
        evidence=evidence or ["Direct application stack specification."],
        environment_values=nonsecret_settings,
        summary=summary,
        confidence=confidence,
        reasoning=reasoning,
        session_id=session_id,
        user_id=user_id,
        **kwargs,
    )


async def propose_official_stack_install(db: AsyncSession, **kwargs: Any) -> Dict[str, Any]:
    """Temporary compatibility alias; requires the same structured manifest as the new tool."""
    return await propose_stack_install(db=db, **kwargs)


async def _resolve_stack_manifest_images(stack_manifest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Validates image references quickly without downloading container layers during chat."""
    if not isinstance(stack_manifest, dict):
        return stack_manifest
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


async def get_app_engine_capabilities(db: AsyncSession, **kwargs: Any) -> Dict[str, Any]:
    """Server-owned setup contract. It deliberately omits secret values and raw Compose input."""
    from services.apps_engine import database_provider_capabilities
    provider_records = database_provider_capabilities.provider_capabilities(force=True)
    return {
        "status": "ok",
        "modes": ["git_railpack", "git_dockerfile", "registry_image", "restricted_compose_stack"],
        "databases": {"provider_records": provider_records, "stack": "private internal services declared by reviewed manifest"},
        "storage": "panel-owned named volumes only; no host paths or Docker socket",
        "networking": "one loopback-only web port; dependencies private; no host network or public database ports",
        "secrets": {"generators": ["urlsafe64", "base64_48", "hex32", "password"], "values_visible_to_ai": False},
        "setup_limits": "one source inspection and one reviewed plan attempt; no documentation, DNS, SSL, logs, file reads, directory scans, or hidden retries",
        "stack_manifest": {
            "services": "one to eight service objects observed by panel source inspection: name, image tag or digest, private ports, dependencies, non-secret environment, safe named volumes like {name,mount_path} or {source,target}, resources, optional command health",
            "required": ["name", "version", "services", "startup_order", "web_service", "web_port"],
            "health": "web_health_path only with source or vendor evidence; unknown endpoint must be omitted",
            "secrets": "key, purpose, generator, target service, target environment; values generated only after approval",
        },
        "unsupported": ["raw Docker Compose", "repository Compose execution", "external setup docs during install", "DNS/SSL checks during setup", "host networking", "privileged containers", "host mounts", "Docker socket", "public database ports"],
    }


async def get_app_engine_diagnostics(db: AsyncSession, app_id: int, **kwargs: Any) -> Dict[str, Any]:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        return {"status": "error", "message": "App Engine app was not found."}
    from models.domain import Domain
    from services import container_app_diagnostics_service
    domain = await db.get(Domain, app.domain_id)
    if domain is None:
        return {"status": "error", "message": "App Engine domain was not found."}
    return {"status": "ok", "diagnostics": await container_app_diagnostics_service.collect(db, app, domain)}
