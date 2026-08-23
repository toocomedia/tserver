"""
router.py — FastAPI router for AI Helper: multi-provider management, settings, permissions, and streaming chat.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from plugins.ai_helper import service
from plugins.ai_helper.schemas import (
    ChatRequest,
    CreateSessionPayload,
    FetchModelsRequest,
    PermissionPolicyPayload,
    ProviderPayload,
    TestConnectionRequest,
    UpdateSessionPayload,
)
from plugins.ai_helper.services.chat import _ACTIVITY_PREFIX
from plugins.ai_helper.services.secrets_consent import grant_consent, revoke_consent
from templating import templates

router = APIRouter(prefix="/plugins/ai_helper", tags=["ai-helper"])


def _mask_key(decrypted_key: str) -> str:
    if not decrypted_key:
        return ""
    if len(decrypted_key) <= 8:
        return "••••••••"
    return f"{decrypted_key[:4]}••••••••{decrypted_key[-4:]}"


# -------------------------------------------------------------
# Web Views: List, Add, Edit
# -------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def ai_helper_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Main list view: displays all added AI providers, active status, and permission policy."""
    providers = await service.list_providers(db)
    active_provider = await service.get_active_provider(db)
    policy = await service.get_permission_policy(db)
    audit_logs = service.get_audit_logs(limit=25)
    resources = await service.get_discoverable_resources(db)

    return templates.TemplateResponse("ai_helper_list.html", {
        "request": request,
        "active_page": "ai_helper",
        "providers": providers,
        "active_provider": active_provider,
        "policy": policy,
        "audit_logs": audit_logs,
        "resources": resources,
        "presets": service.PROVIDER_PRESETS,
    })


@router.get("/create", response_class=HTMLResponse)
async def ai_helper_create_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Dedicated Add Provider form page."""
    return templates.TemplateResponse("ai_helper_form.html", {
        "request": request,
        "active_page": "ai_helper",
        "is_edit": False,
        "provider": None,
        "presets": service.PROVIDER_PRESETS,
    })


@router.post("/create")
async def ai_helper_create_action(
    request: Request,
    name: str = Form(...),
    provider_type: str = Form("openai_compatible"),
    api_key: str = Form(""),
    base_url: str = Form("https://api.openai.com/v1"),
    model_name: str = Form("gpt-4o-mini"),
    models_list: str = Form(""),
    temperature: float = Form(0.2),
    max_tokens: int = Form(4096),
    custom_rules: str = Form(""),
    is_default: bool = Form(False),
    is_enabled: bool = Form(True),
    db: AsyncSession = Depends(get_db),
):
    """Handles creating a new provider."""
    data = {
        "name": name,
        "provider_type": provider_type,
        "api_key": api_key,
        "base_url": base_url,
        "model_name": model_name,
        "models_list": models_list,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "custom_rules": custom_rules,
        "is_default": is_default,
        "is_enabled": is_enabled,
    }
    created = await service.create_provider(db, data)
    from middleware.auth import wants_json
    if wants_json(request):
        return JSONResponse({"status": "ok", "message": "Provider created successfully.", "id": created.id})
    return RedirectResponse(url="/plugins/ai_helper/", status_code=303)


@router.get("/{provider_id}/edit", response_class=HTMLResponse)
async def ai_helper_edit_page(provider_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Dedicated Edit Provider form page."""
    provider = await service.get_provider(db, provider_id)
    if not provider:
        raise HTTPException(404, "AI Provider not found.")

    raw_key = service.decrypt_key(provider.api_key_encrypted)
    masked_key = _mask_key(raw_key)

    return templates.TemplateResponse("ai_helper_form.html", {
        "request": request,
        "active_page": "ai_helper",
        "is_edit": True,
        "provider": provider,
        "has_api_key": bool(raw_key),
        "masked_api_key": masked_key,
        "presets": service.PROVIDER_PRESETS,
    })


@router.post("/{provider_id}/edit")
async def ai_helper_edit_action(
    provider_id: int,
    request: Request,
    name: str = Form(...),
    provider_type: str = Form("openai_compatible"),
    api_key: str = Form(""),
    base_url: str = Form("https://api.openai.com/v1"),
    model_name: str = Form("gpt-4o-mini"),
    models_list: str = Form(""),
    temperature: float = Form(0.2),
    max_tokens: int = Form(4096),
    custom_rules: str = Form(""),
    is_default: bool = Form(False),
    is_enabled: bool = Form(True),
    db: AsyncSession = Depends(get_db),
):
    """Handles updating an existing provider."""
    data = {
        "name": name,
        "provider_type": provider_type,
        "api_key": api_key if api_key.strip() else None,
        "base_url": base_url,
        "model_name": model_name,
        "models_list": models_list,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "custom_rules": custom_rules,
        "is_default": is_default,
        "is_enabled": is_enabled,
    }
    updated = await service.update_provider(db, provider_id, data)
    if not updated:
        raise HTTPException(404, "AI Provider not found.")
    from middleware.auth import wants_json
    if wants_json(request):
        return JSONResponse({"status": "ok", "message": "Provider updated successfully."})
    return RedirectResponse(url="/plugins/ai_helper/", status_code=303)


@router.post("/{provider_id}/set-default")
async def ai_helper_set_default(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Sets a provider as active default."""
    success = await service.set_default_provider(db, provider_id)
    if not success:
        raise HTTPException(404, "AI Provider not found.")
    return JSONResponse({"status": "ok", "message": "Default provider updated."})


@router.post("/{provider_id}/test")
async def ai_helper_test_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Tests connectivity to a stored provider."""
    result = await service.test_provider(db, provider_id)
    return JSONResponse(result)


@router.delete("/{provider_id}")
async def ai_helper_delete_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Deletes an AI provider."""
    success = await service.delete_provider(db, provider_id)
    if not success:
        raise HTTPException(404, "AI Provider not found.")
    return JSONResponse({"status": "ok", "message": "Provider deleted."})


# -------------------------------------------------------------
# Permissions API Endpoints
# -------------------------------------------------------------

@router.get("/api/resources")
async def get_resources_endpoint(db: AsyncSession = Depends(get_db)):
    """Returns discoverable system resources for interactive permission whitelists."""
    resources = await service.get_discoverable_resources(db)
    return JSONResponse({"status": "ok", "resources": resources})


@router.get("/api/permissions")
async def get_permissions_endpoint(db: AsyncSession = Depends(get_db)):
    """Returns current AI Helper permissions policy and summary."""
    policy = await service.get_permission_policy(db)
    return JSONResponse({
        "status": "ok",
        "policy": {
            "global_mode": policy.global_mode,
            "allow_domains_proxy": policy.allow_domains_proxy,
            "allow_dns": policy.allow_dns,
            "allow_php_sites": policy.allow_php_sites,
            "allow_container_apps": policy.allow_container_apps,
            "allow_databases": policy.allow_databases,
            "allow_files_read": policy.allow_files_read,
            "allowed_domains": policy.allowed_domains,
            "allowed_app_ids": policy.allowed_app_ids,
            "allowed_databases": policy.allowed_databases,
            "allowed_file_targets": policy.allowed_file_targets,
            "ask_on_demand": policy.ask_on_demand,
        }
    })


@router.post("/api/permissions")
async def update_permissions_endpoint(req: PermissionPolicyPayload, db: AsyncSession = Depends(get_db)):
    """Updates AI Helper permissions policy."""
    data = req.model_dump(exclude_unset=True)
    policy = await service.update_permission_policy(db, data)
    return JSONResponse({
        "status": "ok",
        "message": "Permission policy updated successfully.",
        "policy": {
            "global_mode": policy.global_mode,
            "allow_domains_proxy": policy.allow_domains_proxy,
            "allow_dns": policy.allow_dns,
            "allow_php_sites": policy.allow_php_sites,
            "allow_container_apps": policy.allow_container_apps,
            "allow_databases": policy.allow_databases,
            "allow_files_read": policy.allow_files_read,
            "allowed_domains": policy.allowed_domains,
            "allowed_app_ids": policy.allowed_app_ids,
            "allowed_databases": policy.allowed_databases,
            "allowed_file_targets": policy.allowed_file_targets,
            "ask_on_demand": policy.ask_on_demand,
        }
    })


@router.get("/api/audit-logs")
async def get_audit_logs_endpoint(limit: int = 50):
    """Returns recent tool call audit logs."""
    logs = service.get_audit_logs(limit=limit)
    return JSONResponse({"status": "ok", "logs": logs})


# -------------------------------------------------------------
# Providers & Streaming Chat Endpoints
# -------------------------------------------------------------

@router.get("/api/providers")
async def get_providers_list(db: AsyncSession = Depends(get_db)):
    """Returns list of enabled AI providers for client-side model switcher."""
    providers = await service.list_providers(db)
    items = [
        {
            "id": p.id,
            "name": p.name,
            "provider_type": p.provider_type,
            "model_name": p.model_name,
            "models": p.get_models(),
            "models_list": p.models_list,
            "is_default": p.is_default,
            "is_enabled": p.is_enabled,
        }
        for p in providers
        if p.is_enabled
    ]
    return JSONResponse({"status": "ok", "providers": items})


@router.post("/api/test-connection")
async def test_connection(req: TestConnectionRequest, db: AsyncSession = Depends(get_db)):
    data = req.model_dump(exclude_unset=True)
    result = await service.test_connection(db, override_data=data if data else None)
    return JSONResponse(result)


@router.post("/api/fetch-models")
async def fetch_models_endpoint(req: FetchModelsRequest, db: AsyncSession = Depends(get_db)):
    data = req.model_dump(exclude_unset=True)
    result = await service.fetch_models(db, override_data=data if data else None)
    return JSONResponse(result)


@router.post("/api/chat")
async def chat_endpoint(req: ChatRequest, request: Request, db: AsyncSession = Depends(get_db)):
    session_id = req.session_id or f"session_{uuid.uuid4().hex[:12]}"

    # If frontend explicitly sends allow_secrets=True, grant consent immediately
    if req.allow_secrets:
        grant_consent(session_id)

    if not req.stream:
        # Non-streaming response
        chunks = []
        async for chunk in service.stream_ai_chat(
            db=db,
            session_id=session_id,
            user_message=req.message,
            context_key=req.context_key,
            context_text=req.context,
            provider_id=req.provider_id,
            model_name=req.model_name,
            task_type=req.task_type or "general",
            session_title=req.session_title,
            user_id=request.session.get("user_id"),
        ):
            chunks.append(chunk)
        return JSONResponse({
            "status": "ok",
            "session_id": session_id,
            "response": "".join(chunks),
        })

    # Streaming response (Server-Sent Events)
    async def sse_event_generator():
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"
        async for chunk in service.stream_ai_chat(
            db=db,
            session_id=session_id,
            user_message=req.message,
            context_key=req.context_key,
            context_text=req.context,
            provider_id=req.provider_id,
            model_name=req.model_name,
            task_type=req.task_type or "general",
            session_title=req.session_title,
            user_id=request.session.get("user_id"),
        ):
            if chunk.startswith(_ACTIVITY_PREFIX):
                # Route tool activity events separately — don't include in text stream
                activity_json = chunk[len(_ACTIVITY_PREFIX):]
                yield f"data: {json.dumps({'type': 'tool_activity', 'activity': json.loads(activity_json)})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'token', 'token': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# -------------------------------------------------------------
# Secrets Consent API Endpoints
# -------------------------------------------------------------

@router.post("/api/sessions/{session_id}/allow-secrets")
async def grant_secrets_consent(session_id: str):
    """Grants secrets viewing consent for a specific chat session."""
    grant_consent(session_id)
    return JSONResponse({"status": "ok", "message": "Secrets consent granted for this session.", "session_id": session_id})


@router.delete("/api/sessions/{session_id}/allow-secrets")
async def revoke_secrets_consent(session_id: str):
    """Revokes secrets viewing consent for a specific chat session."""
    revoke_consent(session_id)
    return JSONResponse({"status": "ok", "message": "Secrets consent revoked. Credentials will be masked again.", "session_id": session_id})


# -------------------------------------------------------------
# Sessions & Task History Endpoints
# -------------------------------------------------------------

@router.get("/api/sessions")
async def list_sessions_endpoint(
    task_type: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Returns list of chat sessions, optionally filtered by task type."""
    sessions = await service.list_sessions(db, task_type=task_type, limit=limit)
    return JSONResponse({"status": "ok", "sessions": sessions})


@router.post("/api/sessions")
async def create_session_endpoint(
    req: CreateSessionPayload,
    db: AsyncSession = Depends(get_db),
):
    """Creates or initializes a new chat session."""
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    session = await service.get_or_create_session(
        db=db,
        session_id=session_id,
        title=req.title or "New Chat",
        task_type=req.task_type or "general",
        context_key=req.context_key,
        provider_id=req.provider_id,
        model_name=req.model_name,
    )
    return JSONResponse({
        "status": "ok",
        "session": {
            "id": session.id,
            "session_id": session.session_id,
            "title": session.title,
            "task_type": session.task_type,
            "context_key": session.context_key,
            "model_name": session.model_name,
            "provider_id": session.provider_id,
            "message_count": session.message_count,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }
    })


@router.get("/api/sessions/{session_id}")
async def get_session_endpoint(session_id: str, db: AsyncSession = Depends(get_db)):
    """Gets metadata for a specific session."""
    session = await service.get_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    return JSONResponse({"status": "ok", "session": session})


@router.patch("/api/sessions/{session_id}")
async def update_session_endpoint(
    session_id: str,
    req: UpdateSessionPayload,
    db: AsyncSession = Depends(get_db),
):
    """Updates session title or task type."""
    data = req.model_dump(exclude_unset=True)
    updated = await service.update_session(db, session_id, data)
    if not updated:
        raise HTTPException(404, "Session not found.")
    return JSONResponse({"status": "ok", "session": updated})


@router.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str, db: AsyncSession = Depends(get_db)):
    """Deletes a session and its message history."""
    await service.delete_session(db, session_id)
    return JSONResponse({"status": "ok", "message": "Session and messages deleted."})


@router.delete("/api/sessions")
async def clear_all_sessions_endpoint(
    task_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Clears all conversation history or history for a specific task."""
    await service.clear_all_sessions(db, task_type=task_type)
    return JSONResponse({"status": "ok", "message": "All session histories cleared."})


@router.get("/api/sessions/{session_id}/messages")
async def get_session_history(session_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieves all chat messages for a session."""
    messages = await service.get_session_messages(db, session_id)
    return JSONResponse({"status": "ok", "session_id": session_id, "messages": messages})


# -------------------------------------------------------------
# Action Plans API Endpoints
# -------------------------------------------------------------

@router.get("/api/action-plans/{plan_id}")
async def get_action_plan_endpoint(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Retrieves validated, server-side action plan details by opaque ID."""
    from plugins.ai_helper.services import action_plans
    plan = await action_plans.get_action_plan(db, plan_id, user_id=request.session.get("user_id"))
    if not plan:
        raise HTTPException(404, "Action plan not found or expired.")
    return JSONResponse({"status": "ok", "plan": plan})


@router.post("/api/action-plans/{plan_id}/mark-applied")
async def mark_action_plan_applied_endpoint(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Marks an action plan as applied with replay protection."""
    from plugins.ai_helper.services import action_plans
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    try:
        res = await action_plans.mark_plan_applied(db, plan_id, user_id=user_id)
        return JSONResponse(res)
    except ValueError as exc:
        if "already been applied" in str(exc):
            return JSONResponse({"status": "ok", "already_applied": True, "plan_id": plan_id})
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/create-stack-plan")
async def create_stack_plan_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    """Creates a reviewed general-stack plan from restricted structured fields."""
    from plugins.ai_helper.tools.app_setup import propose_stack_install
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    if user_id is None:
        raise HTTPException(401, "Authentication required.")

    body = await request.json()
    stack_manifest = body.get("stack_manifest")
    if not isinstance(stack_manifest, dict):
        raise HTTPException(400, "Structured stack manifest is required.")
    session_id = str(body.get("session_id") or "default_session").strip()
    domain_name = str(body.get("domain_name") or "").strip()
    nonsecret_settings = body.get("nonsecret_settings") or {}

    res = await propose_stack_install(
        db=db,
        stack_manifest=stack_manifest,
        domain_name=domain_name,
        nonsecret_settings=nonsecret_settings,
        evidence=body.get("evidence") or [],
        session_id=session_id,
        user_id=user_id,
    )
    if res.get("status") != "ok":
        raise HTTPException(400, res.get("message", "Could not create stack plan."))
    return JSONResponse(res)
