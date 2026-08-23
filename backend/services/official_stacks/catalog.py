"""Official Vendor Stacks Catalog and registry."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from services.official_stacks.schema import (
    ConfigFileDefinition,
    HealthCheckDefinition,
    OfficialStackDefinition,
    SecretRequirement,
    ServiceDefinition,
    VolumeDefinition,
)

# Registry of authoritative, server-verified vendor stack definitions.
_CATALOG: Dict[str, OfficialStackDefinition] = {}


def register_stack(stack: OfficialStackDefinition) -> None:
    _CATALOG[stack.catalog_id] = stack


def get_stack(catalog_id: str) -> Optional[OfficialStackDefinition]:
    return _CATALOG.get(catalog_id)


def list_stacks() -> List[OfficialStackDefinition]:
    return list(_CATALOG.values())


def match_repository(url: str) -> Optional[Tuple[OfficialStackDefinition, str]]:
    """Matches a Git repository or image reference against the official catalog."""
    cleaned = (url or "").strip().lower().rstrip("/")
    if not cleaned:
        return None
    # Normalize github / git URLs
    clean_repo = re.sub(r"\.git$", "", cleaned)
    clean_repo = re.sub(r"^git@([^:]+):", r"https://\1/", clean_repo)
    clean_repo = re.sub(r"^ssh://git@([^/]+)/", r"https://\1/", clean_repo)
    for stack in _CATALOG.values():
        for official_repo in stack.official_repositories:
            norm_official = re.sub(r"\.git$", "", official_repo.lower().rstrip("/"))
            norm_official = re.sub(r"^git@([^:]+):", r"https://\1/", norm_official)
            if clean_repo == norm_official or clean_repo.startswith(norm_official + "/"):
                return stack, stack.default_version
    return None


# -----------------------------------------------------------------------------
# First Official Vendor Stack: Plausible Community Edition
# -----------------------------------------------------------------------------
_PLAUSIBLE_LOGS_XML = "<clickhouse><logger><level>warning</level><console>1</console></logger></clickhouse>"
_PLAUSIBLE_IPV4_XML = "<clickhouse><listen_host>0.0.0.0</listen_host></clickhouse>"
_PLAUSIBLE_LOW_RES_XML = "<clickhouse><max_server_memory_usage_to_ram_ratio>0.5</max_server_memory_usage_to_ram_ratio><max_threads>2</max_threads></clickhouse>"
_PLAUSIBLE_USER_OVERRIDES_XML = "<clickhouse><profiles><default><max_memory_usage>500000000</max_memory_usage><max_threads>2</max_threads></default></profiles></clickhouse>"

_PLAUSIBLE_SERVICES = {
    "plausible_db": ServiceDefinition(
        name="plausible_db",
        image_reference="postgres:16-alpine",
        pinned_tag="16-alpine",
        internal_ports=[5432],
        volumes=[VolumeDefinition(name_suffix="db-data", container_mount_path="/var/lib/postgresql/data")],
        health_check=HealthCheckDefinition(
            probe_type="command",
            command=["pg_isready", "-U", "postgres"],
            interval_seconds=4,
            timeout_seconds=5,
            retries=15,
            start_period_seconds=15,
        ),
        memory_limit_mb=256,
        environment_defaults={"POSTGRES_USER": "postgres", "POSTGRES_DB": "plausible_db"},
    ),
    "plausible_events_db": ServiceDefinition(
        name="plausible_events_db",
        image_reference="clickhouse/clickhouse-server:24.12-alpine",
        pinned_tag="24.12-alpine",
        internal_ports=[8123, 9000],
        volumes=[
            VolumeDefinition(name_suffix="event-data", container_mount_path="/var/lib/clickhouse"),
            VolumeDefinition(name_suffix="event-logs", container_mount_path="/var/log/clickhouse-server"),
        ],
        config_files=[
            ConfigFileDefinition(filename="logs.xml", container_target_path="/etc/clickhouse-server/config.d/logs.xml", content=_PLAUSIBLE_LOGS_XML),
            ConfigFileDefinition(filename="ipv4-only.xml", container_target_path="/etc/clickhouse-server/config.d/ipv4-only.xml", content=_PLAUSIBLE_IPV4_XML),
            ConfigFileDefinition(filename="low-resources.xml", container_target_path="/etc/clickhouse-server/config.d/low-resources.xml", content=_PLAUSIBLE_LOW_RES_XML),
            ConfigFileDefinition(filename="default-profile-low-resources-overrides.xml", container_target_path="/etc/clickhouse-server/users.d/default-profile-low-resources-overrides.xml", content=_PLAUSIBLE_USER_OVERRIDES_XML),
        ],
        health_check=HealthCheckDefinition(
            probe_type="command",
            command=["wget", "-qO-", "http://127.0.0.1:8123/ping"],
            interval_seconds=4,
            timeout_seconds=5,
            retries=15,
            start_period_seconds=20,
        ),
        memory_limit_mb=1024,
    ),
    "plausible": ServiceDefinition(
        name="plausible",
        image_reference="ghcr.io/plausible/community-edition:v3.2.1",
        pinned_tag="v3.2.1",
        internal_ports=[8000],
        depends_on=["plausible_db", "plausible_events_db"],
        health_check=HealthCheckDefinition(
            probe_type="http",
            http_path="/api/health",
            http_port=8000,
            interval_seconds=5,
            timeout_seconds=5,
            retries=20,
            start_period_seconds=25,
        ),
        memory_limit_mb=512,
        is_web_entrypoint=True,
    ),
}

PLAUSIBLE_CE = OfficialStackDefinition(
    catalog_id="plausible_ce",
    display_name="Plausible Analytics CE",
    vendor_name="Plausible Analytics",
    description="Privacy-focused, lightweight web analytics. Official 3-service stack with PostgreSQL and ClickHouse.",
    official_repositories=[
        "https://github.com/plausible/community-edition",
        "https://github.com/plausible/analytics",
        "https://github.com/plausible/hosting",
        "github.com/plausible/community-edition",
        "github.com/plausible/analytics",
        "github.com/plausible/hosting",
    ],
    allowed_versions=["v3.2.1"],
    default_version="v3.2.1",
    services=_PLAUSIBLE_SERVICES,
    startup_order=["plausible_db", "plausible_events_db", "plausible"],
    web_service_name="plausible",
    web_internal_port=8000,
    web_health_path="/api/health",
    startup_timeout_seconds=60,
    recommended_ram_mb=2048,
    minimum_ram_mb=1536,
    allowed_nonsecret_settings=[
        "BASE_URL", "TIMEZONE", "DISABLE_REGISTRATION",
        "MAILER_EMAIL", "SMTP_HOST_ADDR", "SMTP_HOST_PORT", "SMTP_USER_NAME", "SMTP_USER_PWD", "MAILER_NAME",
    ],
    default_environment={"DISABLE_REGISTRATION": "invite_only"},
    url_templates={
        "DATABASE_URL": "postgresql://postgres:{POSTGRES_PASSWORD}@{plausible_db}:5432/plausible_db",
        "CLICKHOUSE_DATABASE_URL": "http://{plausible_events_db}:8123/plausible_events_db",
    },
    required_secrets=[
        SecretRequirement(key="SECRET_KEY_BASE", purpose="Plausible cryptographic cookie and session signing secret", generator="urlsafe64"),
        SecretRequirement(key="POSTGRES_PASSWORD", purpose="PostgreSQL internal password for Plausible database", generator="hex32"),
    ],
    post_install_message="Open Plausible to create first account",
    docs_url="https://github.com/plausible/community-edition",
)

register_stack(PLAUSIBLE_CE)
