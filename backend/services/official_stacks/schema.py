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
    filename: str
    container_target_path: str
    content: str = ""
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
    default_environment: dict[str, str] = field(default_factory=dict)
    url_templates: dict[str, str] = field(default_factory=dict)
    required_secrets: list[SecretRequirement] = field(default_factory=list)
    post_install_message: str = ""
    docs_url: str = ""


def stack_from_dict(data: dict[str, Any]) -> OfficialStackDefinition:
    """Constructs an OfficialStackDefinition dynamically from dictionary / AI tool output."""
    services: dict[str, ServiceDefinition] = {}
    raw_services = data.get("services") or {}
    for sname, sdata in raw_services.items():
        if isinstance(sdata, ServiceDefinition):
            services[sname] = sdata
            continue
        vols = [
            VolumeDefinition(
                name_suffix=v.get("name_suffix") or f"vol-{i}",
                container_mount_path=v.get("container_mount_path", ""),
                read_only=bool(v.get("read_only", False)),
                description=v.get("description", "Persistent data volume"),
            )
            for i, v in enumerate(sdata.get("volumes") or [])
        ]
        cfgs = [
            ConfigFileDefinition(
                filename=c.get("filename", ""),
                container_target_path=c.get("container_target_path", ""),
                content=c.get("content", ""),
                read_only=bool(c.get("read_only", True)),
            )
            for c in (sdata.get("config_files") or [])
        ]
        hc = None
        raw_hc = sdata.get("health_check")
        if raw_hc:
            hc = HealthCheckDefinition(
                probe_type=raw_hc.get("probe_type", "command"),
                command=raw_hc.get("command"),
                http_path=raw_hc.get("http_path"),
                http_port=raw_hc.get("http_port"),
                interval_seconds=int(raw_hc.get("interval_seconds", 5)),
                timeout_seconds=int(raw_hc.get("timeout_seconds", 5)),
                retries=int(raw_hc.get("retries", 15)),
                start_period_seconds=int(raw_hc.get("start_period_seconds", 20)),
            )
        services[sname] = ServiceDefinition(
            name=sdata.get("name") or sname,
            image_reference=sdata.get("image_reference", ""),
            pinned_tag=sdata.get("pinned_tag", "latest"),
            pinned_digest=sdata.get("pinned_digest"),
            internal_ports=list(sdata.get("internal_ports") or []),
            volumes=vols,
            config_files=cfgs,
            health_check=hc,
            depends_on=list(sdata.get("depends_on") or []),
            memory_limit_mb=int(sdata.get("memory_limit_mb", 512)),
            cpu_limit=str(sdata.get("cpu_limit", "1.0")),
            environment_defaults=dict(sdata.get("environment_defaults") or {}),
            command=sdata.get("command"),
            is_web_entrypoint=bool(sdata.get("is_web_entrypoint", False)),
        )

    secrets: list[SecretRequirement] = []
    for sec in (data.get("required_secrets") or []):
        if isinstance(sec, SecretRequirement):
            secrets.append(sec)
        elif isinstance(sec, dict):
            secrets.append(SecretRequirement(
                key=sec.get("key", ""),
                purpose=sec.get("purpose", ""),
                generator=sec.get("generator", "urlsafe64"),
            ))

    startup_order = list(data.get("startup_order") or list(services.keys()))
    web_svc = data.get("web_service_name") or (startup_order[-1] if startup_order else "web")
    if web_svc in services:
        services[web_svc] = ServiceDefinition(
            name=services[web_svc].name,
            image_reference=services[web_svc].image_reference,
            pinned_tag=services[web_svc].pinned_tag,
            pinned_digest=services[web_svc].pinned_digest,
            internal_ports=services[web_svc].internal_ports,
            volumes=services[web_svc].volumes,
            config_files=services[web_svc].config_files,
            health_check=services[web_svc].health_check,
            depends_on=services[web_svc].depends_on,
            memory_limit_mb=services[web_svc].memory_limit_mb,
            cpu_limit=services[web_svc].cpu_limit,
            environment_defaults=services[web_svc].environment_defaults,
            command=services[web_svc].command,
            is_web_entrypoint=True,
        )

    return OfficialStackDefinition(
        catalog_id=data.get("catalog_id", "custom_stack"),
        display_name=data.get("display_name", "Official Stack"),
        vendor_name=data.get("vendor_name", "Vendor"),
        description=data.get("description", ""),
        official_repositories=list(data.get("official_repositories") or []),
        allowed_versions=list(data.get("allowed_versions") or [data.get("default_version", "latest")]),
        default_version=data.get("default_version", "latest"),
        services=services,
        startup_order=startup_order,
        web_service_name=web_svc,
        web_internal_port=int(data.get("web_internal_port", 8000)),
        web_health_path=data.get("web_health_path", "/api/health"),
        startup_timeout_seconds=int(data.get("startup_timeout_seconds", 60)),
        recommended_ram_mb=int(data.get("recommended_ram_mb", 2048)),
        minimum_ram_mb=int(data.get("minimum_ram_mb", 1024)),
        allowed_nonsecret_settings=list(data.get("allowed_nonsecret_settings") or []),
        default_environment=dict(data.get("default_environment") or {}),
        url_templates=dict(data.get("url_templates") or {}),
        required_secrets=secrets,
        post_install_message=data.get("post_install_message", ""),
        docs_url=data.get("docs_url", ""),
    )
