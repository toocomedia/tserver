"""services/official_stacks/stack_synthesizer.py — Synthesizes verified stack definitions from repository inspection."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

_STACK_DB_DEFAULTS: dict[str, dict[str, Any]] = {
    "postgres": {
        "ports": [5432],
        "volume": "/var/lib/postgresql/data",
        "env": {"POSTGRES_USER": "postgres", "POSTGRES_DB": "app"},
        "secret": ("POSTGRES_PASSWORD", "PostgreSQL password", "password"),
        "url": ("DATABASE_URL", "postgresql://postgres:{POSTGRES_PASSWORD}@{service}:5432/app"),
        "image": "postgres:16-alpine",
        "health": {"type": "command", "command": ["pg_isready", "-U", "postgres"]},
        "ram_mb": 256,
    },
    "postgresql": {
        "ports": [5432],
        "volume": "/var/lib/postgresql/data",
        "env": {"POSTGRES_USER": "postgres", "POSTGRES_DB": "app"},
        "secret": ("POSTGRES_PASSWORD", "PostgreSQL password", "password"),
        "url": ("DATABASE_URL", "postgresql://postgres:{POSTGRES_PASSWORD}@{service}:5432/app"),
        "image": "postgres:16-alpine",
        "health": {"type": "command", "command": ["pg_isready", "-U", "postgres"]},
        "ram_mb": 256,
    },
    "mysql": {
        "ports": [3306],
        "volume": "/var/lib/mysql",
        "env": {"MYSQL_DATABASE": "app", "MYSQL_USER": "app"},
        "secret": ("MYSQL_PASSWORD", "MySQL password", "password"),
        "url": ("DATABASE_URL", "mysql://app:{MYSQL_PASSWORD}@{service}:3306/app"),
        "image": "mariadb:11",
        "health": {"type": "command", "command": ["mariadb-admin", "ping", "-h", "localhost"]},
        "ram_mb": 256,
    },
    "mariadb": {
        "ports": [3306],
        "volume": "/var/lib/mysql",
        "env": {"MARIADB_DATABASE": "app", "MARIADB_USER": "app"},
        "secret": ("MARIADB_PASSWORD", "MariaDB password", "password"),
        "url": ("DATABASE_URL", "mysql://app:{MARIADB_PASSWORD}@{service}:3306/app"),
        "image": "mariadb:11",
        "health": {"type": "command", "command": ["mariadb-admin", "ping", "-h", "localhost"]},
        "ram_mb": 256,
    },
    "clickhouse": {
        "ports": [8123, 9000],
        "volume": "/var/lib/clickhouse",
        "env": {},
        "url": ("CLICKHOUSE_DATABASE_URL", "http://{service}:8123/plausible_events_db"),
        "health": {
            "type": "command",
            "command": ["wget", "--spider", "-q", "http://127.0.0.1:8123/ping"],
            "interval_seconds": 5,
            "timeout_seconds": 5,
            "retries": 20,
            "start_period_seconds": 30,
        },
        "ram_mb": 768,
    },
    "redis": {
        "ports": [6379],
        "volume": "/data",
        "env": {},
        "url": ("REDIS_URL", "redis://{service}:6379/0"),
        "image": "redis:7-alpine",
        "health": {"type": "command", "command": ["redis-cli", "ping"]},
        "ram_mb": 256,
    },
    "mongo": {
        "ports": [27017],
        "volume": "/data/db",
        "env": {},
        "url": ("MONGODB_URL", "mongodb://{service}:27017/app"),
        "image": "mongo:7",
        "ram_mb": 384,
    },
    "mongodb": {
        "ports": [27017],
        "volume": "/data/db",
        "env": {},
        "url": ("MONGODB_URL", "mongodb://{service}:27017/app"),
        "image": "mongo:7",
        "ram_mb": 384,
    },
}


def requires_multi_container_stack(inspection: dict[str, Any]) -> bool:
    """Returns True if the inspected repository requires a multi-container stack setup."""
    if not isinstance(inspection, dict):
        return False
    compose_services = (inspection.get("compose_info") or {}).get("services")
    if isinstance(compose_services, list) and len(compose_services) > 1:
        return True

    kinds: set[str] = {str(k).strip().lower() for k in (inspection.get("database_types") or [])}
    for key in ("database_detections", "database_suggestions"):
        for item in inspection.get(key) or []:
            if isinstance(item, dict) and item.get("kind"):
                kinds.add(str(item.get("kind")).strip().lower())

    if "clickhouse" in kinds:
        return True
    if len(kinds - {"sqlite"}) >= 2:
        return True
    return False


def synthesize_stack_from_compose(
    inspection: dict[str, Any],
    domain_name: str = "",
    repo_url: str = "",
) -> dict[str, Any] | None:
    """Extracts a restricted stack plan manifest from inspected Compose services."""
    compose_info = inspection.get("compose_info")
    if not isinstance(compose_info, dict) or not compose_info.get("services"):
        return None

    service_map: dict[str, dict[str, Any]] = {}
    for item in compose_info["services"]:
        if not isinstance(item, dict):
            continue
        image = str(item.get("image") or "").strip()
        if not image:
            continue
        name_raw = str(item.get("name") or image.rsplit("/", 1)[-1].split(":", 1)[0])
        name = _sanitize_name(name_raw)
        if name in service_map:
            continue

        kind = _identify_kind(name, image)
        defaults = _STACK_DB_DEFAULTS.get(kind) or {}
        ports = [int(p) for p in (item.get("internal_ports") or []) if _is_valid_port(p)]
        if not ports:
            ports = list(defaults.get("ports") or [8000])

        svc: dict[str, Any] = {
            "name": name,
            "image": image,
            "ports": ports,
            "depends_on": [],
            "environment": dict(defaults.get("env") or {}),
            "volumes": [],
            "resources": {"memory_mb": defaults.get("ram_mb", 512), "cpu": "1.0"},
        }
        if defaults.get("volume"):
            svc["volumes"].append({"name": f"{name}-data", "mount_path": defaults["volume"]})
        if defaults.get("health"):
            svc["health"] = defaults["health"]
        service_map[name] = svc

    if not service_map:
        return None

    return _build_stack_definition_bundle(
        service_map=service_map,
        inspection=inspection,
        domain_name=domain_name,
        repo_url=repo_url,
        evidence_source="Compose configuration",
    )


def _derive_web_image(clean_repo: str, inspection: dict[str, Any], tag: str) -> str:
    """Derives container image reference from inspection facts or repository coordinates."""
    img = str(inspection.get("image_reference") or inspection.get("image") or "").strip()
    if img:
        return img
    norm_repo = clean_repo.lower().removesuffix(".git").rstrip("/")
    parts = [p for p in norm_repo.split("/") if p and not p.endswith(":")]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}:{tag}"
    base_name = norm_repo.rsplit("/", 1)[-1] if norm_repo else "app"
    return f"{base_name}:{tag}"


def synthesize_stack_from_inspection(
    inspection: dict[str, Any],
    domain_name: str = "",
    repo_url: str = "",
) -> dict[str, Any] | None:
    """Synthesizes a stack manifest when source inspection discovers datastores like ClickHouse."""
    clean_repo = repo_url or str(inspection.get("repository_url") or "")
    base_name = clean_repo.rsplit("/", 1)[-1].removesuffix(".git") if clean_repo else "app"
    app_svc_name = _sanitize_name(base_name) or "web"

    app_port = 8000
    if inspection.get("internal_port"):
        try:
            p = int(inspection["internal_port"])
            if 1 <= p <= 65535:
                app_port = p
        except (ValueError, TypeError):
            pass

    service_map: dict[str, dict[str, Any]] = {}

    raw_branch = str(inspection.get("branch") or "v1.0.0").strip()
    tag = "v1.0.0" if raw_branch.lower() in ("main", "master", "latest", "trunk", "") else raw_branch
    if not tag.startswith("v") and tag and tag[0].isdigit():
        tag = f"v{tag}"
    elif not any(c.isdigit() for c in tag):
        tag = "v1.0.0"

    web_image = _derive_web_image(clean_repo, inspection, tag)

    # 1. Main web service
    service_map[app_svc_name] = {
        "name": app_svc_name,
        "image": web_image,
        "ports": [app_port],
        "depends_on": [],
        "environment": {},
        "volumes": [],
        "resources": {"memory_mb": 512, "cpu": "1.0"},
    }

    # 2. Extract detected datastores
    detected_kinds: list[str] = []
    for k in (inspection.get("database_types") or []):
        detected_kinds.append(str(k).strip().lower())
    for key in ("database_detections", "database_suggestions"):
        for item in inspection.get(key) or []:
            if isinstance(item, dict) and item.get("kind"):
                detected_kinds.append(str(item.get("kind")).strip().lower())

    seen_kinds: set[str] = set()
    for raw_k in detected_kinds:
        kind = "postgres" if raw_k in ("postgres", "postgresql", "psql") else "mariadb" if raw_k in ("mariadb", "mysql") else raw_k
        if kind in seen_kinds or kind not in _STACK_DB_DEFAULTS or kind == "sqlite":
            continue
        seen_kinds.add(kind)

        defaults = _STACK_DB_DEFAULTS[kind]
        db_name = f"{app_svc_name}_{kind}"[:48]
        service_map[db_name] = {
            "name": db_name,
            "image": defaults.get("image", f"{kind}:1.0"),
            "ports": list(defaults["ports"]),
            "depends_on": [],
            "environment": dict(defaults.get("env") or {}),
            "volumes": [{"name": f"{db_name}-data", "mount_path": defaults["volume"]}] if defaults.get("volume") else [],
            "resources": {"memory_mb": defaults.get("ram_mb", 256), "cpu": "1.0"},
        }
        if defaults.get("health"):
            service_map[db_name]["health"] = defaults["health"]

    if len(service_map) <= 1:
        return None

    return _build_stack_definition_bundle(
        service_map=service_map,
        inspection=inspection,
        domain_name=domain_name,
        repo_url=repo_url,
        evidence_source="source inspection facts",
    )


def _build_stack_definition_bundle(
    service_map: dict[str, dict[str, Any]],
    inspection: dict[str, Any],
    domain_name: str,
    repo_url: str,
    evidence_source: str,
) -> dict[str, Any]:
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
    stack_name = _sanitize_name(clean_repo.rsplit("/", 1)[-1].removesuffix(".git") if clean_repo else "stack") or "stack"
    has_clickhouse = any("clickhouse" in f"{n} {s['image']}".lower() for n, s in service_map.items())

    manifest: dict[str, Any] = {
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
        "recommended_ram_mb": 2048 if has_clickhouse else 1024,
        "minimum_ram_mb": 512,
        "allowed_nonsecret_settings": allowed_settings,
        "default_environment": default_env,
        "url_templates": url_templates,
        "secrets": secrets,
    }
    settings = {"BASE_URL": f"https://{domain_name.strip()}"} if domain_name.strip() else {}
    evidence = [f"Panel source inspection generated this restricted stack plan from {evidence_source}."]

    return {
        "stack_manifest": manifest,
        "domain_name": domain_name.strip(),
        "nonsecret_settings": settings,
        "evidence": evidence,
        "summary": f"Deploy restricted stack for {clean_repo or stack_name}",
        "confidence": 0.95,
        "reasoning": f"Server inspected {evidence_source} and generated verified multi-service stack deployment.",
    }


def _sanitize_name(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(raw).strip().lower()).strip("-_")
    if not cleaned:
        return "service"
    if not re.match(r"^[a-z]", cleaned):
        cleaned = f"svc-{cleaned}"
    return cleaned[:48]


def _identify_kind(name: str, image: str) -> str:
    text = f"{name} {image}".lower()
    for k in ("clickhouse", "postgres", "mariadb", "mysql", "redis", "mongo"):
        if k in text:
            return k
    return "web"


def _is_valid_port(raw: Any) -> bool:
    try:
        val = int(raw)
        return 1 <= val <= 65535
    except (ValueError, TypeError):
        return False
