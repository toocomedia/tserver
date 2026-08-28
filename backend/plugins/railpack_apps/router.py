"""Railpack Apps plugin pages and deployment endpoints."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.container_app_snapshot import ContainerAppSnapshot
from models.ai_helper import AiActionPlan
from models.container_app_database import ContainerAppDatabase
from models.container_app_backup import ContainerAppBackup
from models.domain import Domain
from models.ssl_cert import SslCert
from services import container_app_cleanup_service, container_app_database_service
from services import container_app_database_lifecycle_service
from services import container_app_deployment_service
from services import container_app_removal_service, ssl_service
from services.apps_engine import deployment_drafts, secret_vault, snapshots
from plugins.railpack_apps import command_service
from plugins.railpack_apps.router_command import router as command_router
from plugins.railpack_apps.router_create import router as create_router
from plugins.railpack_apps.router_recovery import router as recovery_router
from plugins.railpack_apps.router_resources import router as resource_router
from plugins.railpack_apps.router_template import router as template_router
from templating import templates

router = APIRouter(prefix="/plugins/railpack_apps", tags=["railpack-apps"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    apps = (await db.scalars(select(ContainerApp).order_by(ContainerApp.id.desc()))).all()
    domain_ids = [app.domain_id for app in apps]
    domains = (await db.scalars(select(Domain).where(Domain.id.in_(domain_ids)))).all() if domain_ids else []
    return templates.TemplateResponse("railpack_apps.html", {
        "request": request, "active_page": "railpack_apps", "apps": apps,
        "domains_by_id": {domain.id: domain for domain in domains},
    })


class BulkActionRequest(BaseModel):
    action: str
    ids: list[int]


@router.post("/bulk")
async def bulk_action(req: BulkActionRequest, db: AsyncSession = Depends(get_db)):
    if req.action not in ["start", "stop", "restart", "delete"]:
        raise HTTPException(400, "Invalid bulk action.")
    apps = (await db.scalars(select(ContainerApp).where(ContainerApp.id.in_(req.ids)))).all()
    if not apps:
        return JSONResponse({"status": "ok"})
    
    from services import container_app_control_service, container_app_cleanup_service, container_app_removal_service
    from services.resource_guard_service import resource_guard_service

    for app in apps:
        domain = await db.get(Domain, app.domain_id)
        if not domain:
            continue
        if req.action == "delete":
            app.status = "deleting"
            await db.commit()
            try:
                await container_app_cleanup_service.uninstall(db, app, domain, remove_network=False)
                attachments = await container_app_database_service.attachments_for(db, app.id)
                managed_ids = [item.id for item in attachments if item.provider in {"docker", "panel_postgres", "panel_mariadb"}]
                await container_app_removal_service.remove_selected_data(
                    db, app, attachments, database_ids=managed_ids,
                    delete_app_volume=bool(app.data_volume or getattr(app, "storage_mounts", None) or getattr(app, "deploy_type", None) in {"official_stack", "app_spec"}),
                    delete_wordpress_files=bool(app.wordpress_content_volume),
                    delete_backups=True
                )
                await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
                await db.execute(delete(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id))
                await db.delete(app)
            except Exception:
                app.status = "delete_failed"
                await db.commit()
        else:
            if app.status in {"deleting", "delete_failed", "data_preserved"}:
                continue
            if req.action != "stop":
                try:
                    await resource_guard_service.allow_start(db)
                except RuntimeError:
                    continue
            try:
                await container_app_control_service.control(db, app, domain, req.action)
            except Exception:
                if req.action != "stop":
                    app.status = "failed"
                    await db.commit()
    
    await db.commit()
    return JSONResponse({"status": "ok"})


router.include_router(create_router)
router.include_router(recovery_router)
router.include_router(command_router)
router.include_router(template_router)


@router.get("/{app_id}", response_class=HTMLResponse)
async def detail(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    domain = await db.get(Domain, app.domain_id)
    ssl_active = await ssl_service.is_domain_ssl_active(db, domain)
    deployments = (await db.scalars(select(ContainerAppDeployment).where(
        ContainerAppDeployment.app_id == app.id,
    ).order_by(ContainerAppDeployment.id.desc()).limit(8))).all()
    requested_deployment = _optional_deployment_id(request.query_params.get("deployment"))
    deployment = next(
        (item for item in deployments if item.id == requested_deployment),
        deployments[0] if deployments else None,
    )
    databases = await container_app_database_service.attachments_for(db, app.id)
    backups = list((await db.scalars(select(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id).order_by(ContainerAppBackup.id.desc()).limit(12))).all())
    user_id = request.session.get("user_id")
    pending_plan = await _pending_draft(db, app.id, user_id)
    pending_snapshot = await db.get(ContainerAppSnapshot, app.pending_snapshot_id) if app.pending_snapshot_id else None
    active_snapshot = await db.get(ContainerAppSnapshot, app.active_snapshot_id) if app.active_snapshot_id else None
    rollback_snapshot = await db.scalar(select(ContainerAppSnapshot).where(
        ContainerAppSnapshot.app_id == app.id,
        ContainerAppSnapshot.state == "superseded",
    ).order_by(ContainerAppSnapshot.id.desc()))
    credentials = await snapshots.credentials_for(db, app.id)
    from plugins.railpack_apps.documentation_service import get_app_documentation
    app_docs = get_app_documentation(app, domain, active_snapshot)
    app_containers = command_service.get_authorized_containers(app)
    quick_commands = command_service.get_quick_commands(app, domain)
    return templates.TemplateResponse("railpack_apps_detail.html", {
        "request": request, "active_page": "railpack_apps", "app": app, "domain": domain, "ssl_active": ssl_active, "deployment": deployment, "deployments": deployments,
        "databases": databases, "database_statuses": {item.id: container_app_database_lifecycle_service.status(item) for item in databases}, "backups": backups,
        "pending_plan": pending_plan, "pending_snapshot": pending_snapshot, "active_snapshot": active_snapshot,
        "rollback_snapshot": rollback_snapshot, "credentials": credentials, "app_docs": app_docs,
        "app_containers": app_containers, "quick_commands": quick_commands,
    })


@router.get("/{app_id}/deployments/{deployment_id}")
async def deployment_status(app_id: int, deployment_id: int, db: AsyncSession = Depends(get_db)):
    deployment = await db.get(ContainerAppDeployment, deployment_id)
    if deployment is None or deployment.app_id != app_id:
        raise HTTPException(404, "Deployment not found.")
    from services import container_app_build_process_service as build_proc
    live_out = build_proc.get_live_output(deployment.id)
    full_output = (deployment.output + live_out)[-80_000:] if live_out else deployment.output
    return JSONResponse({
        "id": deployment.id, "status": deployment.status, "stage": deployment.stage,
        "action": deployment.action, "output": full_output, "error": deployment.error,
    })


@router.get("/{app_id}/diagnostics")
async def diagnostics(app_id: int, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    from services import container_app_diagnostics_service
    return JSONResponse(await container_app_diagnostics_service.collect(db, app, domain))


@router.post("/{app_id}/deploy")
async def deploy(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    try:
        deployment = await container_app_deployment_service.queue_deployment(
            db, app, action="deploy" if app.status == "pending" else "redeploy",
        )
        await db.commit()
    except HTTPException as exc:
        active = await container_app_deployment_service.active_deployment(db, app.id)
        if "application/json" in request.headers.get("accept", ""):
            if active:
                return JSONResponse({"deployment_id": active.id, "app_id": app.id})
            raise exc
        if active:
            return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={active.id}", status_code=303)
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?{urlencode({'error': str(exc.detail)})}", status_code=303)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deployment_id": deployment.id, "app_id": app.id})
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/deployment-changes/{plan_id}/apply")
async def apply_deployment_changes(app_id: int, plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if await container_app_deployment_service.active_deployment(db, app.id):
        raise HTTPException(409, "Deployment changes cannot be applied while a deployment is running.")
    try:
        snapshot_id, _statuses = await deployment_drafts.apply_plan(db, app, plan_id, request.session.get("user_id"))
        deployment = await container_app_deployment_service.queue_deployment(
            db, app, action="deploy", snapshot_id=snapshot_id,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?{urlencode({'error': str(exc)})}", status_code=303)
    except HTTPException:
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?snapshot={snapshot_id}", status_code=303)
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/snapshots/{snapshot_id}/deploy")
async def deploy_snapshot(app_id: int, snapshot_id: int, request: Request, action: str = Form("deploy"), db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if action not in {"deploy", "retry", "rebuild"}:
        raise HTTPException(400, "Unsupported snapshot action.")
    try:
        deployment = await container_app_deployment_service.queue_deployment(db, app, action=action, snapshot_id=snapshot_id)
        await db.commit()
    except HTTPException as exc:
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?{urlencode({'error': str(exc.detail)})}", status_code=303)
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/snapshots/{snapshot_id}/rollback")
async def rollback_snapshot(app_id: int, snapshot_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    snapshot = await db.get(ContainerAppSnapshot, snapshot_id)
    if snapshot is None or snapshot.app_id != app.id or snapshot.state != "superseded":
        raise HTTPException(400, "Only a prior active snapshot can be rolled back.")
    deployment = await container_app_deployment_service.queue_deployment(db, app, action="rollback", snapshot_id=snapshot.id)
    await db.commit()
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}?deployment={deployment.id}", status_code=303)


@router.post("/{app_id}/snapshots/{snapshot_id}/discard")
async def discard_snapshot(app_id: int, snapshot_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    if await container_app_deployment_service.active_deployment(db, app.id):
        raise HTTPException(409, "Deployment snapshot cannot be discarded while a deployment is running.")
    snapshot = await db.get(ContainerAppSnapshot, snapshot_id)
    if snapshot is None or snapshot.app_id != app.id or snapshot.state != "pending":
        raise HTTPException(400, "Only a pending deployment snapshot can be discarded.")
    snapshot.state = "discarded"
    if app.pending_snapshot_id == snapshot.id:
        app.pending_snapshot_id = None
    await db.commit()
    return RedirectResponse(f"/plugins/railpack_apps/{app.id}", status_code=303)


@router.post("/{app_id}/credentials/{credential_id}/reveal")
async def reveal_credential(app_id: int, credential_id: int, request: Request, action: str = "reveal", db: AsyncSession = Depends(get_db)):
    app = await _app(db, app_id)
    try:
        credential, password = await secret_vault.reveal_credential(
            db, app.id, credential_id, action=action, user_id=request.session.get("user_id"),
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return JSONResponse({"username": credential.username, "password": password}, headers={"Cache-Control": "no-store"})


@router.post("/{app_id}/deployments/{deployment_id}/cancel")
async def cancel_deployment_by_id(app_id: int, deployment_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    dep = await container_app_deployment_service.cancel_deployment(db, app_id, deployment_id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "cancelled", "deployment_id": deployment_id})
    return RedirectResponse(f"/plugins/railpack_apps/{app_id}?deployment={deployment_id}", status_code=303)


@router.post("/{app_id}/cancel-deployment")
async def cancel_active_deployment(app_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    dep = await container_app_deployment_service.cancel_deployment(db, app_id)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "cancelled", "deployment_id": dep.id if dep else None})
    return RedirectResponse(f"/plugins/railpack_apps/{app_id}", status_code=303)


@router.post("/{app_id}/settings")
async def update_settings(
    app_id: int,
    request: Request,
    git_ref: str | None = Form(None),
    git_ref_type: str | None = Form(None),
    root_directory: str | None = Form(None),
    dockerfile_path: str | None = Form(None),
    build_args: str | None = Form(None),
    build_secret_keys: str | None = Form(None),
    custom_start_command: str | None = Form(None),
    health_path: str | None = Form(None),
    startup_timeout_seconds: int | None = Form(None),
    storage_mounts: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    app = await _app(db, app_id)
    if app.deploy_type in {"official_stack", "app_spec"}:
        raise HTTPException(400, "Stack settings are fixed by the approved server manifest. Create a reviewed candidate to change them.")
    active = await container_app_deployment_service.active_deployment(db, app.id)
    if active:
        raise HTTPException(409, "Settings cannot be updated while a deployment is running or queued.")

    from dependencies.git import repository_service
    from services import container_app_service

    patch: dict[str, object] = {}
    if git_ref is not None and app.source_type == "git":
        ref_type = git_ref_type or app.git_ref_type or "branch"
        repository_service.validate_source(app.repository_url or "", git_ref.strip(), ref_type)
        patch.update({"git_ref": git_ref.strip(), "branch": git_ref.strip(), "git_ref_type": ref_type})
    if root_directory is not None:
        patch["root_directory"] = container_app_service.validate_root_directory(root_directory)
    if dockerfile_path is not None:
        patch["dockerfile_path"] = container_app_service.validate_dockerfile_path(dockerfile_path)
    if build_args is not None:
        patch["build_args"] = container_app_service.parse_build_args(build_args)
    if build_secret_keys is not None:
        patch["build_secret_keys"] = container_app_service.parse_build_secret_keys(build_secret_keys)
    if custom_start_command is not None:
        patch["custom_start_command"] = container_app_service.validate_custom_start_command(custom_start_command)
    if health_path is not None:
        patch["health_path"] = container_app_service.validate_health_path(health_path)
    if startup_timeout_seconds is not None:
        patch["startup_timeout_seconds"] = container_app_service.validate_startup_timeout(startup_timeout_seconds)
    if storage_mounts is not None:
        new_mounts_json = container_app_service.parse_storage_mounts(app.id, storage_mounts)
        patch["storage_mounts"] = new_mounts_json

    if app.pending_snapshot_id:
        previous = await db.get(ContainerAppSnapshot, app.pending_snapshot_id)
        if previous and previous.state == "pending":
            previous.state = "discarded"
    await snapshots.create_snapshot(db, app, config_patch=patch, created_by_user_id=request.session.get("user_id"))
    await db.commit()

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"status": "ok", "app_id": app.id})
    return RedirectResponse(
        f"/plugins/railpack_apps/{app.id}?{urlencode({'notice': 'Settings saved as a pending deployment snapshot. Deploy candidate to apply them.'})}",
        status_code=303,
    )


@router.post("/{app_id}/uninstall")
async def uninstall(
    app_id: int, confirmation: str = Form(""),
    keep_database_ids: list[int] = Form([]), keep_app_volume: bool = Form(False),
    keep_wordpress_files: bool = Form(False), keep_saved_backups: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    app = await _app(db, app_id)
    domain = await db.get(Domain, app.domain_id)
    if domain is None:
        raise HTTPException(409, "App domain is missing.")
    attachments = await container_app_database_service.attachments_for(db, app.id)
    managed_ids = {item.id for item in attachments if item.provider in {"docker", "panel_postgres", "panel_mariadb"}}
    if set(keep_database_ids) - managed_ids:
        raise HTTPException(400, "Only this app's local managed services can be kept here.")
    if confirmation != "DELETE ALL":
        raise HTTPException(400, "Type DELETE ALL to confirm this removal.")
    delete_database_ids = list(managed_ids - set(keep_database_ids))
    delete_app_volume = (bool(app.data_volume) or bool(app.storage_mounts) or getattr(app, "deploy_type", None) in {"official_stack", "app_spec"}) and not keep_app_volume
    delete_wordpress_files = bool(app.wordpress_content_volume) and not keep_wordpress_files
    delete_saved_backups = not keep_saved_backups
    app.status, app.last_error = "deleting", None
    try:
        await container_app_cleanup_service.uninstall(db, app, domain, remove_network=False)
    except HTTPException as exc:
        app.status, app.last_error = "delete_failed", str(exc.detail)[:1000]
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?error=Delete+failed", status_code=303)
    try:
        data_preserved = await container_app_removal_service.remove_selected_data(
            db, app, attachments, database_ids=delete_database_ids,
            delete_app_volume=delete_app_volume, delete_wordpress_files=delete_wordpress_files,
            delete_backups=delete_saved_backups,
        )
    except (HTTPException, ValueError) as exc:
        app.status, app.last_error = "delete_failed", str(exc.detail if isinstance(exc, HTTPException) else exc)[:1000]
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?error=Data+removal+failed", status_code=303)
    if data_preserved:
        await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
        app.status = "data_preserved"
        await db.commit()
        return RedirectResponse(f"/plugins/railpack_apps/{app.id}?notice=Application+removed;+managed+data+is+preserved", status_code=303)
    await db.execute(delete(ContainerAppDeployment).where(ContainerAppDeployment.app_id == app.id))
    await db.execute(delete(ContainerAppBackup).where(ContainerAppBackup.app_id == app.id))
    await db.delete(app)
    return RedirectResponse("/plugins/railpack_apps/", status_code=303)


async def _app(db: AsyncSession, app_id: int) -> ContainerApp:
    app = await db.get(ContainerApp, app_id)
    if app is None:
        raise HTTPException(404, "Container app not found.")
    return app


async def _pending_draft(db: AsyncSession, app_id: int, user_id: int | None) -> dict | None:
    if user_id is None:
        return None
    plans = list((await db.scalars(select(AiActionPlan).where(
        AiActionPlan.action_type == "container_app_patch",
        AiActionPlan.status == "awaiting_approval",
        AiActionPlan.user_id == user_id,
    ).order_by(AiActionPlan.id.desc()).limit(20))).all())
    for plan in plans:
        try:
            payload = json.loads(plan.payload_json)
        except (TypeError, ValueError):
            continue
        if payload.get("app_id") == app_id:
            return {"plan_id": plan.plan_id, "summary": plan.summary, "confidence": plan.confidence,
                    "reasoning": plan.reasoning, "payload": payload}
    return None


def _optional_deployment_id(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


router.include_router(resource_router)
