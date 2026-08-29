"""Registry image inspection — pull then docker inspect.

Registers a Guard token (image_pull profile) so the panel accounts for RAM
used while pulling.  Always proceeds even if Guard is at capacity (it's a
metadata-only, read-then-discard operation — image is removed after inspect).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from services.container_app_service import _run
from services.resource_guard_service import resource_guard_service

logger = logging.getLogger(__name__)

# Registry image reference: name[:tag][@digest]
_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/\-]*(?::[A-Za-z0-9][A-Za-z0-9._.\-]*)?(?:@[A-Za-z0-9:+._\-]+)?$"
)

_DATABASE_ENVIRONMENTS = {
    "DATABASE_URL": "postgresql", "POSTGRES_URL": "postgresql", "POSTGRESQL_URL": "postgresql",
    "MYSQL_URL": "mariadb", "REDIS_URL": "redis", "MONGODB_URI": "mongodb", "MONGO_URL": "mongodb",
}
_IMAGE_PROFILES = {
    "docker.umami.is/umami-software/umami": {
        "runtime": "Umami", "internal_port": 3000, "database_types": ["postgresql"],
        "required_environment_names": ["DATABASE_URL"],
    },
    "umami-software/umami": {
        "runtime": "Umami", "internal_port": 3000, "database_types": ["postgresql"],
        "required_environment_names": ["DATABASE_URL"],
    },
    "plausible/analytics": {
        "runtime": "Plausible Analytics", "internal_port": 8000, "database_types": ["postgresql", "clickhouse"],
        "required_environment_names": ["BASE_URL", "SECRET_KEY_BASE", "DATABASE_URL", "CLICKHOUSE_DATABASE_URL"],
        "requires_multi_container": True,
    },
    "ghcr.io/plausible/community-edition": {
        "runtime": "Plausible Analytics", "internal_port": 8000, "database_types": ["postgresql", "clickhouse"],
        "required_environment_names": ["BASE_URL", "SECRET_KEY_BASE", "DATABASE_URL", "CLICKHOUSE_DATABASE_URL"],
        "requires_multi_container": True,
    },
    "milesmcc/shynet": {
        "runtime": "Shynet", "internal_port": 8080, "database_types": ["postgresql"],
        "required_environment_names": ["SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS"],
    },
    "ghost": {
        "runtime": "Ghost", "internal_port": 2368, "database_types": ["mariadb"],
        "required_environment_names": ["url", "database__client"],
    },
    "n8nio/n8n": {
        "runtime": "n8n", "internal_port": 5678, "database_types": ["postgresql"],
        "required_environment_names": ["N8N_ENCRYPTION_KEY", "WEBHOOK_URL"],
    },
    "n8n": {
        "runtime": "n8n", "internal_port": 5678, "database_types": ["postgresql"],
        "required_environment_names": ["N8N_ENCRYPTION_KEY", "WEBHOOK_URL"],
    },
    "directus/directus": {
        "runtime": "Directus", "internal_port": 8055, "database_types": ["postgresql"],
        "required_environment_names": ["KEY", "SECRET"],
    },
    "wordpress": {
        "runtime": "WordPress", "internal_port": 80, "database_types": ["mariadb"],
        "required_environment_names": ["WORDPRESS_DB_HOST", "WORDPRESS_DB_USER", "WORDPRESS_DB_PASSWORD", "WORDPRESS_DB_NAME"],
    },
    "vaultwarden/server": {
        "runtime": "Vaultwarden", "internal_port": 80, "database_types": [],
        "required_environment_names": ["ROCKET_PORT", "WEBSOCKET_ENABLED"],
    },
}

_INSPECT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 900.0  # 15 minutes


def validate_image_reference(reference: str) -> str:
    ref = reference.strip().lower()
    if not ref or not _IMAGE_RE.fullmatch(ref) or ref.startswith(("/", ".", "-")):
        raise ValueError(f"Invalid image reference: {reference!r}")
    return reference.strip()


async def inspect_image(reference: str) -> dict[str, Any]:
    """Pull *reference* and return metadata dict.

    Returns:
        digest, size_mb, exposed_ports, entrypoint, healthcheck, labels
    Raises:
        ValueError  for bad reference or pull/inspect failure
    """
    validate_image_reference(reference)
    ref_key = reference.strip().lower()
    now = time.time()
    if ref_key in _INSPECT_CACHE:
        cached_time, cached_data = _INSPECT_CACHE[ref_key]
        if now - cached_time < _CACHE_TTL_SECONDS:
            return dict(cached_data)

    token = resource_guard_service.register(
        "container_app", "image-inspect", "background",
        f"Image inspect: {reference}", profile="image_pull",
    )
    try:
        res = await asyncio.to_thread(_pull_and_inspect, reference)
        _INSPECT_CACHE[ref_key] = (now, dict(res))
        return res
    finally:
        resource_guard_service.unregister(token)


def _pull_and_inspect(reference: str) -> dict[str, Any]:
    # Fast path: check if the image already exists locally before pulling
    inspect = _run(["docker", "inspect", "--format", "{{json .}}", reference], timeout=15)
    was_local = (inspect.returncode == 0)

    if not was_local:
        # Pull
        pull = _run(["docker", "pull", reference], timeout=300)
        if pull.returncode != 0:
            stderr = (pull.stderr or pull.stdout or "").strip()
            raise ValueError(f"docker pull failed: {stderr[-500:]}")

        # Inspect
        inspect = _run(["docker", "inspect", "--format", "{{json .}}", reference], timeout=30)
        if inspect.returncode != 0:
            raise ValueError("docker inspect failed after successful pull.")

    try:
        raw = json.loads(inspect.stdout)
        meta = raw[0] if isinstance(raw, list) and raw else raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise ValueError(f"Could not parse docker inspect output: {exc}") from exc
    if not meta:
        raise ValueError("Could not parse docker inspect output.")

    # Digest
    digest: str = meta.get("RepoDigests", [""])[0] or meta.get("Id", "")

    # Size
    size_bytes: int = meta.get("Size") or meta.get("VirtualSize") or 0
    size_mb = round(size_bytes / (1024 * 1024), 1)

    # Exposed ports (dict keys like "8080/tcp")
    exposed_ports: list[str] = sorted(
        port.split("/")[0] for port in (meta.get("Config", {}).get("ExposedPorts") or {})
    )

    # Entrypoint + Cmd
    cfg = meta.get("Config") or {}
    entrypoint: list[str] = cfg.get("Entrypoint") or []
    cmd: list[str] = cfg.get("Cmd") or []

    # Healthcheck
    hc = cfg.get("Healthcheck") or {}
    healthcheck: str | None = None
    if hc.get("Test"):
        tests = hc["Test"]
        if isinstance(tests, list) and len(tests) > 1:
            healthcheck = " ".join(tests[1:])  # skip "CMD" / "CMD-SHELL" prefix

    # Labels
    labels: dict[str, str] = cfg.get("Labels") or {}
    environment_names = _environment_names(cfg.get("Env") or [])

    # Remove the pulled image to reclaim disk immediately if it was freshly pulled
    if not was_local:
        _run(["docker", "rmi", reference], timeout=60)

    result = {
        "reference": reference,
        "digest": digest,
        "size_mb": size_mb,
        "exposed_ports": exposed_ports,
        "entrypoint": entrypoint,
        "cmd": cmd,
        "healthcheck": healthcheck,
        "labels": labels,
        "environment_names": environment_names,
    }
    result.update(_recommendations(reference, exposed_ports, environment_names, healthcheck))
    return result


def _environment_names(values: list[str]) -> list[str]:
    names = {value.split("=", 1)[0] for value in values if "=" in value}
    return sorted(name for name in names if re.fullmatch(r"[A-Z_][A-Z0-9_]*", name))


def _recommendations(
    reference: str, exposed_ports: list[str], environment_names: list[str], healthcheck: str | None,
) -> dict[str, Any]:
    profile = _IMAGE_PROFILES.get(_without_tag(reference), {})
    databases = set(profile.get("database_types", []))
    databases.update(_DATABASE_ENVIRONMENTS[name] for name in environment_names if name in _DATABASE_ENVIRONMENTS)
    port = profile.get("internal_port") or _http_healthcheck_port(healthcheck)
    if not port and exposed_ports:
        common_ports = [int(p) for p in exposed_ports if p.isdigit()]
        for p in (80, 8080, 8000, 3000, 5000, 8090, 5678, 2368, 8055):
            if p in common_ports:
                port = p
                break
        if not port and common_ports:
            port = common_ports[0]

    requires_multi = bool(profile.get("requires_multi_container") or "clickhouse" in databases or len(databases) >= 2)
    summary = "Registry metadata inspected. Review every suggested setting before deployment."
    if requires_multi:
        summary = f"{profile.get('runtime', 'Application')} requires multi-container stack deployment (databases: {', '.join(sorted(databases))}). Recommended to deploy via AI Helper or Compose Stack."

    return {
        "runtime": profile.get("runtime", "Registry image"),
        "build_mode": "image",
        "internal_port": port,
        "database_types": sorted(databases),
        "required_environment_names": profile.get("required_environment_names", []),
        "requires_multi_container": requires_multi,
        "summary": summary,
        "inspection_note": _inspection_note(exposed_ports, port),
    }


def _without_tag(reference: str) -> str:
    cleaned = reference.lower().split("@", 1)[0]
    for prefix in ("docker.io/library/", "docker.io/", "library/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    head, slash, tail = cleaned.rpartition("/")
    return f"{head}{slash}{tail.split(':', 1)[0]}" if slash else tail.split(":", 1)[0]


def _http_healthcheck_port(healthcheck: str | None) -> int | None:
    match = re.search(r"https?://(?:localhost|127\.0\.0\.1)(?::(\d+))?", healthcheck or "", re.I)
    return int(match.group(1) or 80) if match else None


def _inspection_note(exposed_ports: list[str], port: int | None) -> str:
    if port:
        return f"Private HTTP port {port} is recommended from image metadata."
    if exposed_ports:
        return f"Image exposes {', '.join(exposed_ports)}. No HTTP port could be safely confirmed."
    return "Image does not declare an HTTP port. Enter the app HTTP port manually."
