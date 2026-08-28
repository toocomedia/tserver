"""Resolve App Engine database targets from active provider-owned records."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app_database import ContainerAppDatabase
from services import container_app_service

_PORTS = {"postgresql": 5432, "mariadb": 3306, "redis": 6379, "mongodb": 27017}
_HOST_NATIVE = {"panel_postgres", "panel_mariadb"}


@dataclass(frozen=True)
class DatabaseNetworkTarget:
    """Stable connection target; never contains a temporary bridge IP."""

    host: str
    port: int
    network_name: str | None = None
    network_alias: str | None = None
    add_host_gateway: bool = False


def internal_service_target(service_name: str, port: int) -> DatabaseNetworkTarget:
    """Resolve a database declared inside the same Compose AppSpec."""
    if not service_name or not 1 <= int(port) <= 65535:
        raise ValueError("Internal database service target is invalid.")
    return DatabaseNetworkTarget(host=service_name, port=int(port), network_alias=service_name)


async def resolve_attachment(
    db: AsyncSession, app_id: int, attachment_id: int,
) -> DatabaseNetworkTarget:
    """Query the persisted active provider binding instead of guessing aliases."""
    item = await db.get(ContainerAppDatabase, attachment_id)
    if item is None or item.app_id != app_id:
        raise ValueError("Database attachment was not found for this app.")
    if item.status != "ready":
        raise ValueError("Database attachment is not ready.")
    return target_from_record(item)


def target_from_record(item: ContainerAppDatabase) -> DatabaseNetworkTarget:
    port = _PORTS.get(item.kind)
    if port is None:
        raise ValueError("Database attachment kind is unsupported.")
    if item.provider == "docker":
        alias = str(item.network_alias or "").strip()
        if not alias or alias.startswith("172."):
            raise ValueError("Private database provider has no stable network alias.")
        return DatabaseNetworkTarget(
            host=alias,
            port=port,
            network_name=container_app_service.network_name(item.app_id),
            network_alias=alias,
        )
    if item.provider in _HOST_NATIVE:
        return DatabaseNetworkTarget(
            host="host.docker.internal",
            port=port,
            add_host_gateway=True,
        )
    if item.provider in {"external", "supabase"}:
        return _remote_target(item, port)
    raise ValueError("Database attachment provider is unsupported.")


def _remote_target(item: ContainerAppDatabase, default_port: int) -> DatabaseNetworkTarget:
    # Remote credentials stay provider-owned; only a non-secret hostname is accepted here.
    try:
        from services import container_app_database_service
        value = container_app_database_service.connection_url(item)
    except Exception as exc:
        raise ValueError("Remote database provider target is unavailable.") from exc
    parsed = urlsplit(value)
    if not parsed.hostname:
        raise ValueError("Remote database provider returned an invalid target.")
    return DatabaseNetworkTarget(host=parsed.hostname, port=parsed.port or default_port)

