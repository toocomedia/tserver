"""
service.py — AI Helper service layer: multi-provider management, key encryption, and chat pipeline.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import AsyncGenerator, Dict, List, Optional

from cryptography.fernet import Fernet
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.ai_helper import AiChatMessage, AiHelperSettings, AiProvider
from plugins.ai_helper import engine, prompts

logger = logging.getLogger(__name__)

PROVIDER_PRESETS: Dict[str, Dict[str, any]] = {
    "openai": {
        "name": "OpenAI",
        "type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "desc": "Official OpenAI API",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "desc": "Anthropic Messages API",
    },
    "deepseek": {
        "name": "DeepSeek",
        "type": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "desc": "DeepSeek API",
    },
    "openrouter": {
        "name": "OpenRouter",
        "type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "desc": "Universal Multi-Model Router",
    },
    "groq": {
        "name": "Groq",
        "type": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "desc": "High-Speed LPU Inference",
    },
    "gemini": {
        "name": "Google Gemini",
        "type": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "desc": "Google Gemini (OpenAI endpoint)",
    },
    "mistral": {
        "name": "Mistral AI",
        "type": "openai_compatible",
        "base_url": "https://api.mistral.ai/v1",
        "desc": "Mistral AI",
    },
    "together": {
        "name": "Together AI",
        "type": "openai_compatible",
        "base_url": "https://api.together.xyz/v1",
        "desc": "Together AI Engine",
    },
    "custom": {
        "name": "Custom Endpoint",
        "type": "openai_compatible",
        "base_url": "",
        "desc": "Self-hosted / custom endpoint",
    },
}



def _get_fernet() -> Fernet:
    secret = config.SECRET_KEY or "fallback-srv-panel-secret-key-32"
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_key(raw_key: str) -> str:
    if not raw_key:
        return ""
    return _get_fernet().encrypt(raw_key.strip().encode()).decode()


def decrypt_key(encrypted_token: str | None) -> str:
    if not encrypted_token:
        return ""
    try:
        return _get_fernet().decrypt(encrypted_token.encode()).decode()
    except Exception as exc:
        logger.warning("Could not decrypt AI API key: %s", exc)
        return ""


# -------------------------------------------------------------
# Provider CRUD Operations
# -------------------------------------------------------------

async def list_providers(db: AsyncSession) -> List[AiProvider]:
    """Returns all added AI providers ordered by default first, then newest."""
    stmt = select(AiProvider).order_by(AiProvider.is_default.desc(), AiProvider.id.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_provider(db: AsyncSession, provider_id: int) -> Optional[AiProvider]:
    return await db.get(AiProvider, provider_id)


async def get_active_provider(db: AsyncSession) -> Optional[AiProvider]:
    """Finds the default enabled provider, or falls back to the first enabled one."""
    stmt = select(AiProvider).where(AiProvider.is_default == True, AiProvider.is_enabled == True)
    result = await db.execute(stmt)
    active = result.scalar_one_or_none()
    if active:
        return active

    stmt_any = select(AiProvider).where(AiProvider.is_enabled == True).order_by(AiProvider.id.asc()).limit(1)
    res_any = await db.execute(stmt_any)
    return res_any.scalar_one_or_none()


async def create_provider(db: AsyncSession, data: Dict[str, any]) -> AiProvider:
    """Adds a new AI provider."""
    # If first provider or is_default is specified, set default
    existing_count = (await db.execute(select(AiProvider))).scalars().all()
    should_be_default = bool(data.get("is_default")) or len(existing_count) == 0

    if should_be_default:
        await db.execute(update(AiProvider).values(is_default=False))

    raw_key = data.get("api_key") or ""
    provider = AiProvider(
        name=str(data.get("name") or "New Provider").strip(),
        provider_type=str(data.get("provider_type") or "openai_compatible").strip(),
        api_key_encrypted=encrypt_key(raw_key) if raw_key else None,
        base_url=str(data.get("base_url") or "https://api.openai.com/v1").strip(),
        model_name=str(data.get("model_name") or "gpt-4o-mini").strip(),
        temperature=float(data.get("temperature", 0.2)),
        max_tokens=int(data.get("max_tokens", 4096)),
        custom_rules=str(data.get("custom_rules") or "").strip(),
        is_default=should_be_default,
        is_enabled=bool(data.get("is_enabled", True)),
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


async def update_provider(db: AsyncSession, provider_id: int, data: Dict[str, any]) -> Optional[AiProvider]:
    """Updates an existing provider."""
    provider = await db.get(AiProvider, provider_id)
    if not provider:
        return None

    if data.get("is_default"):
        await db.execute(update(AiProvider).values(is_default=False))
        provider.is_default = True

    if "name" in data:
        provider.name = str(data["name"]).strip()
    if "provider_type" in data:
        provider.provider_type = str(data["provider_type"]).strip()
    if "base_url" in data:
        provider.base_url = str(data["base_url"]).strip()
    if "model_name" in data:
        provider.model_name = str(data["model_name"]).strip()
    if "temperature" in data:
        try:
            provider.temperature = float(data["temperature"])
        except (ValueError, TypeError):
            pass
    if "max_tokens" in data:
        try:
            provider.max_tokens = int(data["max_tokens"])
        except (ValueError, TypeError):
            pass
    if "custom_rules" in data:
        provider.custom_rules = str(data["custom_rules"]).strip()
    if "is_enabled" in data:
        provider.is_enabled = bool(data["is_enabled"])

    raw_key = data.get("api_key")
    if raw_key and isinstance(raw_key, str) and raw_key.strip():
        provider.api_key_encrypted = encrypt_key(raw_key.strip())

    await db.commit()
    await db.refresh(provider)
    return provider


async def delete_provider(db: AsyncSession, provider_id: int) -> bool:
    """Deletes an AI provider."""
    provider = await db.get(AiProvider, provider_id)
    if not provider:
        return False
    was_default = provider.is_default
    await db.delete(provider)
    await db.commit()

    if was_default:
        # Pick another provider as default
        stmt = select(AiProvider).order_by(AiProvider.id.asc()).limit(1)
        next_p = (await db.execute(stmt)).scalar_one_or_none()
        if next_p:
            next_p.is_default = True
            await db.commit()
    return True


async def set_default_provider(db: AsyncSession, provider_id: int) -> bool:
    """Marks a provider as active default."""
    target = await db.get(AiProvider, provider_id)
    if not target:
        return False
    await db.execute(update(AiProvider).values(is_default=False))
    target.is_default = True
    target.is_enabled = True
    await db.commit()
    return True


async def test_provider(db: AsyncSession, provider_id: int) -> Dict[str, any]:
    """Runs a live test against a stored provider and records result."""
    provider = await db.get(AiProvider, provider_id)
    if not provider:
        return {"success": False, "error": "Provider not found."}

    api_key = decrypt_key(provider.api_key_encrypted)
    result = await engine.test_api_connection(
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        api_key=api_key,
        model_name=provider.model_name,
    )

    provider.last_tested_status = "ok" if result.get("success") else "failed"
    provider.last_latency_ms = result.get("latency_ms")
    await db.commit()
    return result


# -------------------------------------------------------------
# Connection & Model Discovery
# -------------------------------------------------------------

async def test_connection(
    db: AsyncSession,
    override_data: Optional[Dict[str, any]] = None,
) -> Dict[str, any]:
    """Tests connection to the configured or submitted AI provider."""
    provider_type = override_data.get("provider_type") if override_data else None
    base_url = override_data.get("base_url") if override_data else None
    model_name = override_data.get("model_name") if override_data else None
    override_key = override_data.get("api_key") if override_data else None

    if not provider_type or not base_url or not model_name:
        active = await get_active_provider(db)
        if active:
            provider_type = provider_type or active.provider_type
            base_url = base_url or active.base_url
            model_name = model_name or active.model_name
            if not override_key:
                api_key = decrypt_key(active.api_key_encrypted)
            else:
                api_key = override_key.strip()
        else:
            api_key = override_key.strip() if override_key else ""
    else:
        api_key = override_key.strip() if override_key else ""

    if not api_key:
        return {
            "success": False,
            "latency_ms": 0,
            "sample_response": None,
            "error": "No API Key provided. Please enter an API key.",
            "status_code": 400,
        }

    return await engine.test_api_connection(
        provider_type=provider_type,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
    )


async def fetch_models(
    db: AsyncSession,
    override_data: Optional[Dict[str, any]] = None,
) -> Dict[str, any]:
    """Fetches available model IDs from provider /models endpoint."""
    provider_type = (override_data.get("provider_type") if override_data else None) or "openai_compatible"
    base_url = (override_data.get("base_url") if override_data else None) or "https://api.openai.com/v1"
    override_key = override_data.get("api_key") if override_data else None

    if override_key and isinstance(override_key, str) and override_key.strip():
        api_key = override_key.strip()
    else:
        active = await get_active_provider(db)
        api_key = decrypt_key(active.api_key_encrypted) if active else ""

    if not api_key:
        return {
            "success": False,
            "models": [],
            "error": "No API Key provided. Please enter an API key to fetch models.",
        }

    try:
        models = await engine.fetch_available_models(
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
        )
        return {
            "success": True,
            "models": models,
            "count": len(models),
            "error": None,
        }
    except engine.AIProviderError as e:
        return {
            "success": False,
            "models": [],
            "error": e.message,
        }
    except Exception as e:
        return {
            "success": False,
            "models": [],
            "error": str(e),
        }


# -------------------------------------------------------------
# Streaming Chat Pipeline
# -------------------------------------------------------------

async def stream_ai_chat(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    context_key: Optional[str] = None,
    context_text: Optional[str] = None,
    provider_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    Main multi-turn streaming chat pipeline:
    1. Loads specified or active provider & decrypted API key.
    2. Builds system prompt with fixed rules + active context + custom rules.
    3. Loads previous conversation history for this session.
    4. Streams AI chunks via engine and persists the conversation.
    """
    active = None
    if provider_id:
        active = await get_provider(db, provider_id)
    if not active:
        active = await get_active_provider(db)

    if not active:
        yield "Error: No AI provider configured. Please add an AI provider in AI Assistant settings."
        return

    if not active.is_enabled:
        yield "Error: Active AI provider is currently disabled."
        return

    api_key = decrypt_key(active.api_key_encrypted)
    if not api_key:
        yield "Error: No API key configured for the active provider. Please configure an API key."
        return

    # Trim context log to prevent exceeding context window
    trimmed_context = engine.trim_context_log(context_text or "")
    system_prompt = prompts.build_system_prompt(
        context=trimmed_context,
        custom_rules=active.custom_rules,
    )

    # Fetch recent conversation history (last 10 messages)
    stmt = (
        select(AiChatMessage)
        .where(AiChatMessage.session_id == session_id)
        .order_by(AiChatMessage.id.desc())
        .limit(10)
    )
    result = await db.execute(stmt)
    recent_records = list(reversed(result.scalars().all()))

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt}
    ]

    for record in recent_records:
        messages.append({"role": record.role, "content": record.content})

    messages.append({"role": "user", "content": user_message})

    # Save user message to database
    user_record = AiChatMessage(
        session_id=session_id,
        role="user",
        content=user_message,
        context_key=context_key,
    )
    db.add(user_record)
    await db.commit()

    # Stream response
    full_response = []
    try:
        async for chunk in engine.stream_chat(
            provider_type=active.provider_type,
            base_url=active.base_url,
            api_key=api_key,
            model_name=active.model_name,
            messages=messages,
            temperature=active.temperature,
            max_tokens=active.max_tokens,
        ):
            full_response.append(chunk)
            yield chunk
    except engine.AIProviderError as exc:
        err_msg = f"\n\n[Error from AI Provider: {exc.message}]"
        full_response.append(err_msg)
        yield err_msg
    except Exception as exc:
        err_msg = f"\n\n[Error: {str(exc)}]"
        full_response.append(err_msg)
        yield err_msg

    # Save assistant response to database
    complete_text = "".join(full_response).strip()
    if complete_text:
        assistant_record = AiChatMessage(
            session_id=session_id,
            role="assistant",
            content=complete_text,
            context_key=context_key,
        )
        db.add(assistant_record)
        await db.commit()


async def clear_session(db: AsyncSession, session_id: str) -> bool:
    """Deletes all stored chat messages for a session."""
    await db.execute(delete(AiChatMessage).where(AiChatMessage.session_id == session_id))
    await db.commit()
    return True


async def get_session_messages(db: AsyncSession, session_id: str) -> List[Dict[str, any]]:
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
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]
