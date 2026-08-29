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
_IMAGE_PROFILES: dict[str, dict[str, Any]] = {
    "docker.umami.is/umami-software/umami": {
        "runtime": "Umami", "internal_port": 3000, "database_types": ["postgresql"],
        "required_environment_names": ["DATABASE_URL"],
    },
}

_INSPECT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 900.0  # 15 minutes


def _discover_source_repository(reference: str, labels: dict[str, str]) -> str | None:
    """Dynamically derive upstream Git repository from OCI labels or standard image coordinates."""
    # 1. Check standard OCI and Docker labels
    for key in (
        "org.opencontainers.image.source",
        "org.opencontainers.image.url",
        "org.label-schema.vcs-url",
        "org.label-schema.url",
    ):
        val = (labels.get(key) or "").strip()
        if val.startswith("http://") or val.startswith("https://"):
            clean = val.removesuffix(".git").rstrip("/")
            if "github.com" in clean or "gitlab.com" in clean:
                return clean

    # 2. Derive from standard namespaced image reference (e.g. plausible/analytics, milesmcc/shynet)
    cleaned = reference.lower().split("@", 1)[0]
    for prefix in ("docker.io/library/", "docker.io/", "library/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    tagless = cleaned.split(":", 1)[0]
    parts = [p for p in tagless.split("/") if p]
    if len(parts) == 2 and not parts[0].endswith((".com", ".io", ".org", ".net")):
        return f"https://github.com/{parts[0]}/{parts[1]}"
    return None


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

    repo_url = _discover_source_repository(reference, labels)
    repo_inspection = None
    if repo_url:
        try:
            from services.container_app_inspection_service import inspect_repository
            repo_inspection = inspect_repository(repo_url)
        except Exception as exc:
            logger.info("Dynamic upstream repository inspection for %s skipped: %s", repo_url, exc)

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
        "source_repository": repo_url,
    }
    result.update(_recommendations(reference, exposed_ports, environment_names, healthcheck, repo_inspection))
    return result


def _environment_names(values: list[str]) -> list[str]:
    names = {value.split("=", 1)[0] for value in values if "=" in value}
    return sorted(name for name in names if re.fullmatch(r"[A-Z_][A-Z0-9_]*", name))


def _recommendations(
    reference: str,
    exposed_ports: list[str],
    environment_names: list[str],
    healthcheck: str | None,
    repo_inspection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = _IMAGE_PROFILES.get(_without_tag(reference), {})
    databases = set(profile.get("database_types", []))
    if repo_inspection and repo_inspection.get("database_types"):
        databases.update(repo_inspection["database_types"])

    all_envs = set(environment_names)
    if repo_inspection and repo_inspection.get("env_sample"):
        all_envs.update(repo_inspection["env_sample"].keys())

    for env in all_envs:
        upper = env.upper()
        if any(k in upper for k in ("POSTGRES", "PG_", "DATABASE_URL")):
            databases.add("postgresql")
        elif any(k in upper for k in ("MYSQL", "MARIADB")):
            databases.add("mariadb")
        elif "CLICKHOUSE" in upper:
            databases.add("clickhouse")
        elif "REDIS" in upper:
            databases.add("redis")
        elif "MONGO" in upper:
            databases.add("mongodb")

    port = profile.get("internal_port") or _http_healthcheck_port(healthcheck)
    if not port and repo_inspection:
        port = repo_inspection.get("internal_port")
    if not port and exposed_ports:
        common_ports = [int(p) for p in exposed_ports if p.isdigit()]
        for p in (80, 8080, 8000, 3000, 5000, 8090, 5678, 2368, 8055):
            if p in common_ports:
                port = p
                break
        if not port and common_ports:
            port = common_ports[0]

    req_envs = list(profile.get("required_environment_names", []))
    if repo_inspection:
        for k in repo_inspection.get("env_sample", {}).keys():
            if k not in req_envs:
                req_envs.append(k)
    req_envs.sort()

    has_compose = bool(repo_inspection and repo_inspection.get("has_docker_compose"))
    requires_multi = bool(profile.get("requires_multi_container") or "clickhouse" in databases or len(databases) >= 2 or has_compose)

    runtime = profile.get("runtime", "Registry image")
    if repo_inspection and repo_inspection.get("runtime"):
        runtime = repo_inspection["runtime"]

    summary = "Registry metadata inspected dynamically. Review suggested settings before deployment."
    if requires_multi:
        summary = f"Multi-container stack detected (databases: {', '.join(sorted(databases))}). Recommended to deploy via AI Helper or Compose Stack."

    return {
        "runtime": runtime,
        "build_mode": "image",
        "internal_port": port,
        "database_types": sorted(databases),
        "required_environment_names": req_envs,
        "requires_multi_container": requires_multi,
        "summary": summary,
        "inspection_note": _inspection_note(exposed_ports, port),
        "compose_info": repo_inspection.get("compose_info") if repo_inspection else None,
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
