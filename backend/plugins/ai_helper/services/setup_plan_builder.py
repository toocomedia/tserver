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
from services.official_stacks.manifest_validator import compute_stack_manifest_hash
from services.official_stacks.proposal_manifest import stack_from_proposal, validate_stack_settings
from services.official_stacks.schema import stack_to_dict

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
        "env": {},
        "url": ("CLICKHOUSE_URL", "http://{service}:8123/default"),
    },
    "redis": {"ports": [6379], "volume": "/data", "env": {}, "url": ("REDIS_URL", "redis://{service}:6379/0")},
    "mongo": {"ports": [27017], "volume": "/data/db", "env": {}, "url": ("MONGODB_URL", "mongodb://{service}:27017/app")},
}


def normalize_database_kind(kind: str) -> str:
    cleaned = (kind or "").strip().lower()
    return _DATABASE_KIND_ALIASES.get(cleaned, cleaned)


def normalize_database_provider(provider: str, kind: str) -> str:
    prov = (provider or "docker").strip().lower()
    if prov in {"panel", "panel_managed", "managed"}:
        return "panel_postgres" if kind == "postgresql" else "panel_mariadb" if kind == "mariadb" else "docker"
    if prov in {"postgres", "postgresql"}:
        return "panel_postgres"
    if prov in {"mysql", "mariadb"}:
        return "panel_mariadb"
    return prov or "docker"


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
) -> Dict[str, Any]:
    """Constructs and normalizes a single-container App Engine plan payload."""
    clean_envs: Dict[str, str] = {}
    if isinstance(environment_values, dict):
        for k, v in environment_values.items():
            if isinstance(k, str) and k.strip():
                clean_envs[k.strip()] = str(v) if v is not None else ""

    # Strip raw secret values; identify keys that need server-side generation
    cleaned_secrets = [
        {"key": key, "purpose": "Application secret", "generator": "urlsafe64"}
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
            generator = str(item.get("generator") or "urlsafe64").strip()
            if generator not in {"urlsafe64", "password", "hex32", "base64_48"}:
                generator = "urlsafe64"
            cleaned_secrets.append({"key": key, "purpose": purpose or "Application secret", "generator": generator})
            known_secret_keys.add(key)

    clean_dbs: List[Dict[str, str]] = []
    if isinstance(database_attachments, list):
        for item in database_attachments:
            if isinstance(item, dict) and item.get("kind"):
                kind = normalize_database_kind(str(item.get("kind", "")))
                provider = normalize_database_provider(str(item.get("provider", "docker")), kind)
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
        "internal_port": normalize_port(internal_port, 3000),
        "build_mode": bmode,
        "custom_start_command": (custom_start_command or "").strip(),
        "health_path": normalize_health_path(health_path),
        "environment_values": clean_envs,
        "secret_requirements": cleaned_secrets,
        "database_attachments": clean_dbs,
        "storage_mounts": clean_mounts,
        "domain_name": domain_name.strip(),
    }


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
    """Pins untagged/latest images in stack manifest to immutable digests."""
    manifest = copy.deepcopy(stack_manifest)
    services = manifest.get("services")
    if not isinstance(services, list):
        return manifest
    for service in services:
        if not isinstance(service, dict):
            continue
        image = str(service.get("image") or "").strip()
        if not image:
            continue
        tail = image.rsplit("/", 1)[-1]
        if "@sha256:" not in image and (":" not in tail or tail.endswith(":latest")):
            try:
                inspection = await container_app_image_inspect_service.inspect_image(image)
                digest = str(inspection.get("digest") or "").strip()
                if "@sha256:" in digest:
                    service["image"] = digest
            except Exception as exc:
                logger.warning("Could not resolve digest for %s: %s", image, exc)
    return manifest


def build_stack_args_from_compose(
    inspection: Dict[str, Any],
    domain_name: str = "",
    repo_url: str = "",
) -> Dict[str, Any] | None:
    """Deterministically extracts a stack proposal manifest from source inspection facts."""
    compose_info = inspection.get("compose_info") if isinstance(inspection, dict) else None
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
        name_raw = str(item.get("name") or image.rsplit("/", 1)[-1].split(":", 1)[0])
        name = re.sub(r"[^a-z0-9_-]+", "-", name_raw.strip().lower()).strip("-_") or "svc"
        if not re.match(r"^[a-z]", name):
            name = f"svc-{name}"
        name = name[:48]
        if name in service_map:
            continue

        kind = "web"
        text = f"{name} {image}".lower()
        for k in ("clickhouse", "postgres", "mariadb", "mysql", "redis", "mongo"):
            if k in text:
                kind = k
                break

        defaults = _STACK_DB_DEFAULTS.get(kind) or {}
        ports = [int(p) for p in (item.get("internal_ports") or []) if normalize_port(p, 0) > 0]
        if not ports:
            ports = list(defaults.get("ports") or [8000])

        svc: dict[str, Any] = {
            "name": name,
            "image": image,
            "ports": ports,
            "depends_on": [],
            "environment": dict(defaults.get("env") or {}),
            "volumes": [],
            "resources": {"memory_mb": 768 if kind == "clickhouse" else 256 if defaults else 512, "cpu": "1.0"},
        }
        if defaults.get("volume"):
            svc["volumes"].append({"name": f"{name}-data", "mount_path": defaults["volume"]})
        service_map[name] = svc

    if not service_map:
        return None

    web_svc = ""
    for n, s in service_map.items():
        text = f"{n} {s['image']}".lower()
        if not any(k in text for k in ("postgres", "mariadb", "mysql", "clickhouse", "redis", "mongo")):
            web_svc = n
            break
    if not web_svc:
        web_svc = list(service_map.keys())[0]

    for n in service_map:
        if n != web_svc and n not in service_map[web_svc]["depends_on"]:
            service_map[web_svc]["depends_on"].append(n)

    env_sample = inspection.get("env_sample") if isinstance(inspection.get("env_sample"), dict) else {}
    default_env: dict[str, str] = {}
    for raw_k, raw_v in env_sample.items():
        k = str(raw_k).strip().upper()
        if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", k) and not any(t in k for t in ("PASSWORD", "SECRET", "TOKEN", "KEY_BASE")):
            default_env[k] = str(raw_v or "")

    allowed_settings = sorted(default_env)
    if "BASE_URL" not in allowed_settings:
        allowed_settings.append("BASE_URL")

    secrets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for n, s in service_map.items():
        text = f"{n} {s['image']}".lower()
        for k, d in _STACK_DB_DEFAULTS.items():
            if k in text and d.get("secret"):
                sec_k, purp, gen = d["secret"]
                if (n, sec_k) not in seen:
                    seen.add((n, sec_k))
                    secrets.append({"key": sec_k, "purpose": purp, "generator": gen, "service": n, "environment": sec_k})

    for raw_k in env_sample:
        k = str(raw_k).strip().upper()
        if any(t in k for t in ("SECRET", "KEY_BASE", "TOKEN", "PRIVATE_KEY", "API_KEY")):
            gen = "base64_48" if k == "SECRET_KEY_BASE" else "urlsafe64"
            if (web_svc, k) not in seen:
                seen.add((web_svc, k))
                secrets.append({"key": k, "purpose": "Application secret", "generator": gen, "service": web_svc, "environment": k})

    url_templates: dict[str, str] = {}
    for n, s in service_map.items():
        text = f"{n} {s['image']}".lower()
        for k, d in _STACK_DB_DEFAULTS.items():
            if k in text and d.get("url"):
                uk, ut = d["url"]
                url_templates.setdefault(uk, ut.replace("{service}", f"{{{n}}}"))

    clean_repo = repo_url or str(inspection.get("repository_url") or "")
    stack_name = re.sub(r"[^a-z0-9_-]+", "-", (clean_repo.rsplit("/", 1)[-1].removesuffix(".git") if clean_repo else "stack")).strip("-_") or "stack"
    if not re.match(r"^[a-z]", stack_name):
        stack_name = f"stack-{stack_name}"

    manifest = {
        "name": stack_name[:48],
        "display_name": f"{stack_name.replace('-', ' ').title()} Stack",
        "vendor_name": "",
        "source_repositories": [clean_repo] if clean_repo.startswith(("https://", "http://", "git@")) else [],
        "version": str(inspection.get("branch") or "main"),
        "services": list(service_map.values()),
        "startup_order": [n for n in service_map if n != web_svc] + [web_svc],
        "web_service": web_svc,
        "web_port": service_map[web_svc]["ports"][0],
        "startup_timeout_seconds": 120,
        "recommended_ram_mb": 2048 if any("clickhouse" in f"{n} {s['image']}".lower() for n, s in service_map.items()) else 1024,
        "minimum_ram_mb": 512,
        "allowed_nonsecret_settings": allowed_settings,
        "default_environment": default_env,
        "url_templates": url_templates,
        "secrets": secrets,
    }
    settings = {"BASE_URL": f"https://{domain_name.strip()}"} if domain_name.strip() else {}
    evidence = list(compose_info.get("evidence") or [])
    evidence.append("Panel source inspection generated this restricted stack plan.")

    return {
        "stack_manifest": manifest,
        "domain_name": domain_name.strip(),
        "nonsecret_settings": settings,
        "evidence": evidence[:12],
        "summary": f"Deploy restricted stack for {clean_repo or stack_name}",
        "confidence": 0.95,
        "reasoning": "Server inspected Compose configuration and generated verified stack deployment.",
    }


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
            if stype == "git" and repository_url.strip():
                inspection = container_app_inspection_service.inspect_repository(repository_url.strip(), branch.strip() or "main")
            elif stype == "image" and image_reference.strip():
                inspection = await container_app_image_inspect_service.inspect_image(image_reference.strip())
        except Exception as exc:
            logger.warning("Automatic inspection failed during fallback: %s", exc)

    # Check if multi-container stack
    compose_services = (inspection.get("compose_info") or {}).get("services") if isinstance(inspection, dict) else None
    if compose_services and len(compose_services) > 1:
        stack_args = build_stack_args_from_compose(inspection, domain_name=domain_name, repo_url=repository_url)
        if stack_args:
            try:
                payload = await build_stack_payload(
                    stack_manifest=stack_args["stack_manifest"],
                    domain_name=domain_name,
                    nonsecret_settings=stack_args.get("nonsecret_settings"),
                    evidence=stack_args.get("evidence"),
                )
                return await action_plans.create_action_plan(
                    db=db,
                    session_id=session_id,
                    action_type="stack_install",
                    payload=payload,
                    summary=stack_args.get("summary") or "Deploy application stack",
                    confidence=0.9,
                    reasoning=stack_args.get("reasoning") or "Automatic verified stack setup plan.",
                    user_id=user_id,
                )
            except Exception as exc:
                logger.warning("Stack payload build failed, falling back to single app: %s", exc)

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
