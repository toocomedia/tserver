"""Modern AppSpec Compose preparation over the proven panel-owned renderer."""
from __future__ import annotations

from models.container_app_snapshot import ContainerAppSnapshot
from services.apps_engine import app_spec_snapshots
from services.apps_engine.app_spec import AppSpec
from services.official_stacks import compose_runtime as renderer
from services.official_stacks.manifest_validator import compute_stack_manifest_hash


def stack_for_snapshot(snapshot: ContainerAppSnapshot) -> AppSpec:
    return app_spec_snapshots.app_spec_for(snapshot)


def pin_snapshot_images(snapshot: ContainerAppSnapshot) -> AppSpec:
    """Pull and persist immutable image digests before candidate sealing."""
    current = stack_for_snapshot(snapshot)
    if all(service.pinned_digest for service in current.services.values()):
        return current
    pinned = renderer.resolved_images(current)
    app_spec_snapshots.bind_app_spec(snapshot, pinned)
    snapshot.image_digest = compute_stack_manifest_hash(pinned, pinned.default_version)
    return pinned


project_name = renderer.project_name
compose_path = renderer.compose_path
environment_dir = renderer.environment_dir
service_environments = renderer.service_environments
render_compose = renderer.render_compose
write_project = renderer.write_project
validate_project = renderer.validate_project
up = renderer.up
start = renderer.start
stop = renderer.stop
down = renderer.down
