"""Apps Engine create-page and deployment-start endpoints."""
from __future__ import annotations

import json
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.domain import Domain
from models.hosted_app import HostedApp
from models.ssl_cert import SslCert
from services import container_app_database_service, container_app_deployment_service
from services import container_app_image_inspect_service, container_app_inspection_service, container_app_service, container_app_wordpress_service
from services.apps_engine import secret_vault, snapshots
from services.official_stacks import compose_runtime
from services.official_stacks.schema import stack_from_dict
from services.official_stacks.manifest_validator import compute_stack_manifest_hash, validate_stack_manifest
from plugins.ai_helper.services import action_plans
from services.apps_engine import reviewed_setup_deploy
from dependencies.git import repository_service
from templating import templates

router = APIRouter()


@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request, db: AsyncSession = Depends(get_db)):
    used = set((await db.scalars(select(ContainerApp.domain_id))).all())
    used.update((await db.scalars(select(HostedApp.domain_id))).all())
    domains = (await db.scalars(select(Domain).order_by(Domain.name))).all()
    ssl_domain_names = set((await db.scalars(select(SslCert.full_domain))).all())
    # Load Supabase projects for the provider picker
    supabase_projects = []
    try:
        from models.supabase_project import SupabaseProject
        supabase_projects = list((await db.scalars(
            select(SupabaseProject).order_by(SupabaseProject.name)
        )).all())
    except Exception:
        pass
    initial_plan = None
    plan_id = str(request.query_params.get("plan") or "").strip()
    if plan_id:
        try:
            initial_plan = await action_plans.get_action_plan(db, plan_id)
        except Exception:
            pass
    return templates.TemplateResponse("railpack_apps_create.html", {
        "request": request, "active_page": "railpack_apps", "domains": domains, "used_domain_ids": used,
        "ssl_domain_names": ssl_domain_names, "supabase_projects": supabase_projects,
        "initial_plan": initial_plan,
    })



def _resolve_session_draft_key(request: Request, draft_key_id: str | None) -> Path | None:
    if not draft_key_id or not draft_key_id.strip():
        return None
    did = draft_key_id.strip()
    session_drafts = request.session.get("draft_deploy_keys")
    if not isinstance(session_drafts, list) or did not in session_drafts:
        raise HTTPException(403, "Draft deploy key was not generated in this session.")
    return repository_service.get_draft_deploy_key_path(did)


@router.post("/draft-deploy-key")
async def draft_deploy_key(request: Request):
    try:
        draft_id, public_key = await asyncio.to_thread(repository_service.create_draft_deploy_key)
        session_drafts = request.session.get("draft_deploy_keys", [])
        if not isinstance(session_drafts, list):
            session_drafts = []
        session_drafts.append(draft_id)
        request.session["draft_deploy_keys"] = session_drafts[-20:]
        return JSONResponse({"draft_id": draft_id, "public_key": public_key})
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(500, f"Could not generate deploy key: {exc}") from exc


@router.post("/inspect")
async def inspect(request: Request, repository_url: str = Form(...), branch: str = Form("main"), draft_key_id: str = Form("")):
    ssh_key = _resolve_session_draft_key(request, draft_key_id)
    return JSONResponse(container_app_inspection_service.inspect_repository(repository_url.strip(), branch.strip() or "main", ssh_key_path=ssh_key))


@router.post("/inspect-branches")
async def inspect_branches(request: Request, repository_url: str = Form(...), draft_key_id: str = Form("")):
    try:
        ssh_key = _resolve_session_draft_key(request, draft_key_id)
        result = await asyncio.to_thread(repository_service.list_branches, repository_url.strip(), ssh_key_path=ssh_key)
        return JSONResponse({"default_branch": result.default_branch, "branches": result.branches})
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(400, str(exc))


@router.post("/inspect-image")
async def inspect_image(image_reference: str = Form(...)):
    try:
        return JSONResponse(await container_app_image_inspect_service.inspect_image(image_reference.strip()))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/deploy-reviewed-plan/{plan_id}")
async def deploy_reviewed_plan(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        app_id, deployment_id = await reviewed_setup_deploy.deploy_plan(
            db, plan_id.strip(), user_id=request.session.get("user_id"),
        )
        return JSONResponse({
            "status": "ok",
            "app_id": app_id,
            "deployment_id": deployment_id,
            "redirect": f"/plugins/railpack_apps/{app_id}?deployment={deployment_id}",
        })
    except HTTPException:
        raise
    except ValueError as exc:
        status_code = 409 if any(w in str(exc).lower() for w in ("already", "conflict", "executing", "applied")) else 400
        raise HTTPException(status_code, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Deploy reviewed plan failed for plan %s: %s", plan_id, exc)
        raise HTTPException(400, f"Deployment failed: {exc}") from exc


@router.post("/create")
async def create(
    request: Request, domain_id: int = Form(...), source_type: str = Form(...), build_mode: str = Form("railpack"),
    repository_url: str = Form(""), branch: str = Form("main"), image_reference: str = Form(""),
    internal_port: int = Form(3000), ssl: bool = Form(False), environment_values: str = Form("{}"), secret_requirements: str = Form("[]"),
    database_mode: str = Form("none"), database_url: str = Form(""), database_attachments: str = Form(""),
    preset: str = Form(""), wordpress_site_title: str = Form(""), wordpress_admin_user: str = Form(""),
    wordpress_admin_email: str = Form(""), wordpress_admin_password: str = Form(""),
    git_ref: str = Form(""), git_ref_type: str = Form("branch"), draft_key_id: str = Form(""),
    root_directory: str = Form(""), dockerfile_path: str = Form("Dockerfile"),
    build_args: str = Form(""), build_secret_keys: str = Form(""), custom_start_command: str = Form(""),
    storage_mounts: str = Form("[]"), health_path: str = Form("disabled"),
    startup_timeout_seconds: int = Form(45),
    deploy_type: str = Form("railpack"), stack_catalog_id: str = Form(""),
    stack_version: str = Form(""), nonsecret_settings: str = Form("{}"), stack_plan_id: str = Form(""), app_plan_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if draft_key_id.strip():
        _resolve_session_draft_key(request, draft_key_id)
        repo = repository_url.strip()
        if not (repo.startswith("git@") or repo.startswith("ssh://")):
            raise HTTPException(400, "SSH deploy keys require an SSH repository URL (e.g. git@github.com:owner/repo.git).")
    domain = await db.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found.")
    has_certificate = await db.scalar(select(SslCert.id).where(SslCert.full_domain == domain.name)) is not None

    if deploy_type in {"official_stack", "app_spec"} or source_type in {"official_stack", "app_spec"}:
        plan_id = (stack_plan_id or app_plan_id).strip()
        if not plan_id:
            raise HTTPException(400, "Choose a reviewed stack plan before deployment.")
        plan = await action_plans.get_action_plan(db, plan_id, user_id=request.session.get("user_id"))
        if not plan or not action_plans.payload_is_intact(plan) or plan["status"] != "awaiting_approval" or plan["action_type"] not in {"stack_install", "official_stack_install", "app_spec_install"}:
            raise HTTPException(400, "Stack review plan is unavailable, expired, or already used.")
        if plan["action_type"] == "app_spec_install":
            app_id, deployment_id = await reviewed_setup_deploy.deploy_plan(
                db, plan_id, user_id=request.session.get("user_id"), ssl_requested=ssl,
            )
            return _create_response(request, app_id, deployment_id)
        payload = plan.get("payload") or {}
        try:
            stack = validate_stack_manifest(stack_from_dict(payload.get("stack_manifest") or {}))
            v = str(payload.get("stack_version") or stack.default_version)
            if payload.get("stack_catalog_id") != stack.catalog_id or payload.get("manifest_hash") != compute_stack_manifest_hash(stack, v):
                raise ValueError("Stack review plan does not match its server manifest.")
            clean_settings = _environment_values(json.dumps(payload.get("nonsecret_settings") or {}))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

        try:
            secret_vault.encrypt("")
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        app = await container_app_service.create_app(
            db, domain=domain, source_type="image", build_mode="image",
            deploy_type="official_stack", stack_catalog_id=stack.catalog_id, stack_version=v,
            stack_services=compose_runtime.manifest_json(stack),
            repository_url=stack.official_repositories[0] if stack.official_repositories else None,
            branch=v, image_reference=stack.services[stack.web_service_name].image_reference,
            internal_port=stack.web_internal_port,
            ssl_requested=ssl and not has_certificate,
            environment_values=clean_settings,
            health_path=stack.web_health_path or "disabled",
            startup_timeout_seconds=stack.startup_timeout_seconds,
        )

        container_app_service.write_env(Path(app.env_path), clean_settings)

        await snapshots.create_snapshot(
            db, app, environment_patch=clean_settings, plan_id=plan_id,
            created_by_user_id=request.session.get("user_id"),
        )
        domain.project_type = "container"
        deployment = await container_app_deployment_service.queue_deployment(db, app)
        try:
            await action_plans.mark_plan_applied(
                db, plan_id, user_id=request.session.get("user_id"), expected_hash=plan["payload_hash"],
                expected_action_type=plan["action_type"],
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await db.commit()
        return _create_response(request, app.id, deployment.id)

    app_plan = None
    if app_plan_id.strip():
        app_plan = await action_plans.get_action_plan(db, app_plan_id.strip(), user_id=request.session.get("user_id"))
        if app_plan and action_plans.payload_is_intact(app_plan) and app_plan.get("status") == "awaiting_approval":
            plan_payload = app_plan.get("payload") or {}
            plan_domain = str(plan_payload.get("domain_name") or "").strip().lower()
            if plan_domain and plan_domain != domain.name.lower():
                raise HTTPException(400, "Application setup plan was prepared for another domain.")
            # Populate form defaults only if not provided by user in the form
            if not environment_values or environment_values.strip() in ("{}", ""):
                environment_values = json.dumps(plan_payload.get("environment_values") or {})
            if not secret_requirements or secret_requirements.strip() in ("[]", ""):
                secret_requirements = json.dumps(plan_payload.get("secret_requirements") or [])
            if not database_attachments or database_attachments.strip() in ("[]", ""):
                database_attachments = json.dumps(plan_payload.get("database_attachments") or [])
            if not storage_mounts or storage_mounts.strip() in ("[]", ""):
                storage_mounts = json.dumps(plan_payload.get("storage_mounts") or [])
            if not repository_url.strip() and plan_payload.get("repository_url"):
                repository_url = str(plan_payload.get("repository_url") or "")
            if not image_reference.strip() and plan_payload.get("image_reference"):
                image_reference = str(plan_payload.get("image_reference") or "")

    requested_secrets = _secret_requirements(secret_requirements)
    try:
        secret_vault.encrypt("")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    attachments = _attachments(database_attachments)
    if preset == "wordpress":
        attachments = _prepare_wordpress(attachments, wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password)
        source_type, build_mode, image_reference, internal_port = "image", "image", container_app_wordpress_service.WP_IMAGE, 80
    app = await container_app_service.create_app(
        db, domain=domain, source_type=source_type, build_mode=build_mode, repository_url=repository_url.strip() or None,
        branch=branch.strip() or "main", image_reference=image_reference.strip() or None, internal_port=internal_port,
        ssl_requested=ssl and not has_certificate, environment_values=_environment_values(environment_values), database_mode=database_mode,
        database_url=database_url.strip() or None, database_attachments=attachments,
        git_ref=git_ref.strip() or None, git_ref_type=git_ref_type.strip() or "branch",
        draft_key_id=draft_key_id.strip() or None, root_directory=root_directory.strip(),
        dockerfile_path=dockerfile_path.strip() or "Dockerfile", build_args=build_args.strip() or None,
        build_secret_keys=build_secret_keys.strip() or None,
        custom_start_command=custom_start_command.strip() or None, storage_mounts=storage_mounts.strip() or None,
        health_path=health_path.strip() or "disabled", startup_timeout_seconds=startup_timeout_seconds,
    )
    if preset == "wordpress":
        await _configure_wordpress(app, wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password, db)
    if requested_secrets or app_plan:
        await snapshots.create_snapshot(
            db, app, secret_requirements=requested_secrets, plan_id=app_plan_id.strip() or None,
            created_by_user_id=request.session.get("user_id"),
        )
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
    if app_plan and app_plan.get("status") == "awaiting_approval":
        try:
            await action_plans.mark_plan_applied(
                db, app_plan_id.strip(), user_id=request.session.get("user_id"),
                expected_hash=app_plan["payload_hash"],
                expected_action_type=app_plan.get("action_type"),
            )
        except Exception as exc:
            logger.warning("Could not mark action plan applied: %s", exc)
    await db.commit()
    return _create_response(request, app.id, deployment.id)


def _attachments(raw: str) -> list[dict[str, str]] | None:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Database attachments are invalid.") from exc


def _prepare_wordpress(attachments, title: str, user: str, email: str, password: str) -> list[dict[str, str]]:
    container_app_wordpress_service.validate_setup(title, user, email, password)
    attachments = attachments if attachments is not None else []
    if not any(item.get("kind") == "mariadb" for item in attachments if isinstance(item, dict)):
        attachments.append({"kind": "mariadb", "provider": "docker", "environment_key": "MYSQL_URL"})
    return attachments


async def _configure_wordpress(app, title: str, user: str, email: str, password: str, db: AsyncSession) -> None:
    container_app_wordpress_service.prepare(app, title, user, email, password)
    items = await container_app_database_service.attachments_for(db, app.id)
    container_app_database_service.rebuild_environment(app, items, container_app_database_service.read_app_environment(app))


def _environment_values(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Environment values are invalid.") from exc
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise HTTPException(400, "Environment values must be a key/value object.")
    return value


def _secret_requirements(raw: str) -> list[dict]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Secret requirements are invalid.") from exc
    if not isinstance(value, list) or len(value) > 32 or any(not isinstance(item, dict) for item in value):
        raise HTTPException(400, "Secret requirements are invalid.")
    return value


def _create_response(request: Request, app_id: int, deployment_id: int):
    location = f"/plugins/railpack_apps/{app_id}?deployment={deployment_id}"
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"app_id": app_id, "deployment_id": deployment_id, "redirect": location})
    return RedirectResponse(location, status_code=303)
