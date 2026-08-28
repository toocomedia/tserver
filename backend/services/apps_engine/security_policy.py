"""Allowlist-only security gate for AI-proposed AppSpec objects."""
from __future__ import annotations

import re
from typing import Any

from services.apps_engine.app_spec import AppSpec
from services.apps_engine.app_spec_codec import app_spec_from_dict

RESTART_POLICY = "on-failure:10"
SECURITY_OPTIONS = ("no-new-privileges:true",)

_TOP_LEVEL = {
    "name", "display_name", "web_service_name", "web_port", "services",
    "required_secrets", "default_environment", "url_templates",
}
_SERVICE = {
    "name", "image_reference", "pinned_digest", "internal_ports", "volumes",
    "health_check", "depends_on", "environment_defaults", "command",
    "cpu_limit", "memory_limit_mb",
}
_VOLUME = {"name_suffix", "container_mount_path", "read_only", "description"}
_HEALTH = {
    "probe_type", "command", "http_path", "http_port", "interval_seconds",
    "timeout_seconds", "retries", "start_period_seconds",
}
_SECRET = {"key", "purpose", "generator", "rotate", "service_name", "environment_key"}
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/@:+-]{0,511}$")
_IMAGE_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,446}@sha256:[0-9a-f]{64}$")
_VOLUME_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_GENERATORS = {"password", "hex32", "hex64", "base64_32", "urlsafe64"}
_FORBIDDEN_PATHS = ("/var/run/docker.sock", "/etc", "/proc", "/sys")
_FORBIDDEN_COMMANDS = ("--privileged", "--cap-add", "--network=host", "/var/run/docker.sock")


def validate_app_spec(candidate: dict[str, Any] | AppSpec) -> AppSpec:
    """Return a typed AppSpec only when every accepted field is explicitly safe."""
    if isinstance(candidate, AppSpec):
        from services.apps_engine.app_spec_codec import app_spec_to_dict
        raw = app_spec_to_dict(candidate)
    elif isinstance(candidate, dict):
        raw = candidate
    else:
        raise ValueError("AppSpec must be an object.")
    _reject_unknown(raw, _TOP_LEVEL, "AppSpec")
    if set(_TOP_LEVEL - {"required_secrets", "default_environment", "url_templates"}) - set(raw):
        raise ValueError("AppSpec is missing required fields.")
    services = raw.get("services")
    if not isinstance(services, dict) or not 1 <= len(services) <= 8:
        raise ValueError("AppSpec must contain between one and eight named services.")
    for name, service in services.items():
        _validate_service(str(name), service)
    _validate_secrets(raw.get("required_secrets") or [], set(map(str, services)))
    _validate_environment(raw.get("default_environment") or {}, "AppSpec environment")
    _validate_environment(raw.get("url_templates") or {}, "AppSpec URL templates", templates=True)
    spec = app_spec_from_dict(raw)
    from services.official_stacks.manifest_validator import validate_stack_manifest
    return validate_stack_manifest(spec)


def _validate_service(name: str, raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"AppSpec service '{name}' must be an object.")
    _reject_unknown(raw, _SERVICE, f"Service '{name}'")
    if not _NAME.fullmatch(name) or str(raw.get("name") or name) != name:
        raise ValueError("AppSpec service name is invalid.")
    pinned_digest = raw.get("pinned_digest")
    if pinned_digest is not None and not _IMAGE_DIGEST.fullmatch(str(pinned_digest)):
        raise ValueError(f"Service '{name}' pinned digest is invalid.")
    image = str(pinned_digest or raw.get("image_reference") or "")
    if not _IMAGE.fullmatch(image):
        raise ValueError(f"Service '{name}' image reference is invalid.")
    ports = raw.get("internal_ports")
    if not isinstance(ports, list) or not ports or any(not isinstance(port, int) or not 1 <= port <= 65535 for port in ports):
        raise ValueError(f"Service '{name}' private ports are invalid.")
    _validate_environment(raw.get("environment_defaults") or {}, f"Service '{name}' environment")
    dependencies = raw.get("depends_on") or []
    if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
        raise ValueError(f"Service '{name}' dependencies are invalid.")
    command = raw.get("command")
    if command is not None:
        if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
            raise ValueError(f"Service '{name}' command must be an argument list.")
        if any(marker in token for marker in _FORBIDDEN_COMMANDS for token in command):
            raise ValueError(f"Service '{name}' command contains a forbidden capability.")
    for volume in raw.get("volumes") or []:
        _validate_volume(name, volume)
    health = raw.get("health_check")
    if health is not None:
        if not isinstance(health, dict):
            raise ValueError(f"Service '{name}' health check is invalid.")
        _reject_unknown(health, _HEALTH, f"Service '{name}' health check")


def _validate_volume(service_name: str, raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"Service '{service_name}' volume is invalid.")
    _reject_unknown(raw, _VOLUME, f"Service '{service_name}' volume")
    name = str(raw.get("name_suffix") or "")
    path = str(raw.get("container_mount_path") or "")
    if not _VOLUME_NAME.fullmatch(name):
        raise ValueError(f"Service '{service_name}' volume name is invalid.")
    if not path.startswith("/") or path == "/" or ".." in path.split("/"):
        raise ValueError(f"Service '{service_name}' volume path is invalid.")
    if any(path == forbidden or path.startswith(f"{forbidden}/") for forbidden in _FORBIDDEN_PATHS):
        raise ValueError(f"Service '{service_name}' volume path is forbidden.")


def _validate_secrets(raw: Any, services: set[str]) -> None:
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("AppSpec secret requirements are invalid.")
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("AppSpec secret requirements must be objects.")
        _reject_unknown(item, _SECRET, "AppSpec secret requirement")
        generator = item.get("generator")
        if not isinstance(generator, str) or generator not in _GENERATORS:
            raise ValueError("AppSpec secret requirement generator is required and must be supported.")
        if not _ENV.fullmatch(str(item.get("key") or "")):
            raise ValueError("AppSpec secret key is invalid.")
        target = item.get("service_name")
        if target is not None and target not in services:
            raise ValueError("AppSpec secret targets an unknown service.")
        environment_key = item.get("environment_key")
        if environment_key is not None and not _ENV.fullmatch(str(environment_key)):
            raise ValueError("AppSpec secret environment key is invalid.")


def _validate_environment(raw: Any, label: str, *, templates: bool = False) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object.")
    for key, value in raw.items():
        if not _ENV.fullmatch(str(key)) or not isinstance(value, str) or len(value) > 4096 or "\n" in value or "\r" in value:
            raise ValueError(f"{label} contains an invalid value.")
        if not templates and any(token in str(key) for token in ("PASSWORD", "SECRET", "TOKEN", "PRIVATE_KEY", "API_KEY")):
            raise ValueError(f"{label} cannot contain secret values.")


def _reject_unknown(raw: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(unknown))}.")
