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
    """Constructs an OfficialStackDefinition dynamically from dictionary / AI tool output with full polymorphism."""
    import json
    import shlex

    # 1. Parse raw_services
    raw_services = data.get("services") or {}
    if isinstance(raw_services, str):
        try:
            raw_services = json.loads(raw_services)
        except Exception:
            raw_services = {}

    services: dict[str, ServiceDefinition] = {}
    for sname, sdata in raw_services.items():
        if isinstance(sdata, ServiceDefinition):
            services[sname] = sdata
            continue
        if isinstance(sdata, str):
            sdata = {"image_reference": sdata}
        elif not isinstance(sdata, dict):
            sdata = {}

        # Parse volumes
        vols: list[VolumeDefinition] = []
        raw_vols = sdata.get("volumes") or []
        if isinstance(raw_vols, str):
            raw_vols = [raw_vols]
        for i, v in enumerate(raw_vols):
            if isinstance(v, VolumeDefinition):
                vols.append(v)
            elif isinstance(v, str):
                if ":" in v:
                    parts = v.split(":", 1)
                    vols.append(VolumeDefinition(
                        name_suffix=parts[0].strip() or f"vol-{i}",
                        container_mount_path=parts[1].strip(),
                    ))
                else:
                    vols.append(VolumeDefinition(
                        name_suffix=f"vol-{i}",
                        container_mount_path=v.strip(),
                    ))
            elif isinstance(v, dict):
                vols.append(VolumeDefinition(
                    name_suffix=str(v.get("name_suffix") or v.get("name") or v.get("volume") or f"vol-{i}").strip(),
                    container_mount_path=str(v.get("container_mount_path") or v.get("mount_path") or v.get("path") or "").strip(),
                    read_only=bool(v.get("read_only", False)),
                    description=str(v.get("description", "Persistent data volume")),
                ))

        # Parse config_files
        cfgs: list[ConfigFileDefinition] = []
        raw_cfgs = sdata.get("config_files") or []
        if isinstance(raw_cfgs, list):
            for c in raw_cfgs:
                if isinstance(c, ConfigFileDefinition):
                    cfgs.append(c)
                elif isinstance(c, dict):
                    cfgs.append(ConfigFileDefinition(
                        filename=str(c.get("filename") or "").strip(),
                        container_target_path=str(c.get("container_target_path") or c.get("path") or "").strip(),
                        content=str(c.get("content") or ""),
                        read_only=bool(c.get("read_only", True)),
                    ))

        # Parse health_check
        hc: HealthCheckDefinition | None = None
        raw_hc = sdata.get("health_check")
        if isinstance(raw_hc, HealthCheckDefinition):
            hc = raw_hc
        elif isinstance(raw_hc, str):
            if raw_hc.startswith("/") or raw_hc.startswith("http"):
                hc = HealthCheckDefinition(probe_type="http", http_path=raw_hc)
            else:
                try:
                    cmd_parts = shlex.split(raw_hc)
                except Exception:
                    cmd_parts = raw_hc.split()
                hc = HealthCheckDefinition(probe_type="command", command=cmd_parts)
        elif isinstance(raw_hc, dict):
            ptype = str(raw_hc.get("probe_type") or ("http" if "http_path" in raw_hc or "http_port" in raw_hc else "command")).strip()
            raw_cmd = raw_hc.get("command") or raw_hc.get("test")
            cmd_list = None
            if isinstance(raw_cmd, str):
                try:
                    cmd_list = shlex.split(raw_cmd)
                except Exception:
                    cmd_list = raw_cmd.split()
            elif isinstance(raw_cmd, list):
                cmd_list = [str(x) for x in raw_cmd]

            hc = HealthCheckDefinition(
                probe_type=ptype,
                command=cmd_list,
                http_path=str(raw_hc.get("http_path") or raw_hc.get("path") or "/api/health") if ptype == "http" else None,
                http_port=int(raw_hc.get("http_port") or raw_hc.get("port") or 8000) if ptype == "http" else None,
                interval_seconds=int(raw_hc.get("interval_seconds", 5)),
                timeout_seconds=int(raw_hc.get("timeout_seconds", 5)),
                retries=int(raw_hc.get("retries", 15)),
                start_period_seconds=int(raw_hc.get("start_period_seconds", 20)),
            )

        # Parse internal_ports
        raw_ports = sdata.get("internal_ports") or sdata.get("ports") or []
        ports: list[int] = []
        if isinstance(raw_ports, int):
            ports = [raw_ports]
        elif isinstance(raw_ports, str):
            for p in raw_ports.replace(",", " ").split():
                if p.isdigit():
                    ports.append(int(p))
        elif isinstance(raw_ports, list):
            for p in raw_ports:
                if isinstance(p, int):
                    ports.append(p)
                elif isinstance(p, str) and p.isdigit():
                    ports.append(int(p))

        # Image reference & tag
        img_ref = str(sdata.get("image_reference") or sdata.get("image") or "").strip()
        pinned_tag = str(sdata.get("pinned_tag") or (img_ref.split(":")[-1] if ":" in img_ref else "latest")).strip()

        # Parse command
        raw_command = sdata.get("command") or sdata.get("cmd")
        cmd_val = None
        if isinstance(raw_command, str):
            cmd_val = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            cmd_val = [str(x) for x in raw_command]

        services[sname] = ServiceDefinition(
            name=str(sdata.get("name") or sname).strip(),
            image_reference=img_ref,
            pinned_tag=pinned_tag,
            pinned_digest=sdata.get("pinned_digest"),
            internal_ports=ports,
            volumes=vols,
            config_files=cfgs,
            health_check=hc,
            depends_on=list(sdata.get("depends_on") or []),
            memory_limit_mb=int(sdata.get("memory_limit_mb", 512)),
            cpu_limit=str(sdata.get("cpu_limit", "1.0")),
            environment_defaults=dict(sdata.get("environment_defaults") or {}),
            command=cmd_val,
            is_web_entrypoint=bool(sdata.get("is_web_entrypoint", False)),
        )

    # 2. Parse required_secrets
    secrets: list[SecretRequirement] = []
    raw_secrets = data.get("required_secrets") or []
    if isinstance(raw_secrets, str):
        try:
            raw_secrets = json.loads(raw_secrets)
        except Exception:
            raw_secrets = [raw_secrets]
    if isinstance(raw_secrets, list):
        for sec in raw_secrets:
            if isinstance(sec, SecretRequirement):
                secrets.append(sec)
            elif isinstance(sec, str):
                s_key = sec.strip()
                if s_key:
                    secrets.append(SecretRequirement(key=s_key, purpose=f"Secret key for {s_key}"))
            elif isinstance(sec, dict):
                s_key = str(sec.get("key") or sec.get("name") or "").strip()
                if s_key:
                    secrets.append(SecretRequirement(
                        key=s_key,
                        purpose=str(sec.get("purpose") or sec.get("description") or f"Secret key for {s_key}"),
                        generator=str(sec.get("generator", "urlsafe64")),
                    ))

    # 3. Parse startup_order & web service
    raw_order = data.get("startup_order") or []
    if isinstance(raw_order, str):
        startup_order = [s.strip() for s in raw_order.split(",") if s.strip()]
    elif isinstance(raw_order, list):
        startup_order = [str(s).strip() for s in raw_order if str(s).strip()]
    else:
        startup_order = list(services.keys())

    if not startup_order:
        startup_order = list(services.keys())

    web_svc = str(data.get("web_service_name") or (startup_order[-1] if startup_order else "web")).strip()
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

    # 4. Parse dictionaries safely
    def _safe_dict(raw: Any) -> dict[str, str]:
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
        if isinstance(raw, str):
            try:
                res = json.loads(raw)
                if isinstance(res, dict):
                    return {str(k): str(v) for k, v in res.items()}
            except Exception:
                pass
        return {}

    url_templates = _safe_dict(data.get("url_templates"))
    default_env = _safe_dict(data.get("default_environment") or data.get("environment_defaults"))

    return OfficialStackDefinition(
        catalog_id=str(data.get("catalog_id", "custom_stack")).strip() or "custom_stack",
        display_name=str(data.get("display_name", "Official Stack")).strip() or "Official Stack",
        vendor_name=str(data.get("vendor_name", "Vendor")).strip(),
        description=str(data.get("description", "")).strip(),
        official_repositories=list(data.get("official_repositories") or []),
        allowed_versions=list(data.get("allowed_versions") or [str(data.get("default_version", "latest"))]),
        default_version=str(data.get("default_version", "latest")),
        services=services,
        startup_order=startup_order,
        web_service_name=web_svc,
        web_internal_port=int(data.get("web_internal_port", 8000)),
        web_health_path=str(data.get("web_health_path", "/api/health")),
        startup_timeout_seconds=int(data.get("startup_timeout_seconds", 60)),
        recommended_ram_mb=int(data.get("recommended_ram_mb", 2048)),
        minimum_ram_mb=int(data.get("minimum_ram_mb", 1024)),
        allowed_nonsecret_settings=list(data.get("allowed_nonsecret_settings") or []),
        default_environment=default_env,
        url_templates=url_templates,
        required_secrets=secrets,
        post_install_message=str(data.get("post_install_message", "")),
        docs_url=str(data.get("docs_url", "")),
    )
