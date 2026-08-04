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
from typing import Any

from services.container_app_service import _run
from services.resource_guard_service import resource_guard_service

logger = logging.getLogger(__name__)

# Registry image reference: name[:tag][@digest]
_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/\-]*(?::[A-Za-z0-9][A-Za-z0-9._.\-]*)?(?:@[A-Za-z0-9:+._\-]+)?$"
)


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

    token = resource_guard_service.register(
        "container_app", "image-inspect", "background",
        f"Image inspect: {reference}", profile="image_pull",
    )
    try:
        return await asyncio.to_thread(_pull_and_inspect, reference)
    finally:
        resource_guard_service.unregister(token)


def _pull_and_inspect(reference: str) -> dict[str, Any]:
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
        raw: list[dict] = json.loads(inspect.stdout)
        meta = raw[0] if raw else {}
    except (json.JSONDecodeError, IndexError) as exc:
        raise ValueError(f"Could not parse docker inspect output: {exc}") from exc

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

    # Remove the pulled image to reclaim disk immediately
    _run(["docker", "rmi", reference], timeout=60)

    return {
        "reference": reference,
        "digest": digest,
        "size_mb": size_mb,
        "exposed_ports": exposed_ports,
        "entrypoint": entrypoint,
        "cmd": cmd,
        "healthcheck": healthcheck,
        "labels": labels,
    }
