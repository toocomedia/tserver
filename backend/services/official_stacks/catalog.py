"""Official Vendor Stacks Catalog and registry."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from services.official_stacks.schema import (
    ConfigFileDefinition, HealthCheckDefinition, OfficialStackDefinition,
    SecretRequirement, ServiceDefinition, VolumeDefinition,
)

# Registry of authoritative, server-verified vendor stack definitions.
_CATALOG: Dict[str, OfficialStackDefinition] = {}


def _plausible_stack() -> OfficialStackDefinition:
    """Official Plausible CE v3.2.1 topology, adapted for panel-owned Nginx."""
    return OfficialStackDefinition(
        catalog_id="plausible_ce", display_name="Plausible Analytics CE", vendor_name="Plausible",
        description="Plausible CE with PostgreSQL and ClickHouse.",
        official_repositories=["https://github.com/plausible/community-edition"],
        allowed_versions=["v3.2.1"], default_version="v3.2.1",
        services={
            "plausible_db": ServiceDefinition(
                name="plausible_db", image_reference="postgres:16-alpine", pinned_tag="16-alpine",
                internal_ports=[5432], memory_limit_mb=256, cpu_limit="0.5",
                volumes=[VolumeDefinition("plausible-db-data", "/var/lib/postgresql/data")],
                environment_defaults={"POSTGRES_USER": "postgres", "POSTGRES_DB": "plausible_db"},
                health_check=HealthCheckDefinition("command", command=["pg_isready", "-U", "postgres"], start_period_seconds=60),
            ),
            "plausible_events_db": ServiceDefinition(
                name="plausible_events_db", image_reference="clickhouse/clickhouse-server:24.12-alpine", pinned_tag="24.12-alpine",
                internal_ports=[8123, 9000], memory_limit_mb=768, cpu_limit="1.0",
                volumes=[VolumeDefinition("plausible-events-data", "/var/lib/clickhouse"),
                         VolumeDefinition("plausible-events-logs", "/var/log/clickhouse-server")],
                environment_defaults={"CLICKHOUSE_SKIP_USER_SETUP": "1"},
                health_check=HealthCheckDefinition("command", command=["wget", "--no-verbose", "--tries=1", "-O", "-", "http://127.0.0.1:8123/ping"], start_period_seconds=60),
            ),
            "plausible": ServiceDefinition(
                name="plausible", image_reference="ghcr.io/plausible/community-edition:v3.2.1", pinned_tag="v3.2.1",
                internal_ports=[8000], memory_limit_mb=768, cpu_limit="1.0", is_web_entrypoint=True,
                depends_on=["plausible_db", "plausible_events_db"],
                volumes=[VolumeDefinition("plausible-data", "/var/lib/plausible")],
                environment_defaults={"TMPDIR": "/var/lib/plausible/tmp", "HTTP_PORT": "8000"},
                command=["sh", "-c", "/entrypoint.sh db createdb && /entrypoint.sh db migrate && /entrypoint.sh run"],
            ),
        },
        startup_order=["plausible_db", "plausible_events_db", "plausible"],
        web_service_name="plausible", web_internal_port=8000, web_health_path="",
        startup_timeout_seconds=180, recommended_ram_mb=2048, minimum_ram_mb=1536,
        allowed_nonsecret_settings=["DISABLE_REGISTRATION", "ENABLE_EMAIL_VERIFICATION"],
        url_templates={
            "DATABASE_URL": "postgres://postgres:{POSTGRES_PASSWORD}@{plausible_db}:5432/plausible_db",
            "CLICKHOUSE_DATABASE_URL": "http://{plausible_events_db}:8123/plausible_events_db",
        },
        required_secrets=[
            SecretRequirement("POSTGRES_PASSWORD", "PostgreSQL password", "password", "plausible_db", "POSTGRES_PASSWORD"),
            SecretRequirement("SECRET_KEY_BASE", "Plausible session signing key", "base64_48", "plausible", "SECRET_KEY_BASE"),
        ],
        post_install_message="Open the domain to create the first administrator account.",
        docs_url="https://github.com/plausible/community-edition/tree/v3.2.1",
    )


def _register_builtin_stacks() -> None:
    _CATALOG.setdefault("plausible_ce", _plausible_stack())


_register_builtin_stacks()


def register_stack(stack: OfficialStackDefinition) -> None:
    """Registers an official stack definition in the catalog."""
    _CATALOG[stack.catalog_id] = stack


def unregister_stack(catalog_id: str) -> None:
    """Unregisters a stack definition from the catalog."""
    _CATALOG.pop(catalog_id, None)


def get_stack(catalog_id: str) -> Optional[OfficialStackDefinition]:
    """Retrieves an official stack definition by catalog identifier."""
    return _CATALOG.get(catalog_id)


def list_stacks() -> List[OfficialStackDefinition]:
    """Lists all registered official stack definitions."""
    return list(_CATALOG.values())


def clear_catalog() -> None:
    """Clears test/dynamic entries while retaining panel-owned built-in templates."""
    _CATALOG.clear()
    _register_builtin_stacks()


def match_repository(url: str) -> Optional[Tuple[OfficialStackDefinition, str]]:
    """Matches a Git repository or image reference against registered official catalog stacks."""
    cleaned = (url or "").strip().lower().rstrip("/")
    if not cleaned:
        return None
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
