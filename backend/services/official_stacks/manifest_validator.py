"""Strict validation and fingerprinting for Official Vendor Stacks."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict

from services.official_stacks.catalog import get_stack
from services.official_stacks.schema import OfficialStackDefinition

_SAFE_SETTING_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def validate_stack_request(
    catalog_id: str,
    version: str,
    settings: Dict[str, Any],
) -> tuple[OfficialStackDefinition, Dict[str, str]]:
    """
    Validates stack catalog reference, approved version, and non-secret settings.
    Raises ValueError if unauthorized parameters are provided.
    """
    stack = get_stack(catalog_id)
    if stack is None:
        raise ValueError(f"Stack '{catalog_id}' is not an approved official vendor stack.")
    if version not in stack.allowed_versions:
        raise ValueError(f"Version '{version}' is not in the allowed versions for '{catalog_id}': {stack.allowed_versions}")

    sanitized_settings: Dict[str, str] = {}
    allowed_keys = set(stack.allowed_nonsecret_settings)
    for raw_key, raw_value in settings.items():
        key = str(raw_key).strip()
        if not key or not _SAFE_SETTING_KEY_RE.fullmatch(key):
            raise ValueError(f"Invalid configuration key '{key}'.")
        if key not in allowed_keys:
            raise ValueError(f"Configuration key '{key}' is not an allowed non-secret setting for {stack.display_name}.")
        val_str = str(raw_value) if raw_value is not None else ""
        if len(val_str) > 4096 or "\r" in val_str:
            raise ValueError(f"Configuration value for '{key}' is invalid or too long.")
        sanitized_settings[key] = val_str

    return stack, sanitized_settings


def compute_stack_manifest_hash(stack: OfficialStackDefinition, version: str) -> str:
    """Computes a deterministic hash of the authoritative vendor stack topology."""
    services_data = {}
    for name, svc in sorted(stack.services.items()):
        services_data[name] = {
            "image": svc.image_reference,
            "pinned_tag": svc.pinned_tag,
            "ports": svc.internal_ports,
            "volumes": [{"suffix": v.name_suffix, "mount": v.container_mount_path} for v in svc.volumes],
            "depends_on": svc.depends_on,
            "is_web": svc.is_web_entrypoint,
        }
    manifest_payload = {
        "catalog_id": stack.catalog_id,
        "version": version,
        "startup_order": stack.startup_order,
        "web_service": stack.web_service_name,
        "web_port": stack.web_internal_port,
        "services": services_data,
    }
    normalized = json.dumps(manifest_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
