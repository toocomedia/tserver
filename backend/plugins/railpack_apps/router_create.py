"""Apps Engine create-page and deployment-start endpoints."""
from __future__ import annotations

import json
import asyncio

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
    return templates.TemplateResponse("railpack_apps_create.html", {
        "request": request, "active_page": "railpack_apps", "domains": domains, "used_domain_ids": used,
        "ssl_domain_names": ssl_domain_names, "supabase_projects": supabase_projects,
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
    storage_mounts: str = Form("[]"), health_path: str = Form("/"),
    startup_timeout_seconds: int = Form(45),
    deploy_type: str = Form("railpack"), stack_catalog_id: str = Form(""),
    stack_version: str = Form(""), nonsecret_settings: str = Form("{}"),
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

    if deploy_type == "official_stack" or source_type == "official_stack":
        from services.official_stacks.catalog import get_stack
        from services.official_stacks.manifest_validator import validate_stack_request
        from services.official_stacks import stack_runtime_service
        cat_id = (stack_catalog_id or "plausible_ce").strip()
        stack = get_stack(cat_id)
        if stack is None:
            raise HTTPException(404, f"Official stack '{cat_id}' was not found in catalog.")
        v = (stack_version.strip() or stack.default_version)
        parsed_settings = _environment_values(nonsecret_settings or "{}")
        if not parsed_settings and environment_values and environment_values.strip() != "{}":
            parsed_settings = _environment_values(environment_values)
        try:
            _, clean_settings = validate_stack_request(cat_id, v, parsed_settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        secret_vault.encrypt("")
        app = await container_app_service.create_app(
            db, domain=domain, source_type="image", build_mode="image",
            deploy_type="official_stack", stack_catalog_id=cat_id, stack_version=v,
            repository_url=stack.official_repositories[0] if stack.official_repositories else None,
            branch=v, image_reference=stack.services[stack.web_service_name].image_reference,
            internal_port=stack.web_internal_port,
            ssl_requested=ssl and not has_certificate,
            environment_values={},
            health_path=stack.web_health_path,
            startup_timeout_seconds=stack.startup_timeout_seconds,
        )

        real_vault_secrets = {}
        secret_reqs_for_snapshot = []
        for sec_req in stack.required_secrets:
            sec_rec, _ = await secret_vault.ensure_secret(db, app.id, sec_req.key, sec_req.purpose)
            real_vault_secrets[sec_req.key] = await secret_vault.secret_value(db, sec_rec.id)
            secret_reqs_for_snapshot.append({"key": sec_req.key, "purpose": sec_req.purpose})

        compiled_env = stack_runtime_service.compile_stack_environment(
            app, stack, domain.name, real_vault_secrets, clean_settings,
        )
        container_app_service.write_env(Path(app.env_path), compiled_env)

        await snapshots.create_snapshot(
            db, app, secret_requirements=secret_reqs_for_snapshot,
            environment_patch=compiled_env, created_by_user_id=request.session.get("user_id"),
        )
        domain.project_type = "container"
        deployment = await container_app_deployment_service.queue_deployment(db, app)
        await db.commit()
        return _create_response(request, app.id, deployment.id)

    requested_secrets = _secret_requirements(secret_requirements)
    if requested_secrets:
        # Fail before creating databases, app directories, or files when panel key is not persistent.
        secret_vault.encrypt("")
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
        health_path=health_path.strip() or "/", startup_timeout_seconds=startup_timeout_seconds,
    )
    if preset == "wordpress":
        await _configure_wordpress(app, wordpress_site_title, wordpress_admin_user, wordpress_admin_email, wordpress_admin_password, db)
    if requested_secrets:
        await snapshots.create_snapshot(
            db, app, secret_requirements=requested_secrets, created_by_user_id=request.session.get("user_id"),
        )
    domain.project_type = "container"
    deployment = await container_app_deployment_service.queue_deployment(db, app)
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
