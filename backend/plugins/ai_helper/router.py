"""
router.py — FastAPI router for AI Helper settings dashboard, streaming chat, and connection testing.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from plugins.ai_helper import service
from templating import templates

router = APIRouter(prefix="/plugins/ai_helper", tags=["ai-helper"])


class SettingsUpdateRequest(BaseModel):
    is_enabled: Optional[bool] = True
    provider_type: Optional[str] = "openai_compatible"
    api_key: Optional[str] = None
    base_url: Optional[str] = "https://api.openai.com/v1"
    model_name: Optional[str] = "gpt-4o-mini"
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 4096
    custom_rules: Optional[str] = ""


class TestConnectionRequest(BaseModel):
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None


class FetchModelsRequest(BaseModel):
    provider_type: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    context_key: Optional[str] = None
    context: Optional[str] = None
    stream: bool = True


def _mask_key(decrypted_key: str) -> str:
    if not decrypted_key:
        return ""
    if len(decrypted_key) <= 8:
        return "••••••••"
    return f"{decrypted_key[:4]}••••••••{decrypted_key[-4:]}"


@router.get("/", response_class=HTMLResponse)
async def ai_helper_settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    settings = await service.get_settings(db)
    raw_key = service.decrypt_key(settings.api_key_encrypted)
    has_api_key = bool(raw_key)
    masked_key = _mask_key(raw_key)

    return templates.TemplateResponse("ai_helper_settings.html", {
        "request": request,
        "active_page": "ai_helper",
        "settings": settings,
        "has_api_key": has_api_key,
        "masked_api_key": masked_key,
        "presets": service.PROVIDER_PRESETS,
    })


@router.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest, db: AsyncSession = Depends(get_db)):
    data = req.model_dump(exclude_unset=True)
    settings = await service.save_settings(db, data)
    return JSONResponse({
        "status": "ok",
        "message": "AI Assistant settings saved successfully.",
        "is_enabled": settings.is_enabled,
        "provider_type": settings.provider_type,
        "model_name": settings.model_name,
    })


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
