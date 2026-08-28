"""Legacy rollback helper for persisted App Engine stacks."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from models.domain import Domain
from services import container_app_control_service, container_app_service as apps
from services.apps_engine import snapshot_lifecycle, snapshots
from services.official_stacks.catalog import get_stack
from services.official_stacks import stack_runtime_service


async def rollback_stack(
    db: AsyncSession,
    app: ContainerApp,
    domain: Domain,
    target_snapshot: ContainerAppSnapshot,
) -> bool:
    catalog_id = getattr(app, "stack_catalog_id", None)
    if not catalog_id:
        raise RuntimeError(f"App #{app.id} is missing a stack catalog identifier.")
    stack = get_stack(catalog_id)
    if stack is None:
        raise RuntimeError(f"Unknown official stack catalog '{catalog_id}'.")

    runtime = snapshots.runtime_app(app, target_snapshot)
    await snapshots.materialize_environment(db, app, target_snapshot)

    env_path = apps.env_path(app.id)

    # 1. Ensure network and volumes
    stack_runtime_service.ensure_stack_network(app.id)
    stack_runtime_service.ensure_stack_volumes(app.id, stack)
    stack_runtime_service.materialize_stack_configs(app.id, stack)

    # 2. Restart services in startup order
    for svc_name in stack.startup_order:
        svc = stack.services[svc_name]
        is_web = svc.is_web_entrypoint
        host_port = app.host_port if is_web else None
        stack_runtime_service.start_service_container(
            app.id, stack, svc_name, env_path, host_port=host_port,
        )
        await stack_runtime_service.wait_service_health(app.id, stack, svc_name, host_port=host_port)

    # 3. Publish Nginx route
    await container_app_control_service.publish(db, runtime, domain)
    await snapshot_lifecycle.promote_snapshot(db, app, target_snapshot, runtime)
    return True
