"""Export canonical AppSpec as standard, clean Docker Compose YAML."""
from __future__ import annotations

from typing import Any
import yaml

from services.apps_engine.app_spec import AppSpec


def app_spec_to_compose_dict(
    spec: AppSpec,
    environment_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert an AppSpec into a standard docker-compose dictionary."""
    env_vals = environment_values or {}
    services_dict: dict[str, Any] = {}
    volumes_dict: dict[str, Any] = {}

    for sname, service in spec.services.items():
        s_data: dict[str, Any] = {
            "image": service.pinned_digest or service.image_reference,
            "restart": "always",
        }
        if service.internal_ports:
            if sname == spec.web_service_name and spec.web_port:
                s_data["ports"] = [f"{spec.web_port}:{spec.web_port}"]
            else:
                s_data["expose"] = [str(p) for p in service.internal_ports]

        combined_env: dict[str, str] = {}
        combined_env.update(service.environment_defaults)
        for k, v in env_vals.items():
            if k in spec.allowed_nonsecret_settings or k in service.environment_defaults:
                combined_env[k] = v
        if combined_env:
            s_data["environment"] = combined_env

        if service.command:
            s_data["command"] = list(service.command)

        if service.depends_on:
            s_data["depends_on"] = list(service.depends_on)

        if service.volumes:
            v_list = []
            for vol in service.volumes:
                vol_name = vol.name_suffix
                volumes_dict[vol_name] = {}
                v_list.append(f"{vol_name}:{vol.container_mount_path}{':ro' if vol.read_only else ''}")
            s_data["volumes"] = v_list

        if service.health_check and service.health_check.probe_type == "http" and service.health_check.http_path:
            port = service.health_check.http_port or (service.internal_ports[0] if service.internal_ports else 80)
            s_data["healthcheck"] = {
                "test": ["CMD", "curl", "-f", f"http://localhost:{port}{service.health_check.http_path}"],
                "interval": f"{service.health_check.interval_seconds}s",
                "timeout": f"{service.health_check.timeout_seconds}s",
                "retries": service.health_check.retries,
            }

        services_dict[sname] = s_data

    result: dict[str, Any] = {
        "version": "3.8",
        "services": services_dict,
    }
    if volumes_dict:
        result["volumes"] = volumes_dict
    return result


def app_spec_to_compose_yaml(
    spec: AppSpec,
    environment_values: dict[str, str] | None = None,
) -> str:
    """Format AppSpec as human-readable standard docker-compose.yml."""
    raw = app_spec_to_compose_dict(spec, environment_values)
    return yaml.safe_dump(raw, sort_keys=False, default_flow_style=False)
