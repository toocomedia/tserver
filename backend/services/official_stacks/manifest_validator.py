"""Strict validation and fingerprinting for Official Vendor Stacks."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict

from services.official_stacks.catalog import get_stack
from services.official_stacks.schema import OfficialStackDefinition

_SAFE_SETTING_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_SAFE_VOLUME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/@:+-]{0,511}$")
_SAFE_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SAFE_GENERATORS = {"urlsafe64", "base64_48", "hex32", "password"}


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
    validate_stack_manifest(stack)
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


def validate_stack_manifest(stack: OfficialStackDefinition) -> OfficialStackDefinition:
    """Reject unsafe or incomplete manifests before Compose is generated."""
    if not _SAFE_SERVICE_NAME_RE.fullmatch(stack.catalog_id):
        raise ValueError("Stack catalog identifier is invalid.")
    if not stack.services or len(stack.services) > 8:
        raise ValueError("A stack must contain between one and eight services.")
    if stack.web_service_name not in stack.services:
        raise ValueError("Stack web service is not defined.")
    if set(stack.startup_order) != set(stack.services) or len(set(stack.startup_order)) != len(stack.services):
        raise ValueError("Stack startup order must contain every service exactly once.")
    web_services = [name for name, svc in stack.services.items() if svc.is_web_entrypoint]
    if web_services != [stack.web_service_name]:
        raise ValueError("A stack must declare exactly one web service.")
    if stack.web_internal_port not in stack.services[stack.web_service_name].internal_ports:
        raise ValueError("Stack web port must be declared by the web service.")
    if stack.web_health_path and not stack.web_health_path.startswith("/"):
        raise ValueError("HTTP health path must start with '/'.")

    for name, svc in stack.services.items():
        if name != svc.name or not _SAFE_SERVICE_NAME_RE.fullmatch(name):
            raise ValueError("Stack service name is invalid.")
        image = svc.pinned_digest or svc.image_reference
        if not _SAFE_IMAGE_RE.fullmatch(image) or image.endswith(":latest"):
            raise ValueError(f"Service '{name}' image must use a non-latest pinned reference.")
        if not svc.internal_ports or any(port < 1 or port > 65535 for port in svc.internal_ports):
            raise ValueError(f"Service '{name}' has invalid internal ports.")
        if svc.memory_limit_mb < 64 or svc.memory_limit_mb > 16384:
            raise ValueError(f"Service '{name}' memory limit is invalid.")
        try:
            if float(svc.cpu_limit) <= 0 or float(svc.cpu_limit) > 8:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"Service '{name}' CPU limit is invalid.") from None
        if any(dep not in stack.services or dep == name for dep in svc.depends_on):
            raise ValueError(f"Service '{name}' has an invalid dependency.")
        for key, value in svc.environment_defaults.items():
            if key == "DOCKER_HOST" or not _SAFE_ENV_RE.fullmatch(key) or not isinstance(value, str) or "\n" in value:
                raise ValueError(f"Service '{name}' has an invalid environment value.")
        if svc.command and (
            not isinstance(svc.command, list)
            or len(svc.command) > 32
            or any(not isinstance(token, str) or len(token) > 2048 for token in svc.command)
            or any("/var/run/docker.sock" in token or "--privileged" in token for token in svc.command)
        ):
            raise ValueError(f"Service '{name}' command is unsafe or invalid.")
        service_mounts: set[str] = set()
        for volume in svc.volumes:
            if not _SAFE_VOLUME_RE.fullmatch(volume.name_suffix):
                raise ValueError(f"Service '{name}' has an invalid volume name.")
            path = volume.container_mount_path
            if (
                not path.startswith("/")
                or path == "/"
                or "/../" in f"{path}/"
                or path in service_mounts
                or any(path.startswith(f"{existing}/") or existing.startswith(f"{path}/") for existing in service_mounts)
            ):
                raise ValueError(f"Service '{name}' has an invalid or overlapping mount path.")
            service_mounts.add(path)
        health = svc.health_check
        if health and health.probe_type not in {"command", "http"}:
            raise ValueError(f"Service '{name}' health probe type is invalid.")
        if health and health.probe_type == "command" and not health.command:
            raise ValueError(f"Service '{name}' command health probe is empty.")
        if health and health.probe_type == "http" and (not health.http_path or not health.http_path.startswith("/")):
            raise ValueError(f"Service '{name}' HTTP health path is invalid.")

    _reject_dependency_cycles(stack)
    service_names = set(stack.services)
    secret_keys: set[str] = set()
    for secret in stack.required_secrets:
        if not _SAFE_ENV_RE.fullmatch(secret.key) or secret.key in secret_keys:
            raise ValueError("Stack secret key is invalid or duplicated.")
        if secret.generator not in _SAFE_GENERATORS:
            raise ValueError(f"Stack secret '{secret.key}' has an unsupported generator.")
        if secret.service_name and secret.service_name not in service_names:
            raise ValueError(f"Stack secret '{secret.key}' targets an unknown service.")
        if secret.environment_key and not _SAFE_ENV_RE.fullmatch(secret.environment_key):
            raise ValueError(f"Stack secret '{secret.key}' has an invalid environment key.")
        secret_keys.add(secret.key)
    for key, template in stack.url_templates.items():
        if not _SAFE_ENV_RE.fullmatch(key) or not isinstance(template, str) or len(template) > 2048:
            raise ValueError("Stack URL template is invalid.")
        names = set(re.findall(r"{([A-Za-z_][A-Za-z0-9_]*)}", template))
        if not names.issubset(secret_keys | service_names):
            raise ValueError(f"Stack URL template '{key}' uses an unknown placeholder.")
    return stack


def _reject_dependency_cycles(stack: OfficialStackDefinition) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError("Stack service dependencies contain a cycle.")
        if name in visited:
            return
        visiting.add(name)
        for dependency in stack.services[name].depends_on:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for service_name in stack.services:
        visit(service_name)


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
            "health": {"type": svc.health_check.probe_type, "path": svc.health_check.http_path,
                       "command": svc.health_check.command} if svc.health_check else None,
            "environment": svc.environment_defaults,
        }
    manifest_payload = {
        "catalog_id": stack.catalog_id,
        "version": version,
        "startup_order": stack.startup_order,
        "web_service": stack.web_service_name,
        "web_port": stack.web_internal_port,
        "web_health_path": stack.web_health_path,
        "secrets": [{"key": item.key, "generator": item.generator, "service": item.service_name,
                     "environment": item.environment_key} for item in stack.required_secrets],
        "services": services_data,
    }
    normalized = json.dumps(manifest_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
