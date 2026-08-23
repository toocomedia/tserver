"""Resource Guard status and Settings APIs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import dependency_manager
from models.container_app import ContainerApp
from models.domain import Domain
from models.hosted_app import HostedApp
from models.guard_operation import GuardOperation
from plugins import plugin_manager
from services.resource_guard_service import PRIORITIES, resource_guard_service
from services.resource_guard_operation_service import resource_guard_operation_service
from services.resource_guard_profiles import PROFILES
from services import container_app_image_inspect_service as image_inspect_svc
from services import disk_cleanup_service

router = APIRouter(tags=["resource-guard"])


class ResourceGuardSettingsIn(BaseModel):
    mode: str
    memory_limit_percent: int = Field(ge=75, le=95)
    protected_reserve_mb: int | None = Field(default=None, ge=100, le=2048)


class PriorityOverrideIn(BaseModel):
    component_type: str
    component_id: str
    priority: str


@router.get("/api/resource-guard/status")
async def status(db: AsyncSession = Depends(get_db)):
    return await resource_guard_service.status(db)


@router.get("/api/resource-guard/profiles")
async def list_profiles():
    """Return all known resource profiles (for UI display)."""
    return {
        name: {**prof, "name": name}
        for name, prof in PROFILES.items()
    }


@router.get("/api/resource-guard/preflight")
async def preflight(profile: str, db: AsyncSession = Depends(get_db)):
    """Run the admission test for *profile* and return the result."""
    if profile not in PROFILES:
        raise HTTPException(400, f"Unknown profile '{profile}'. Valid: {', '.join(PROFILES)}")
    return await resource_guard_service.preflight(db, profile)


@router.get("/api/resource-guard/operations")
async def list_operations(db: AsyncSession = Depends(get_db)):
    operations = await resource_guard_operation_service.list(db)
    return {"operations": [resource_guard_operation_service.data(item) for item in operations]}


@router.get("/api/resource-guard/operations/{operation_id}")
async def operation_detail(operation_id: int, db: AsyncSession = Depends(get_db)):
    operation = await db.get(GuardOperation, operation_id)
    if operation is None:
        raise HTTPException(404, "Operation not found.")
    return resource_guard_operation_service.data(operation)


@router.post("/api/resource-guard/operations/{operation_id}/cancel")
async def cancel_operation(operation_id: int, db: AsyncSession = Depends(get_db)):
    operation = await resource_guard_operation_service.cancel(db, operation_id)
    if operation is None:
        raise HTTPException(404, "Operation not found.")
    return resource_guard_operation_service.data(operation)


@router.get("/api/settings/resource-guard")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return {"status": await resource_guard_service.status(db), "resources": await _resources(db)}


@router.post("/api/settings/resource-guard")
async def save_settings(payload: ResourceGuardSettingsIn, db: AsyncSession = Depends(get_db)):
    try:
        return await resource_guard_service.save_settings(
            db, payload.mode, payload.memory_limit_percent, payload.protected_reserve_mb
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/settings/resource-guard/priorities")
async def save_priority(payload: PriorityOverrideIn, db: AsyncSession = Depends(get_db)):
    try:
        await resource_guard_service.save_priority(db, payload.component_type, payload.component_id, payload.priority)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


async def _resources(db: AsyncSession) -> list[dict]:
    rows: list[dict] = []
    for plugin in plugin_manager.list_plugins(check_dependencies=False):
        rows.append(await _row(db, "plugin", plugin["id"], plugin["name"]))
    for dependency in dependency_manager.get_all_statuses(cached=True):
        rows.append(await _row(db, "dependency", dependency["id"], dependency["name"]))
    hosted = (await db.execute(select(HostedApp, Domain.name).join(Domain))).all()
    for app, name in hosted:
        rows.append(await _row(db, "hosted_app", str(app.id), name))
    containers = (await db.execute(select(ContainerApp, Domain.name).join(Domain))).all()
    for app, name in containers:
        rows.append(await _row(db, "container_app", str(app.id), name))
    return rows


async def _row(db: AsyncSession, kind: str, item_id: str, label: str) -> dict:
    return {
        "type": kind,
        "id": str(item_id),
        "label": label,
        "priority": await resource_guard_service.priority(db, kind, str(item_id)),
        "priorities": PRIORITIES,
    }


# ── Slice 3: Safe Install Mode ─────────────────────────────────────────────

class SafeInstallRequestIn(BaseModel):
    operation_id: int


class SafeInstallApproveIn(BaseModel):
    approved_ids: list[str]


@router.get("/api/resource-guard/host-capabilities")
async def host_capabilities():
    """Return host cgroup/Docker/Buildx capability report."""
    return resource_guard_service.host_capabilities()


@router.post("/api/resource-guard/safe-install/request")
async def safe_install_request(
    payload: SafeInstallRequestIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Build the Safe Install candidate list for an operation.
    Creates a SafeInstallRun record and returns candidates.
    No service is stopped yet.
    """
    result = await resource_guard_service.request_safe_install(db, payload.operation_id)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "Could not build Safe Install candidates."))
    return result


@router.post("/api/resource-guard/safe-install/{run_id}/approve")
async def safe_install_approve(
    run_id: int,
    payload: SafeInstallApproveIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit approved candidate IDs. Stops them one-by-one and rechecks
    capacity after each. Returns services_stopped and after_ram_mb.
    """
    result = await resource_guard_service.approve_safe_install(db, run_id, payload.approved_ids)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "Safe Install approval failed."))
    return result


@router.post("/api/resource-guard/safe-install/{run_id}/complete")
async def safe_install_complete(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Post-install restore decision. Call after the new app passes health checks.
    Restores stopped services if safe; otherwise pauses the new app and restores originals.
    """
    result = await resource_guard_service.complete_safe_install(db, run_id)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "Safe Install complete failed."))
    return result


@router.post("/api/resource-guard/safe-install/{run_id}/restore")
async def safe_install_restore(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Manually restore stopped services for a Safe Install run."""
    result = await resource_guard_service.restore_safe_install(db, run_id)
    if not result.get("ok"):
        raise HTTPException(409, {"reason": "Restore failed.", "errors": result.get("errors", [])})
    return result


@router.get("/api/resource-guard/safe-install/{run_id}")
async def safe_install_status(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return current state of a Safe Install run."""
    from models.safe_install_run import SafeInstallRun
    run = await db.get(SafeInstallRun, run_id)
    if run is None:
        raise HTTPException(404, "Safe Install run not found.")
    return {
        "id": run.id,
        "operation_id": run.operation_id,
        "outcome": run.outcome,
        "restore_state": run.restore_state,
        "before_ram_mb": run.before_ram_mb,
        "after_ram_mb": run.after_ram_mb,
        "services_stopped": run.services_stopped,
        "approved_ids": run.approved_ids,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


@router.post("/api/resource-guard/safe-install/{operation_id}/start-paused")
async def start_paused_install(
    operation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Run a fresh preflight before starting an app paused by Safe Install.
    Returns the preflight result so the caller can decide whether to proceed.
    """
    return await resource_guard_service.start_paused_install(db, operation_id)


# ── Slice 5: Source Intelligence & Disk Cleanup ────────────────────────────

class InspectImageIn(BaseModel):
    reference: str


class DiskCleanupIn(BaseModel):
    include_ids: list[str]


@router.post("/api/resource-guard/inspect-image")
async def inspect_image(payload: InspectImageIn):
    """Pull a registry image, extract metadata, then remove the pulled image."""
    try:
        image_inspect_svc.validate_image_reference(payload.reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return await image_inspect_svc.inspect_image(payload.reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/resource-guard/disk-inventory")
async def disk_inventory(db: AsyncSession = Depends(get_db)):
    """Return a dry-run inventory of disk space that can be freed."""
    active_digests, rollback_images, existing_ids = await _collect_image_digests(db)
    items = await disk_cleanup_service.inventory(active_digests, rollback_images, existing_ids)
    deletable = [i for i in items if not i["protected"]]
    protected = [i for i in items if i["protected"]]
    total_mb = round(sum(i["size_mb"] for i in deletable), 1)
    return {
        "deletable": deletable,
        "protected": protected,
        "total_recoverable_mb": total_mb,
    }


@router.post("/api/resource-guard/disk-cleanup")
async def disk_cleanup(payload: DiskCleanupIn, db: AsyncSession = Depends(get_db)):
    """Execute cleanup for the selected item IDs."""
    if not payload.include_ids:
        raise HTTPException(400, "include_ids must not be empty.")
    active_digests, rollback_images, existing_ids = await _collect_image_digests(db)
    result = await disk_cleanup_service.run_cleanup(
        payload.include_ids, active_digests, rollback_images, existing_ids
    )
    # Invalidate disk stats cache so /api/stats reflects freed space immediately
    try:
        from routers.system import _invalidate_stats_cache
        _invalidate_stats_cache()
    except Exception:
        pass
    return result


@router.post("/api/resource-guard/builder-prune")
async def builder_prune(db: AsyncSession = Depends(get_db)):
    """Prune Docker builder cache (BuildKit) — safe, does not remove active images."""
    # Use the same protection inventory logic: build cache is never protected
    active_digests, rollback_images, existing_ids = await _collect_image_digests(db)
    items = await disk_cleanup_service.inventory(active_digests, rollback_images, existing_ids)
    cache_items = [i for i in items if i["type"] == disk_cleanup_service.TYPE_BUILD_CACHE]
    if not cache_items:
        return {"deleted": [], "freed_mb": 0.0, "errors": [], "skipped": ["No builder cache found"]}
    result = await disk_cleanup_service.run_cleanup(
        [cache_items[0]["item_id"]], active_digests, rollback_images, existing_ids
    )
    try:
        from routers.system import _invalidate_stats_cache
        _invalidate_stats_cache()
    except Exception:
        pass
    return result


async def _collect_image_digests(db: AsyncSession) -> tuple[set[str], set[str], set[str]]:
    """Return (active_digests, rollback_images, existing_app_ids) sets from all container apps.
    existing_app_ids is used for general-safe protection: any srv-panel/railpack-app image
    belonging to an existing app is kept, even if not the active digest (stopped plugins)."""
    apps = (await db.scalars(select(ContainerApp))).all()
    active: set[str] = set()
    rollback: set[str] = set()
    existing_ids: set[str] = set()
    for app in apps:
        existing_ids.add(str(app.id))
        if app.image_digest:
            active.add(app.image_digest)
        if app.previous_image:
            rollback.add(app.previous_image)
    return active, rollback, existing_ids
