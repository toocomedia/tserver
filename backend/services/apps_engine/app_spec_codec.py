"""Deterministic AppSpec serialization and legacy snapshot decoding."""
from __future__ import annotations

import dataclasses
import json
import shlex
from typing import Any

from services.apps_engine.app_spec import (
    AppSpec,
    ConfigFileSpec,
    HealthCheckSpec,
    SecretRequirement,
    ServiceSpec,
    VolumeSpec,
)


def app_spec_to_dict(spec: AppSpec) -> dict[str, Any]:
    """Serialize only canonical v2 AppSpec fields."""
    result: dict[str, Any] = {
        "name": spec.name,
        "display_name": spec.display_name,
        "web_service_name": spec.web_service_name,
        "web_port": spec.web_port,
        "services": {name: _service_to_dict(service) for name, service in sorted(spec.services.items())},
        "required_secrets": [dataclasses.asdict(item) for item in spec.required_secrets],
        "default_environment": dict(sorted(spec.default_environment.items())),
        "url_templates": dict(sorted(spec.url_templates.items())),
    }
    if getattr(spec, "post_install_message", ""):
        result["post_install_message"] = str(spec.post_install_message)
    if getattr(spec, "docs_url", ""):
        result["docs_url"] = str(spec.docs_url)
    return result


def legacy_app_spec_to_dict(spec: AppSpec) -> dict[str, Any]:
    """Serialize old OfficialStack shape for mixed-version readers."""
    return dataclasses.asdict(spec)


def app_spec_from_dict(data: dict[str, Any], *, allow_legacy_secret_defaults: bool = False) -> AppSpec:
    """Decode canonical v2 or persisted legacy manifest data."""
    if not isinstance(data, dict):
        raise ValueError("AppSpec must be an object.")
    raw_services = data.get("services") or {}
    if isinstance(raw_services, str):
        try:
            raw_services = json.loads(raw_services)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_services = {}
    if isinstance(raw_services, list):
        raw_services = {
            str(item.get("name") or ""): item for item in raw_services if isinstance(item, dict)
        }
    if not isinstance(raw_services, dict):
        raise ValueError("AppSpec services must be an object.")
    services = {str(name): _service(str(name), raw) for name, raw in raw_services.items()}
    web_service = str(data.get("web_service_name") or data.get("web_service") or "").strip()
    if web_service in services and not services[web_service].is_web_entrypoint:
        services[web_service] = dataclasses.replace(services[web_service], is_web_entrypoint=True)
    version = str(data.get("default_version") or data.get("version") or "proposal")
    web_health = str(data.get("web_health_path") or "").strip()
    if not web_health and web_service in services:
        health = services[web_service].health_check
        if health and health.probe_type == "http":
            web_health = str(health.http_path or "").strip()
    return AppSpec(
        name=str(data.get("name") or data.get("catalog_id") or "").strip(),
        display_name=str(data.get("display_name") or data.get("name") or "").strip(),
        web_service_name=web_service,
        web_port=_integer(data.get("web_port", data.get("web_internal_port", 0)), 0),
        services=services,
        required_secrets=_secrets(data.get("required_secrets", data.get("secrets")), allow_legacy_secret_defaults),
        default_environment=_string_map(data.get("default_environment") or data.get("environment_defaults")),
        url_templates=_string_map(data.get("url_templates")),
        vendor_name=str(data.get("vendor_name") or "").strip(),
        description=str(data.get("description") or "").strip(),
        official_repositories=list(data.get("official_repositories") or data.get("source_repositories") or []),
        allowed_versions=list(data.get("allowed_versions") or [version]),
        default_version=version,
        startup_order=_string_list(data.get("startup_order")) or list(services),
        web_health_path=web_health,
        startup_timeout_seconds=_integer(data.get("startup_timeout_seconds"), 60),
        recommended_ram_mb=_integer(data.get("recommended_ram_mb"), 2048),
        minimum_ram_mb=_integer(data.get("minimum_ram_mb"), 512),
        allowed_nonsecret_settings=_string_list(data.get("allowed_nonsecret_settings")),
        post_install_message=str(data.get("post_install_message") or ""),
        docs_url=str(data.get("docs_url") or ""),
    )


def _service_to_dict(service: ServiceSpec) -> dict[str, Any]:
    return {
        "name": service.name,
        "image_reference": service.image_reference,
        "pinned_digest": service.pinned_digest,
        "internal_ports": list(service.internal_ports),
        "volumes": [dataclasses.asdict(item) for item in service.volumes],
        "health_check": dataclasses.asdict(service.health_check) if service.health_check else None,
        "depends_on": list(service.depends_on),
        "environment_defaults": dict(sorted(service.environment_defaults.items())),
        "command": list(service.command) if service.command else None,
        "cpu_limit": service.cpu_limit,
        "memory_limit_mb": service.memory_limit_mb,
    }


def _service(name: str, raw: Any) -> ServiceSpec:
    if isinstance(raw, ServiceSpec):
        return raw
    if isinstance(raw, str):
        raw = {"image_reference": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"AppSpec service '{name}' must be an object.")
    image = str(raw.get("image_reference") or raw.get("image") or "").strip()
    raw_command = raw.get("command") or raw.get("cmd")
    command = shlex.split(raw_command) if isinstance(raw_command, str) else raw_command
    return ServiceSpec(
        name=str(raw.get("name") or name).strip(),
        image_reference=image,
        pinned_digest=str(raw.get("pinned_digest") or "").strip() or None,
        internal_ports=_ports(raw.get("internal_ports", raw.get("ports"))),
        volumes=_volumes(raw.get("volumes"), name),
        health_check=_health(raw.get("health_check", raw.get("health"))),
        depends_on=_string_list(raw.get("depends_on")),
        environment_defaults=_string_map(raw.get("environment_defaults") or raw.get("environment")),
        command=[str(item) for item in command] if isinstance(command, list) else None,
        cpu_limit=str(raw.get("cpu_limit", (raw.get("resources") or {}).get("cpu", "1.0"))),
        memory_limit_mb=_integer(raw.get("memory_limit_mb", (raw.get("resources") or {}).get("memory_mb")), 512),
        pinned_tag=str(raw.get("pinned_tag") or _image_tag(image)),
        config_files=_config_files(raw.get("config_files")),
        is_web_entrypoint=bool(raw.get("is_web_entrypoint", False)),
    )


def _volumes(raw: Any, service_name: str) -> list[VolumeSpec]:
    result: list[VolumeSpec] = []
    for index, item in enumerate(raw if isinstance(raw, list) else ([raw] if isinstance(raw, str) else [])):
        if isinstance(item, VolumeSpec):
            result.append(item)
            continue
        if isinstance(item, str):
            parts = item.split(":")
            source, target = (parts + [""])[:2]
            result.append(VolumeSpec(source or f"{service_name}-vol-{index}", target, len(parts) > 2 and parts[2] == "ro"))
            continue
        if isinstance(item, dict):
            source = item.get("name_suffix") or item.get("name") or item.get("source") or f"{service_name}-vol-{index}"
            target = item.get("container_mount_path") or item.get("mount_path") or item.get("target") or ""
            result.append(VolumeSpec(str(source), str(target), bool(item.get("read_only", False)), str(item.get("description") or "Persistent data volume")))
    return result


def _health(raw: Any) -> HealthCheckSpec | None:
    if isinstance(raw, HealthCheckSpec):
        return raw
    if isinstance(raw, str):
        return HealthCheckSpec("http", http_path=raw) if raw.startswith("/") else HealthCheckSpec("command", command=shlex.split(raw))
    if not isinstance(raw, dict):
        return None
    probe_type = str(raw.get("probe_type") or raw.get("type") or ("http" if raw.get("http_path") else "command"))
    command = raw.get("command") or raw.get("test")
    if isinstance(command, str):
        command = shlex.split(command)
    return HealthCheckSpec(
        probe_type=probe_type,
        command=[str(item) for item in command] if isinstance(command, list) else None,
        http_path=str(raw.get("http_path") or raw.get("path") or "") or None,
        http_port=_integer(raw.get("http_port", raw.get("port")), 0) or None,
        interval_seconds=_integer(raw.get("interval_seconds"), 5),
        timeout_seconds=_integer(raw.get("timeout_seconds"), 5),
        retries=_integer(raw.get("retries"), 15),
        start_period_seconds=_integer(raw.get("start_period_seconds"), 20),
    )


def _secrets(raw: Any, allow_default: bool) -> list[SecretRequirement]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = []
    result: list[SecretRequirement] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, SecretRequirement):
            result.append(item)
            continue
        if not isinstance(item, dict):
            raise ValueError("AppSpec secret requirements must be objects.")
        if not item.get("generator") and not allow_default:
            raise ValueError("AppSpec secret requirement generator is required.")
        result.append(SecretRequirement(
            key=str(item.get("key") or item.get("name") or "").strip(),
            purpose=str(item.get("purpose") or item.get("description") or "Application secret").strip(),
            generator=str(item.get("generator") or "urlsafe64").strip(),
            rotate=bool(item.get("rotate", False)),
            service_name=str(item.get("service_name") or item.get("service") or "").strip() or None,
            environment_key=str(item.get("environment_key") or item.get("environment") or item.get("env") or "").strip() or None,
        ))
    return result


def _config_files(raw: Any) -> list[ConfigFileSpec]:
    return [ConfigFileSpec(
        filename=str(item.get("filename") or ""),
        container_target_path=str(item.get("container_target_path") or item.get("path") or ""),
        content=str(item.get("content") or ""),
        read_only=bool(item.get("read_only", True)),
    ) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _ports(raw: Any) -> list[int]:
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    return [int(item) for item in raw if str(item).isdigit()] if isinstance(raw, list) else []


def _string_map(raw: Any) -> dict[str, str]:
    return {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else {}


def _string_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _integer(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _image_tag(image: str) -> str:
    return image.rsplit(":", 1)[-1] if ":" in image.rsplit("/", 1)[-1] else "latest"
