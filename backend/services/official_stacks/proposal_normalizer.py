"""services/official_stacks/proposal_normalizer.py — Sanitization and alias normalization for AI stack proposals."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Set

# Known canonical top-level fields for official stack proposals
_TOP_LEVEL_CANONICAL = {
    "name", "display_name", "vendor_name", "description", "source_repositories", "version",
    "services", "startup_order", "web_service", "web_port", "web_health_path",
    "startup_timeout_seconds", "recommended_ram_mb", "minimum_ram_mb",
    "allowed_nonsecret_settings", "default_environment", "url_templates", "secrets", "docs_url",
}

# Mapping of common top-level aliases from LLM / tool outputs to canonical keys
_TOP_LEVEL_ALIASES: dict[str, str] = {
    "catalog_id": "name",
    "stack_name": "name",
    "title": "display_name",
    "vendor": "vendor_name",
    "repositories": "source_repositories",
    "repos": "source_repositories",
    "source_repository": "source_repositories",
    "service_list": "services",
    "order": "startup_order",
    "web_service_name": "web_service",
    "entrypoint": "web_service",
    "web_internal_port": "web_port",
    "health_path": "web_health_path",
    "startup_timeout": "startup_timeout_seconds",
    "timeout_seconds": "startup_timeout_seconds",
    "recommended_ram": "recommended_ram_mb",
    "ram_mb": "recommended_ram_mb",
    "min_ram_mb": "minimum_ram_mb",
    "minimum_ram": "minimum_ram_mb",
    "allowed_settings": "allowed_nonsecret_settings",
    "settings": "allowed_nonsecret_settings",
    "environment": "default_environment",
    "env": "default_environment",
    "environment_variables": "default_environment",
    "templates": "url_templates",
    "required_secrets": "secrets",
    "secret_requirements": "secrets",
    "documentation_url": "docs_url",
}

# Mapping of service-level aliases
_SERVICE_ALIASES: dict[str, str] = {
    "service_name": "name",
    "image_reference": "image",
    "container_image": "image",
    "internal_ports": "ports",
    "internal_port": "ports",
    "port": "ports",
    "dependencies": "depends_on",
    "depends": "depends_on",
    "env": "environment",
    "env_vars": "environment",
    "environment_variables": "environment",
    "mounts": "volumes",
    "volume": "volumes",
    "resource_limits": "resources",
    "limits": "resources",
    "cmd": "command",
    "health_check": "health",
    "healthcheck": "health",
}

# Volume key aliases
_VOLUME_ALIASES: dict[str, str] = {
    "target": "mount_path",
    "path": "mount_path",
    "container_path": "mount_path",
    "container_mount_path": "mount_path",
    "destination": "mount_path",
    "name_suffix": "name",
    "volume": "name",
    "label": "name",
    "source": "name",
}

# Health check key aliases
_HEALTH_ALIASES: dict[str, str] = {
    "probe_type": "type",
    "cmd": "command",
    "interval": "interval_seconds",
    "timeout": "timeout_seconds",
}


def normalize_stack_proposal_manifest(raw: Any) -> dict[str, Any]:
    """
    Normalizes an incoming dictionary manifest from AI chat or tools.
    Translates common LLM schema aliases without relaxing security validation rules.
    """
    if not isinstance(raw, dict):
        return raw

    manifest = copy.deepcopy(raw)

    # 1. Normalize top-level aliases
    for alias_key, canonical_key in _TOP_LEVEL_ALIASES.items():
        if alias_key in manifest:
            val = manifest.pop(alias_key)
            if canonical_key not in manifest or manifest[canonical_key] is None:
                manifest[canonical_key] = val

    # Strip safe non-manifest metadata that LLMs commonly emit
    manifest.pop("named_volumes", None)
    manifest.pop("networks", None)
    manifest.pop("volumes_from", None)
    top_resources = manifest.pop("resources", None)
    if isinstance(top_resources, dict) and "recommended_ram_mb" not in manifest:
        mem = top_resources.get("memory_mb") or top_resources.get("memory")
        if isinstance(mem, (int, str)):
            try:
                manifest["recommended_ram_mb"] = int(mem)
            except ValueError:
                pass

    # Special case: stray top-level 'port' or 'ports'
    if "port" in manifest:
        raw_port = manifest.pop("port")
        if "web_port" not in manifest and isinstance(raw_port, (int, str)):
            manifest["web_port"] = raw_port
    if "ports" in manifest:
        raw_ports = manifest.pop("ports")
        if "web_port" not in manifest:
            if isinstance(raw_ports, list) and raw_ports:
                manifest["web_port"] = raw_ports[0]
            elif isinstance(raw_ports, (int, str)):
                manifest["web_port"] = raw_ports

    # Special case: stray top-level 'dependencies' (e.g. dict or list)
    top_deps = manifest.pop("dependencies", None)

    # Normalize source_repositories if given as string
    if isinstance(manifest.get("source_repositories"), str):
        manifest["source_repositories"] = [manifest["source_repositories"]]

    # 2. Normalize services list or dict
    raw_services = manifest.get("services")
    if isinstance(raw_services, dict):
        normalized_svc_list = []
        for svc_name, svc_body in raw_services.items():
            if isinstance(svc_body, dict):
                svc_dict = {"name": svc_name, **svc_body}
                normalized_svc_list.append(svc_dict)
        manifest["services"] = normalized_svc_list

    if isinstance(manifest.get("services"), list):
        for svc in manifest["services"]:
            if not isinstance(svc, dict):
                continue
            _normalize_service_dict(svc, top_deps)

    return manifest


def _normalize_service_dict(svc: dict[str, Any], top_deps: Any) -> None:
    """Normalizes a single service definition dictionary."""
    for alias_key, canonical_key in _SERVICE_ALIASES.items():
        if alias_key in svc:
            val = svc.pop(alias_key)
            if canonical_key not in svc or svc[canonical_key] is None:
                svc[canonical_key] = val

    # If top-level dependencies dict provided, attach to service
    if isinstance(top_deps, dict) and svc.get("name") in top_deps:
        svc_deps = top_deps[svc["name"]]
        if isinstance(svc_deps, list) and not svc.get("depends_on"):
            svc["depends_on"] = list(svc_deps)

    # Normalize image reference
    raw_img = str(svc.get("image") or "").strip()
    s_name = str(svc.get("name") or "").lower()
    s_text = f"{s_name} {raw_img}".lower()
    if raw_img:
        if raw_img in ("postgres", "postgresql"):
            svc["image"] = "postgres:16-alpine"
        elif raw_img in ("redis", "valkey"):
            svc["image"] = "redis:7-alpine"
        elif raw_img in ("mariadb", "mysql"):
            svc["image"] = "mariadb:11"
        elif raw_img == "clickhouse":
            svc["image"] = "clickhouse/clickhouse-server:24.3-alpine"
        elif raw_img == "nginx":
            svc["image"] = "nginx:alpine"
        else:
            svc["image"] = raw_img

    # Normalize ports into list and auto-fill if empty
    if "ports" in svc:
        p = svc["ports"]
        if isinstance(p, (int, str)) and not isinstance(p, (list, tuple, dict)):
            svc["ports"] = [p]
        elif isinstance(p, tuple):
            svc["ports"] = list(p)
    if not svc.get("ports"):
        if any(k in s_text for k in ("postgres", "postgresql", "psql")):
            svc["ports"] = [5432]
        elif any(k in s_text for k in ("redis", "valkey", "keydb")):
            svc["ports"] = [6379]
        elif any(k in s_text for k in ("mariadb", "mysql")):
            svc["ports"] = [3306]
        elif "clickhouse" in s_text:
            svc["ports"] = [8123, 9000]
        elif any(k in s_text for k in ("redpanda", "kafka")) and "console" not in s_text:
            svc["ports"] = [9092, 9644]
        elif "console" in s_text:
            svc["ports"] = [8080]
        elif "nginx" in s_text:
            svc["ports"] = [80]
        elif "shynet" in s_text:
            svc["ports"] = [8080]
        elif "mongo" in s_text:
            svc["ports"] = [27017]
        else:
            svc["ports"] = [8000]

    # Normalize depends_on into list
    if "depends_on" in svc:
        d = svc["depends_on"]
        if isinstance(d, str):
            svc["depends_on"] = [d]
        elif isinstance(d, tuple):
            svc["depends_on"] = list(d)

    # Normalize volumes
    if isinstance(svc.get("volumes"), list):
        normalized_vols = []
        for vol in svc["volumes"]:
            if isinstance(vol, dict):
                normalized_vols.append(_normalize_volume_dict(vol))
            else:
                normalized_vols.append(vol)
        svc["volumes"] = normalized_vols

    # Normalize health
    if isinstance(svc.get("health"), dict):
        svc["health"] = _normalize_health_dict(svc["health"])


def _normalize_volume_dict(vol: dict[str, Any]) -> dict[str, Any]:
    """Normalizes a volume mapping dictionary."""
    res = dict(vol)
    for alias_key, canonical_key in _VOLUME_ALIASES.items():
        if alias_key in res:
            val = res.pop(alias_key)
            if canonical_key not in res:
                res[canonical_key] = val
    return res


def _normalize_health_dict(health: dict[str, Any]) -> dict[str, Any]:
    """Normalizes a health check dictionary."""
    res = dict(health)
    for alias_key, canonical_key in _HEALTH_ALIASES.items():
        if alias_key in res:
            val = res.pop(alias_key)
            if canonical_key not in res:
                res[canonical_key] = val
    return res
