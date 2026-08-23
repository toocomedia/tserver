"""Data schema and contracts for Official Vendor Compose Stacks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class VolumeDefinition:
    """Named volume required by a stack service."""
    name_suffix: str
    container_mount_path: str
    read_only: bool = False
    description: str = "Persistent data volume"


@dataclass(frozen=True)
class ConfigFileDefinition:
    """Internal vendor config file materialized into the stack container."""
    asset_name: str
    container_target_path: str
    read_only: bool = True


@dataclass(frozen=True)
class HealthCheckDefinition:
    """Container health check probe specification."""
    probe_type: str  # "command" | "http"
    command: list[str] | None = None
    http_path: str | None = None
    http_port: int | None = None
    interval_seconds: int = 5
    timeout_seconds: int = 5
    retries: int = 15
    start_period_seconds: int = 20


@dataclass(frozen=True)
class ServiceDefinition:
    """Approved service container within an official stack."""
    name: str
    image_reference: str
    pinned_tag: str
    pinned_digest: str | None = None
    internal_ports: list[int] = field(default_factory=list)
    volumes: list[VolumeDefinition] = field(default_factory=list)
    config_files: list[ConfigFileDefinition] = field(default_factory=list)
    health_check: HealthCheckDefinition | None = None
    depends_on: list[str] = field(default_factory=list)
    memory_limit_mb: int = 512
    cpu_limit: str = "1.0"
    environment_defaults: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    is_web_entrypoint: bool = False


@dataclass(frozen=True)
class SecretRequirement:
    """Cryptographic secret generated and held exclusively by the panel vault."""
    key: str
    purpose: str
    generator: str = "urlsafe64"  # "urlsafe64" | "base64_48" | "hex32" | "password"


@dataclass(frozen=True)
class OfficialStackDefinition:
    """Authoritative vendor stack manifest owned by the panel."""
    catalog_id: str
    display_name: str
    vendor_name: str
    description: str
    official_repositories: list[str]
    allowed_versions: list[str]
    default_version: str
    services: dict[str, ServiceDefinition]
    startup_order: list[str]
    web_service_name: str
    web_internal_port: int
    web_health_path: str
    startup_timeout_seconds: int = 60
    recommended_ram_mb: int = 2048
    minimum_ram_mb: int = 1536
    allowed_nonsecret_settings: list[str] = field(default_factory=list)
    required_secrets: list[SecretRequirement] = field(default_factory=list)
    post_install_message: str = ""
    docs_url: str = ""
