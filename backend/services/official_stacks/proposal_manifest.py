"""Parse the narrow structured stack proposal accepted from AI setup chat."""
from __future__ import annotations

import re
from typing import Any

from services.official_stacks.manifest_validator import validate_stack_manifest
from services.official_stacks.proposal_normalizer import normalize_stack_proposal_manifest
from services.official_stacks.schema import (
    HealthCheckDefinition,
    OfficialStackDefinition,
    SecretRequirement,
    ServiceDefinition,
    VolumeDefinition,
)

_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_GENERATOR = {"urlsafe64", "base64_32", "base64_48", "base64_64", "hex32", "hex64", "password"}
_SECRET_ENV = re.compile(r"(?:^|_)(?:PASSWORD|PASS|SECRET|TOKEN|API_KEY|PRIVATE_KEY|SECRET_KEY_BASE)$")
_TOP_LEVEL = {
    "name", "display_name", "vendor_name", "description", "source_repositories", "version",
    "services", "startup_order", "web_service", "web_port", "web_health_path",
    "startup_timeout_seconds", "recommended_ram_mb", "minimum_ram_mb",
    "allowed_nonsecret_settings", "default_environment", "url_templates", "secrets", "docs_url",
    "post_install_message",
}
_SERVICE = {"name", "image", "ports", "internal_ports", "depends_on", "environment", "volumes", "resources", "command", "health"}
_VOLUME = {
    "name", "name_suffix", "volume", "source", "label", "mount_path",
    "container_mount_path", "target", "destination", "path", "mount",
    "target_path", "container_path", "read_only", "readonly", "description", "type",
}
_HEALTH = {"type", "command", "interval_seconds", "timeout_seconds", "retries", "start_period_seconds"}
_DEFAULT_INTERNAL_PORTS = {
    "postgres": [5432],
    "postgresql": [5432],
    "mysql": [3306],
    "mariadb": [3306],
    "clickhouse": [8123, 9000],
    "redis": [6379],
    "valkey": [6379],
    "keydb": [6379],
    "mongo": [27017],
    "mongodb": [27017],
}


def stack_from_proposal(raw: Any, evidence: list[str] | None = None) -> OfficialStackDefinition:
    """Convert an evidence-backed field manifest; reject Compose/YAML and hidden options."""
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Stack proposal requires a structured manifest object.")
    raw = normalize_stack_proposal_manifest(raw)
    unknown = set(raw) - _TOP_LEVEL
    if unknown:
        raise ValueError(f"Stack proposal has unsupported fields: {', '.join(sorted(unknown))}.")
    if any(key in raw for key in {"compose", "docker_compose", "yaml", "networks", "ports", "privileged", "cap_add", "volumes_from"}):
        raise ValueError("Raw Compose fields are not accepted.")

    name = _text(raw.get("name"), "Stack name", 48)
    if not _NAME.fullmatch(name):
        raise ValueError("Stack name must use lowercase letters, digits, '_' or '-'.")
    services_raw = raw.get("services")
    if not isinstance(services_raw, list) or not services_raw:
        raise ValueError("Stack proposal requires a non-empty services list.")
    if len(services_raw) > 8:
        raise ValueError("Stack proposal can contain at most eight services.")
    version = _text(raw.get("version") or "proposal", "Version", 80)
    web_service = _text(raw.get("web_service"), "Web service", 48)
    services = {service.name: service for service in (_service(item) for item in services_raw)}
    if len(services) != len(services_raw):
        raise ValueError("Stack service names must be unique.")

    try:
        web_port = int(raw.get("web_port"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Web port is invalid.") from exc
    if web_service not in services:
        raise ValueError("Web service and web port must match a declared service port.")
    if web_port not in services[web_service].internal_ports:
        services[web_service] = _with_internal_port(services[web_service], web_port)
    services[web_service] = _as_web_service(services[web_service])

    evidence_items = [item for item in (evidence or []) if isinstance(item, str) and item.strip()]
    health_path = str(raw.get("web_health_path") or "").strip()
    if health_path and not evidence_items:
        raise ValueError("HTTP health path requires source or vendor evidence.")
    if health_path and (not health_path.startswith("/") or len(health_path) > 255):
        raise ValueError("HTTP health path must be an absolute path.")
    if any(service.health_check for service in services.values()) and not evidence_items:
        raise ValueError("Service health checks require source or vendor evidence.")

    stack = OfficialStackDefinition(
        catalog_id=name,
        display_name=_text(raw.get("display_name") or name, "Display name", 120),
        vendor_name=str(raw.get("vendor_name") or "").strip()[:120],
        description=str(raw.get("description") or "").strip()[:1000],
        official_repositories=_url_list(raw.get("source_repositories")),
        allowed_versions=[version],
        default_version=version,
        services=services,
        startup_order=_names(raw.get("startup_order"), "Startup order"),
        web_service_name=web_service,
        web_internal_port=web_port,
        web_health_path=health_path,
        startup_timeout_seconds=_number(raw.get("startup_timeout_seconds", 90), "Startup timeout", 30, 900),
        recommended_ram_mb=_number(raw.get("recommended_ram_mb", 1024), "Recommended RAM", 128, 16384),
        minimum_ram_mb=_number(raw.get("minimum_ram_mb", 512), "Minimum RAM", 64, 16384),
        allowed_nonsecret_settings=_env_names(raw.get("allowed_nonsecret_settings"), "Allowed setting"),
        default_environment=_environment(raw.get("default_environment")),
        url_templates=_url_templates(raw.get("url_templates")),
        required_secrets=_secrets(raw.get("secrets"), services),
        docs_url=str(raw.get("docs_url") or "").strip()[:1024],
        post_install_message=str(raw.get("post_install_message") or "").strip()[:1024],
    )
    return validate_stack_manifest(stack)


def validate_stack_settings(stack: OfficialStackDefinition, settings: Any) -> dict[str, str]:
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise ValueError("Stack non-secret settings must be an object.")
    clean: dict[str, str] = {}
    for key, value in settings.items():
        if not isinstance(key, str) or key not in stack.allowed_nonsecret_settings:
            raise ValueError(f"Stack setting '{key}' is not declared by its manifest.")
        text = str(value) if value is not None else ""
        if len(text) > 4096 or "\r" in text or "\n" in text:
            raise ValueError(f"Stack setting '{key}' is invalid.")
        clean[key] = text
    return clean


def _service(raw: Any) -> ServiceDefinition:
    if not isinstance(raw, dict):
        raise ValueError("Each stack service must be an object.")
    unknown = set(raw) - _SERVICE
    if unknown:
        raise ValueError(f"Stack service has unsupported fields: {', '.join(sorted(unknown))}.")
    name = _text(raw.get("name"), "Service name", 48)
    if not _NAME.fullmatch(name):
        raise ValueError("Service name is invalid.")
    image = _text(raw.get("image"), f"Image for {name}", 512)
    if not image:
        raise ValueError(f"Service '{name}' needs a valid Docker image reference.")
    if "@" not in image and ":" not in image.rsplit("/", 1)[-1]:
        image = f"{image}:latest"
    ports = raw.get("ports", raw.get("internal_ports"))
    if ports is None or ports == []:
        ports = _default_internal_ports(name, image)
    ports = _normalize_ports(ports)
    if not ports:
        raise ValueError(f"Service '{name}' needs private internal ports.")
    parsed_ports = [_number(port, f"Port for {name}", 1, 65535) for port in ports]
    if len(set(parsed_ports)) != len(parsed_ports):
        raise ValueError(f"Service '{name}' has duplicate ports.")
    resources = raw.get("resources") or {}
    if not isinstance(resources, dict) or set(resources) - {"memory_mb", "cpu"}:
        raise ValueError(f"Service '{name}' resources are invalid.")
    command = raw.get("command")
    if command is not None and (not isinstance(command, list) or not all(isinstance(item, str) for item in command)):
        raise ValueError(f"Service '{name}' command must be an argument list.")
    tag = image.rsplit(":", 1)[-1] if ":" in image else "latest"
    return ServiceDefinition(
        name=name, image_reference=image, pinned_tag=tag,
        internal_ports=parsed_ports, depends_on=_names(raw.get("depends_on", []), "Dependency", allow_empty=True),
        environment_defaults=_environment(raw.get("environment")), volumes=_volumes(raw.get("volumes"), name),
        health_check=_health(raw.get("health"), name), memory_limit_mb=_number(resources.get("memory_mb", 512), f"Memory for {name}", 64, 16384),
        cpu_limit=str(resources.get("cpu", "1.0")), command=command,
    )


def _volumes(raw: Any, service: str) -> list[VolumeDefinition]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 12:
        raise ValueError(f"Service '{service}' volumes are invalid.")
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(_volume_from_string(item, service))
            continue
        if isinstance(item, dict):
            result.append(_volume_from_mapping(item, service))
            continue
        raise ValueError(f"Service '{service}' volume is invalid.")
    return result


def _default_internal_ports(name: str, image: str) -> list[int]:
    text = f"{name} {image}".lower()
    for marker, ports in _DEFAULT_INTERNAL_PORTS.items():
        if marker in text:
            return list(ports)
    return []


def _normalize_ports(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        values = [int(item) for item in re.findall(r"(?<![0-9.])(\d{1,5})(?![0-9.])", raw)]
        return [values[-1]] if values else []
    return []


def _volume_from_string(raw: str, service: str) -> VolumeDefinition:
    text = raw.strip()
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Service '{service}' volume is invalid.")
    source, target = parts[0].strip(), parts[1].strip()
    mode = parts[2].strip().lower() if len(parts) == 3 else ""
    if mode and mode not in {"ro", "rw"}:
        raise ValueError(f"Service '{service}' volume is invalid.")
    return _volume_definition(source, target, read_only=(mode == "ro"))


def _volume_from_mapping(item: dict[str, Any], service: str) -> VolumeDefinition:
    if set(item) - _VOLUME:
        raise ValueError(f"Service '{service}' volume is invalid.")
    volume_type = str(item.get("type") or "volume").strip().lower()
    if volume_type not in {"volume", "named_volume"}:
        raise ValueError(f"Service '{service}' volume is invalid.")
    source = item.get("name") or item.get("name_suffix") or item.get("volume") or item.get("source") or item.get("label")
    target = (
        item.get("mount_path")
        or item.get("container_mount_path")
        or item.get("target")
        or item.get("destination")
        or item.get("path")
        or item.get("mount")
        or item.get("target_path")
        or item.get("container_path")
    )
    return _volume_definition(
        str(source or ""),
        str(target or ""),
        read_only=bool(item.get("read_only", item.get("readonly", False))),
        description=str(item.get("description") or "Persistent data volume")[:256],
    )


def _volume_definition(
    source: str,
    target: str,
    *,
    read_only: bool = False,
    description: str = "Persistent data volume",
) -> VolumeDefinition:
    suffix = _text(source, "Volume name", 64)
    mount_path = _text(target, "Volume mount path", 256)
    if _looks_like_host_mount(suffix):
        raise ValueError("Host mounts are not allowed in stack proposals.")
    return VolumeDefinition(suffix, mount_path, read_only, description)


def _looks_like_host_mount(source: str) -> bool:
    return (
        source.startswith(("/", ".", "~"))
        or "\\" in source
        or "/" in source
        or re.match(r"^[A-Za-z]:", source) is not None
    )


def _health(raw: Any, service: str) -> HealthCheckDefinition | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - _HEALTH or raw.get("type") != "command":
        raise ValueError(f"Service '{service}' health must be an evidence-backed command probe.")
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError(f"Service '{service}' health command is invalid.")
    return HealthCheckDefinition(
        "command", command=command,
        interval_seconds=_number(raw.get("interval_seconds", 5), "Health interval", 1, 300),
        timeout_seconds=_number(raw.get("timeout_seconds", 5), "Health timeout", 1, 120),
        retries=_number(raw.get("retries", 15), "Health retries", 1, 120),
        start_period_seconds=_number(raw.get("start_period_seconds", 20), "Health start period", 0, 600),
    )


def _secrets(raw: Any, services: dict[str, ServiceDefinition]) -> list[SecretRequirement]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("Stack secrets are invalid.")
    result = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"key", "purpose", "generator", "service", "environment"}:
            raise ValueError("Stack secret has unsupported fields.")
        key = _text(item.get("key"), "Secret key", 128)
        target = _text(item.get("service"), f"Secret '{key}' service", 48)
        environment = _text(item.get("environment"), f"Secret '{key}' environment", 128)
        if not _KEY.fullmatch(key) or not _KEY.fullmatch(environment) or target not in services:
            raise ValueError(f"Stack secret '{key}' target is invalid.")
        generator = str(item.get("generator") or "urlsafe64").strip()
        if generator not in _GENERATOR:
            raise ValueError(f"Stack secret '{key}' generator is invalid.")
        result.append(SecretRequirement(key, _text(item.get("purpose"), f"Secret '{key}' purpose", 256), generator, target, environment))
    return result


def _environment(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Stack environment must be an object.")
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or _SECRET_ENV.search(key) or not isinstance(value, str) or len(value) > 4096 or "\n" in value or "{" in value:
            raise ValueError("Stack environment contains an invalid value or secret placeholder.")
        result[key] = value
    return result


def _names(raw: Any, label: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(raw, list) or (not allow_empty and not raw) or not all(isinstance(item, str) and _NAME.fullmatch(item) for item in raw):
        raise ValueError(f"{label} must be a non-empty service-name list.")
    if len(set(raw)) != len(raw):
        raise ValueError(f"{label} contains duplicates.")
    return list(raw)


def _env_names(raw: Any, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) and _KEY.fullmatch(item) for item in raw):
        raise ValueError(f"{label} is invalid.")
    return list(dict.fromkeys(raw))


def _url_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 8 or not all(isinstance(item, str) and item.startswith(("https://", "http://", "git@", "ssh://")) for item in raw):
        raise ValueError("Source repositories are invalid.")
    return [item[:1024] for item in raw]


def _url_templates(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Stack URL templates must be an object.")
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(value, str) or not value or len(value) > 2048 or "\n" in value:
            raise ValueError("Stack URL template is invalid.")
        result[key] = value
    return result


def _number(raw: Any, label: str, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid.") from exc
    if value < low or value > high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return value


def _text(raw: Any, label: str, maximum: int) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > maximum:
        raise ValueError(f"{label} is invalid.")
    return raw.strip()


def _as_web_service(service: ServiceDefinition) -> ServiceDefinition:
    return ServiceDefinition(**{**service.__dict__, "is_web_entrypoint": True})


def _with_internal_port(service: ServiceDefinition, port: int) -> ServiceDefinition:
    ports = list(service.internal_ports)
    if port not in ports:
        ports.append(port)
    return ServiceDefinition(**{**service.__dict__, "internal_ports": ports})
