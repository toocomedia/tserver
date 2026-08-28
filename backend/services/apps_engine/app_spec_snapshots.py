"""Preparation lifecycle for versioned AppSpec snapshots."""
from __future__ import annotations

from models.container_app_snapshot import ContainerAppSnapshot
from services.apps_engine import snapshot_envelope
from services.apps_engine.app_spec import AppSpec


def runtime_kind(snapshot: ContainerAppSnapshot) -> str:
    """Return the explicit dispatch key without guessing from app fields."""
    return snapshot_envelope.runtime_kind(snapshot.config_json)


def app_spec_for(snapshot: ContainerAppSnapshot) -> AppSpec:
    return snapshot_envelope.app_spec(snapshot.config_json)


def bind_app_spec(snapshot: ContainerAppSnapshot, spec: AppSpec) -> None:
    """Replace only a candidate v2 spec while pinning images before sealing."""
    if snapshot.state not in {"pending", "failed"}:
        raise RuntimeError("Only a candidate AppSpec snapshot can be prepared.")
    snapshot.config_json = snapshot_envelope.replace_app_spec(snapshot.config_json, spec)
    _refresh_fingerprint(snapshot)


def seal_prepared(snapshot: ContainerAppSnapshot) -> None:
    """Seal an immutable candidate after digests and deferred secrets are bound."""
    if runtime_kind(snapshot) != snapshot_envelope.COMPOSE_RUNTIME_KIND:
        raise RuntimeError("Only a Compose AppSpec snapshot uses prepared state.")
    if any(not service.pinned_digest for service in app_spec_for(snapshot).services.values()):
        raise RuntimeError("Every AppSpec service image must be pinned before deployment.")
    _refresh_fingerprint(snapshot)
    snapshot.state = "prepared"


def _refresh_fingerprint(snapshot: ContainerAppSnapshot) -> None:
    # Local import avoids making the legacy snapshot module own AppSpec lifecycle.
    from services.apps_engine.snapshots import refresh_fingerprint

    refresh_fingerprint(snapshot)
