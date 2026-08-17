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
    FetchModelsRequest,
    PermissionPolicyPayload,
    ProviderPayload,
    TestConnectionRequest,
)
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

    return templates.TemplateResponse("ai_helper_list.html", {
        "request": request,
        "active_page": "ai_helper",
        "providers": providers,
        "active_provider": active_provider,
        "policy": policy,
        "audit_logs": audit_logs,
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
    await service.create_provider(db, data)
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
            "is_default": p.is_default,
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
async def chat_endpoint(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    session_id = req.session_id or f"session_{uuid.uuid4().hex[:12]}"

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
        ):
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


@router.get("/api/sessions/{session_id}/messages")
async def get_session_history(session_id: str, db: AsyncSession = Depends(get_db)):
    messages = await service.get_session_messages(db, session_id)
    return JSONResponse({"status": "ok", "session_id": session_id, "messages": messages})


@router.delete("/api/sessions/{session_id}")
async def clear_session_history(session_id: str, db: AsyncSession = Depends(get_db)):
    await service.clear_session(db, session_id)
    return JSONResponse({"status": "ok", "message": "Session history cleared."})
