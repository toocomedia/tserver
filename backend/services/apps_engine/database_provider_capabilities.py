"""Canonical database-provider choices and live managed-service status for App Engine setup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_MANAGED_BINDINGS = (
    {"kind": "postgresql", "provider": "panel_postgres", "dependency_id": "postgresql", "label": "Panel PostgreSQL"},
    {"kind": "mariadb", "provider": "panel_mariadb", "dependency_id": "mariadb", "label": "Panel MariaDB"},
)
_OPTIONAL_PROVIDERS = {
    "postgresql": {"supabase", "external"},
    "mariadb": {"external"},
    "redis": {"external"},
    "mongodb": {"external"},
}
_AMBIGUOUS_PROVIDER_VALUES = {
    "panel", "panel_managed", "managed", "postgres", "postgresql", "mysql", "mariadb",
}


@dataclass(frozen=True)
class ProviderChoiceRequired(ValueError):
    kind: str
    provider: str
    state: str
    dependency_id: str = ""

    def __str__(self) -> str:
        return f"Choose an available provider for {self.kind}; '{self.provider or 'unspecified'}' is {self.state}."


def provider_capabilities(*, force: bool = False) -> list[dict[str, Any]]:
    """Return provider choices from the dependency registry without credential data."""
    try:
        from dependencies import dependency_manager
        docker_healthy = dependency_manager.is_healthy("docker", cached=not force)
    except Exception:
        dependency_manager = None
        docker_healthy = False

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for binding in _MANAGED_BINDINGS:
        try:
            status = dependency_manager.get_status(binding["dependency_id"], force=force) if dependency_manager else None
        except Exception:
            status = None
        state = _managed_state(status)
        by_kind.setdefault(binding["kind"], []).append({
            "service_kind": binding["kind"],
            "provider_id": binding["provider"],
            "id": binding["provider"],  # compatibility with current setup callers
            "label": binding["label"],
            "type": "managed",
            "dependency_id": binding["dependency_id"],
            "managed_dependency_state": state,
            "state": state,  # compatibility with the existing setup contract
            "can_activate": state == "stopped" and bool((status or {}).get("can_toggle")),
            "activation_url": f"/dependencies/{binding['dependency_id']}",
        })

    result: list[dict[str, Any]] = []
    kinds = {"postgresql", "mariadb", "redis", "mongodb"} | set(by_kind)
    for kind in sorted(kinds):
        providers = [{
            "service_kind": kind,
            "provider_id": "docker",
            "id": "docker",
            "label": "Private container service",
            "type": "container",
            "container_available": docker_healthy,
            "state": "active" if docker_healthy else "unavailable",
            "can_activate": False,
        }]
        providers.extend(by_kind.get(kind, []))
        result.append({"service_kind": kind, "kind": kind, "container_available": docker_healthy, "providers": providers})
    return result


def canonical_provider(kind: str, raw_provider: str) -> str:
    """Accept only explicit provider IDs; a database kind is never a provider alias."""
    provider = (raw_provider or "docker").strip().lower()
    if provider in _AMBIGUOUS_PROVIDER_VALUES:
        raise ProviderChoiceRequired(kind=kind, provider=provider, state="ambiguous")
    allowed = {"docker"} | _OPTIONAL_PROVIDERS.get(kind, set())
    allowed.update(item["provider"] for item in _MANAGED_BINDINGS if item["kind"] == kind)
    if provider not in allowed:
        raise ProviderChoiceRequired(kind=kind, provider=provider, state="unsupported")
    return provider


def require_available(kind: str, provider: str) -> None:
    """Block inactive panel-managed services before a reviewed plan is saved."""
    if provider not in {item["provider"] for item in _MANAGED_BINDINGS}:
        return
    for item in provider_capabilities():
        if item["kind"] != kind:
            continue
        match = next((choice for choice in item["providers"] if choice["id"] == provider), None)
        if match and match["state"] == "active":
            return
        raise ProviderChoiceRequired(
            kind=kind,
            provider=provider,
            state=str((match or {}).get("state") or "unavailable"),
            dependency_id=str((match or {}).get("dependency_id") or ""),
        )


def _managed_state(status: dict[str, Any] | None) -> str:
    if not status:
        return "unavailable"
    if status.get("healthy") and status.get("operation") == "idle":
        return "active"
    if not status.get("installed"):
        return "not_installed"
    if status.get("operation") not in {None, "idle"}:
        return "unavailable"
    return "stopped"
