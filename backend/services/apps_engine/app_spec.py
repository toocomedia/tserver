"""Canonical typed application specification for Compose App Engine snapshots."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VolumeSpec:
    """Panel-owned named volume mounted inside one service."""

    name_suffix: str
    container_mount_path: str
    read_only: bool = False
    description: str = "Persistent data volume"


@dataclass(frozen=True)
class ConfigFileSpec:
    """Legacy panel-owned config material retained for snapshot compatibility."""

    filename: str
    container_target_path: str
    content: str = ""
    read_only: bool = True


@dataclass(frozen=True)
class HealthCheckSpec:
    """Private readiness probe for one service."""

    probe_type: str
    command: list[str] | None = None
    http_path: str | None = None
    http_port: int | None = None
    interval_seconds: int = 5
    timeout_seconds: int = 5
    retries: int = 15
    start_period_seconds: int = 20


@dataclass(frozen=True)
class ServiceSpec:
    """One isolated container in an AppSpec."""

    name: str
    image_reference: str
    pinned_digest: str | None = None
    internal_ports: list[int] = field(default_factory=list)
    volumes: list[VolumeSpec] = field(default_factory=list)
    health_check: HealthCheckSpec | None = None
    depends_on: list[str] = field(default_factory=list)
    environment_defaults: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    cpu_limit: str = "1.0"
    memory_limit_mb: int = 512
    pinned_tag: str = "latest"
    config_files: list[ConfigFileSpec] = field(default_factory=list)
    is_web_entrypoint: bool = False


@dataclass(frozen=True, init=False)
class SecretRequirement:
    """Generated secret declaration. Generator is always explicit."""

    key: str
    purpose: str
    generator: str
    rotate: bool = False
    service_name: str | None = None
    environment_key: str | None = None

    def __init__(
        self,
        key: str,
        purpose: str,
        generator: str,
        rotate: bool | str = False,
        service_name: str | None = None,
        environment_key: str | None = None,
    ) -> None:
        # Old stored/test callers used positional service and environment values.
        if isinstance(rotate, str):
            environment_key = service_name
            service_name = rotate
            rotate = False
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "generator", generator)
        object.__setattr__(self, "rotate", bool(rotate))
        object.__setattr__(self, "service_name", service_name)
        object.__setattr__(self, "environment_key", environment_key)


@dataclass(frozen=True, init=False)
class AppSpec:
    """Canonical application topology plus legacy read-only metadata."""

    catalog_id: str
    display_name: str
    services: dict[str, ServiceSpec]
    web_service_name: str
    web_internal_port: int
    required_secrets: list[SecretRequirement]
    default_environment: dict[str, str]
    url_templates: dict[str, str]
    vendor_name: str
    description: str
    official_repositories: list[str]
    allowed_versions: list[str]
    default_version: str
    startup_order: list[str]
    web_health_path: str
    startup_timeout_seconds: int
    recommended_ram_mb: int
    minimum_ram_mb: int
    allowed_nonsecret_settings: list[str]
    post_install_message: str
    docs_url: str

    def __init__(
        self,
        name: str | None = None,
        display_name: str = "",
        web_service_name: str = "",
        web_port: int | None = None,
        services: dict[str, ServiceSpec] | None = None,
        required_secrets: list[SecretRequirement] | None = None,
        default_environment: dict[str, str] | None = None,
        url_templates: dict[str, str] | None = None,
        *,
        catalog_id: str | None = None,
        web_internal_port: int | None = None,
        vendor_name: str = "",
        description: str = "",
        official_repositories: list[str] | None = None,
        allowed_versions: list[str] | None = None,
        default_version: str = "proposal",
        startup_order: list[str] | None = None,
        web_health_path: str = "",
        startup_timeout_seconds: int = 60,
        recommended_ram_mb: int = 2048,
        minimum_ram_mb: int = 512,
        allowed_nonsecret_settings: list[str] | None = None,
        post_install_message: str = "",
        docs_url: str = "",
    ) -> None:
        app_name = str(name or catalog_id or "").strip()
        service_map = dict(services or {})
        entrypoint = str(web_service_name or "").strip()
        port = int(web_port if web_port is not None else (web_internal_port or 0))
        order = list(startup_order or service_map.keys())
        versions = list(allowed_versions or [default_version])
        values: dict[str, Any] = {
            "catalog_id": app_name,
            "display_name": display_name or app_name,
            "services": service_map,
            "web_service_name": entrypoint,
            "web_internal_port": port,
            "required_secrets": list(required_secrets or []),
            "default_environment": dict(default_environment or {}),
            "url_templates": dict(url_templates or {}),
            "vendor_name": vendor_name,
            "description": description,
            "official_repositories": list(official_repositories or []),
            "allowed_versions": versions,
            "default_version": default_version,
            "startup_order": order,
            "web_health_path": web_health_path,
            "startup_timeout_seconds": int(startup_timeout_seconds),
            "recommended_ram_mb": int(recommended_ram_mb),
            "minimum_ram_mb": int(minimum_ram_mb),
            "allowed_nonsecret_settings": list(allowed_nonsecret_settings or []),
            "post_install_message": post_install_message,
            "docs_url": docs_url,
        }
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def name(self) -> str:
        return self.catalog_id

    @property
    def web_port(self) -> int:
        return self.web_internal_port

