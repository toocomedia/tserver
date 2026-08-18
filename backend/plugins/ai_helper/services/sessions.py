"""
services/sessions.py — AI Chat Session management, task separation, auto-titling, and message retrieval.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ai_helper import AiChatMessage, AiChatSession

logger = logging.getLogger(__name__)


def generate_title_from_prompt(prompt: str) -> str:
    """Generates a clean, human-readable session title from the user's initial prompt."""
    if not prompt or not prompt.strip():
        return "New Chat"

    cleaned = prompt.strip()
    cleaned = cleaned.replace("```", "").replace("#", "").strip()

    prefixes = [
        "how do i ", "how can i ", "can you help me ", "can you explain ",
        "explain ", "please ", "i need help with ", "how to ", "what is ",
        "what are ", "why is ", "why does ", "tell me about ",
    ]
    lower = cleaned.lower()
    for p in prefixes:
        if lower.startswith(p):
            cleaned = cleaned[len(p):].strip()
            break

    if not cleaned:
        cleaned = prompt.strip()

    cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()

    if len(cleaned) > 45:
        truncated = cleaned[:45]
        last_space = truncated.rfind(" ")
        if last_space > 25:
            cleaned = truncated[:last_space].rstrip(",.:;-") + "..."
        else:
            cleaned = truncated.rstrip(",.:;-") + "..."

    return cleaned or "New Chat"


async def get_or_create_session(
    db: AsyncSession,
    session_id: str,
    title: Optional[str] = None,
    task_type: str = "general",
    context_key: Optional[str] = None,
    provider_id: Optional[int] = None,
    model_name: Optional[str] = None,
) -> AiChatSession:
    """Retrieves an existing chat session or creates a new one."""
    stmt = select(AiChatSession).where(AiChatSession.session_id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session:
        session.updated_at = datetime.now()
        if task_type and task_type != "general" and session.task_type == "general":
            session.task_type = task_type
        if context_key and not session.context_key:
            session.context_key = context_key
        if model_name:
            session.model_name = model_name
        if provider_id:
            session.provider_id = provider_id
        await db.commit()
        await db.refresh(session)
        return session

    msg_count_stmt = select(func.count(AiChatMessage.id)).where(AiChatMessage.session_id == session_id)
    msg_count_res = await db.execute(msg_count_stmt)
    existing_count = msg_count_res.scalar() or 0

    session = AiChatSession(
        session_id=session_id,
        title=title or "New Chat",
        task_type=task_type or "general",
        context_key=context_key,
        model_name=model_name,
        provider_id=provider_id,
        message_count=existing_count,
        is_archived=False,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions(
    db: AsyncSession,
    task_type: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Lists recent chat sessions, optionally filtered by task type."""
    stmt = select(AiChatSession).where(AiChatSession.is_archived == False)
    if task_type and task_type.lower() not in ("all", "*", ""):
        stmt = stmt.where(AiChatSession.task_type == task_type.lower())

    stmt = stmt.order_by(AiChatSession.updated_at.desc()).limit(limit)
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    items = []
    for s in sessions:
        last_msg_stmt = (
            select(AiChatMessage.content)
            .where(AiChatMessage.session_id == s.session_id)
            .order_by(AiChatMessage.id.desc())
            .limit(1)
        )
        last_msg_res = await db.execute(last_msg_stmt)
        last_content = last_msg_res.scalar_one_or_none()
        preview = ""
        if last_content:
            preview = last_content[:90].replace("\n", " ").strip()
            if len(last_content) > 90:
                preview += "..."

        items.append({
            "id": s.id,
            "session_id": s.session_id,
            "title": s.title,
            "task_type": s.task_type,
            "context_key": s.context_key,
            "model_name": s.model_name,
            "provider_id": s.provider_id,
            "message_count": s.message_count,
            "last_message": preview,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        })

    return items


async def get_session(db: AsyncSession, session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves session metadata."""
    stmt = select(AiChatSession).where(AiChatSession.session_id == session_id)
    res = await db.execute(stmt)
    s = res.scalar_one_or_none()
    if not s:
        msg_count_stmt = select(func.count(AiChatMessage.id)).where(AiChatMessage.session_id == session_id)
        msg_count = (await db.execute(msg_count_stmt)).scalar() or 0
        if msg_count > 0:
            s = await get_or_create_session(db, session_id)
        else:
            return None

    return {
        "id": s.id,
        "session_id": s.session_id,
        "title": s.title,
        "task_type": s.task_type,
        "context_key": s.context_key,
        "model_name": s.model_name,
        "provider_id": s.provider_id,
        "message_count": s.message_count,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def update_session(db: AsyncSession, session_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Updates session title, task_type, or archive state."""
    stmt = select(AiChatSession).where(AiChatSession.session_id == session_id)
    res = await db.execute(stmt)
    s = res.scalar_one_or_none()
    if not s:
        return None

    if "title" in data and data["title"] is not None:
        s.title = str(data["title"]).strip()
    if "task_type" in data and data["task_type"] is not None:
        s.task_type = str(data["task_type"]).strip()
    if "is_archived" in data and data["is_archived"] is not None:
        s.is_archived = bool(data["is_archived"])

    s.updated_at = datetime.now()
    await db.commit()
    await db.refresh(s)
    return {
        "id": s.id,
        "session_id": s.session_id,
        "title": s.title,
        "task_type": s.task_type,
        "is_archived": s.is_archived,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def delete_session(db: AsyncSession, session_id: str) -> bool:
    """Deletes all stored chat messages and the session record."""
    await db.execute(delete(AiChatMessage).where(AiChatMessage.session_id == session_id))
    await db.execute(delete(AiChatSession).where(AiChatSession.session_id == session_id))
    await db.commit()
    return True


async def clear_all_sessions(db: AsyncSession, task_type: Optional[str] = None) -> bool:
    """Clears all sessions and their messages, optionally for a specific task."""
    if task_type and task_type.lower() not in ("all", "*", ""):
        stmt = select(AiChatSession.session_id).where(AiChatSession.task_type == task_type.lower())
        s_ids = (await db.execute(stmt)).scalars().all()
        if s_ids:
            await db.execute(delete(AiChatMessage).where(AiChatMessage.session_id.in_(s_ids)))
            await db.execute(delete(AiChatSession).where(AiChatSession.session_id.in_(s_ids)))
    else:
        await db.execute(delete(AiChatMessage))
        await db.execute(delete(AiChatSession))
    await db.commit()
    return True


async def get_session_messages(db: AsyncSession, session_id: str) -> List[Dict[str, Any]]:
    """Retrieves all chat messages for a session."""
    stmt = (
        select(AiChatMessage)
        .where(AiChatMessage.session_id == session_id)
        .order_by(AiChatMessage.id.asc())
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "context_key": r.context_key,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


async def clear_session(db: AsyncSession, session_id: str) -> bool:
    """Deletes all stored chat messages and session record."""
    return await delete_session(db, session_id)
