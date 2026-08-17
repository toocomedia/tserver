"""
service.py — AI Helper service layer: key encryption, settings management, and multi-turn chat pipeline.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import AsyncGenerator, Dict, List, Optional

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.ai_helper import AiChatMessage, AiHelperSettings
from plugins.ai_helper import engine, prompts

logger = logging.getLogger(__name__)

PROVIDER_PRESETS: Dict[str, Dict[str, any]] = {
    "openai": {
        "name": "OpenAI",
        "type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "o3-mini", "gpt-4-turbo"],
        "desc": "Official OpenAI API (GPT-4o, GPT-4o-mini, o3-mini)",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-3-5-sonnet-20241022",
        "models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
        "desc": "Anthropic Claude 3.5 Sonnet & Haiku (/v1/messages)",
    },
    "deepseek": {
        "name": "DeepSeek",
        "type": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "desc": "DeepSeek V3 & R1 (Cost-effective & powerful)",
    },
    "openrouter": {
        "name": "OpenRouter",
        "type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "models": [
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
            "deepseek/deepseek-chat",
            "meta-llama/llama-3.3-70b-instruct",
            "google/gemini-2.0-flash-exp:free"
        ],
        "desc": "Unified multi-model API router",
    },
    "groq": {
        "name": "Groq",
        "type": "openai_compatible",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "deepseek-r1-distill-llama-70b"
        ],
        "desc": "Ultra-low latency LPU inference",
    },
    "gemini": {
        "name": "Google Gemini",
        "type": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
        "models": ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
        "desc": "Google Gemini via OpenAI-compatible endpoint",
    },
    "mistral": {
        "name": "Mistral AI",
        "type": "openai_compatible",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "models": ["mistral-large-latest", "codestral-latest", "mistral-small-latest"],
        "desc": "Mistral Large & Codestral coding models",
    },
    "together": {
        "name": "Together AI",
        "type": "openai_compatible",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "models": [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "deepseek-ai/DeepSeek-V3",
            "mistralai/Mixtral-8x7B-Instruct-v0.1"
        ],
        "desc": "Open-source models cloud engine",
    },
    "custom": {
        "name": "Custom Endpoint",
        "type": "openai_compatible",
        "base_url": "",
        "default_model": "",
        "models": [],
        "desc": "Any custom proxy or local/remote endpoint",
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


async def get_settings(db: AsyncSession) -> AiHelperSettings:
    """Retrieves or creates the singleton settings record."""
    settings = await db.get(AiHelperSettings, 1)
    if not settings:
        settings = AiHelperSettings(
            id=1,
            is_enabled=True,
            provider_type="openai_compatible",
            base_url="https://api.openai.com/v1",
            model_name="gpt-4o-mini",
            temperature=0.2,
            max_tokens=4096,
            custom_rules="",
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def save_settings(db: AsyncSession, data: Dict[str, any]) -> AiHelperSettings:
    """Updates AI Helper settings."""
    settings = await get_settings(db)

    if "is_enabled" in data:
        settings.is_enabled = bool(data["is_enabled"])
    if "provider_type" in data:
        settings.provider_type = str(data["provider_type"]).strip() or "openai_compatible"
    if "base_url" in data:
        settings.base_url = str(data["base_url"]).strip()
    if "model_name" in data:
        settings.model_name = str(data["model_name"]).strip() or "gpt-4o-mini"
    if "temperature" in data:
        try:
            settings.temperature = max(0.0, min(2.0, float(data["temperature"])))
        except (ValueError, TypeError):
            pass
    if "max_tokens" in data:
        try:
            settings.max_tokens = max(128, min(32768, int(data["max_tokens"])))
        except (ValueError, TypeError):
            pass
    if "custom_rules" in data:
        settings.custom_rules = str(data["custom_rules"]).strip()

    # Only update API key if a non-empty new key is provided
    raw_api_key = data.get("api_key")
    if raw_api_key and isinstance(raw_api_key, str) and raw_api_key.strip():
        settings.api_key_encrypted = encrypt_key(raw_api_key)

    await db.commit()
    await db.refresh(settings)
    return settings


async def test_connection(
    db: AsyncSession,
    override_data: Optional[Dict[str, any]] = None,
) -> Dict[str, any]:
    """Tests connection to the configured or submitted AI provider."""
    settings = await get_settings(db)

    provider_type = (override_data.get("provider_type") if override_data else None) or settings.provider_type
    base_url = (override_data.get("base_url") if override_data else None) or settings.base_url
    model_name = (override_data.get("model_name") if override_data else None) or settings.model_name

    override_key = override_data.get("api_key") if override_data else None
    if override_key and isinstance(override_key, str) and override_key.strip():
        api_key = override_key.strip()
    else:
        api_key = decrypt_key(settings.api_key_encrypted)

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
    settings = await get_settings(db)
    provider_type = (override_data.get("provider_type") if override_data else None) or settings.provider_type
    base_url = (override_data.get("base_url") if override_data else None) or settings.base_url

    override_key = override_data.get("api_key") if override_data else None
    if override_key and isinstance(override_key, str) and override_key.strip():
        api_key = override_key.strip()
    else:
        api_key = decrypt_key(settings.api_key_encrypted)

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


async def stream_ai_chat(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    context_key: Optional[str] = None,
    context_text: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Main multi-turn streaming chat pipeline:
    1. Loads active configuration & decrypted API key.
    2. Builds system prompt with fixed rules + active context + custom rules.
    3. Loads previous conversation history for this session.
    4. Streams AI chunks via engine and persists the conversation.
    """
    settings = await get_settings(db)
    if not settings.is_enabled:
        yield "Error: AI Assistant is currently disabled in settings."
        return

    api_key = decrypt_key(settings.api_key_encrypted)
    if not api_key:
        yield "Error: No AI API key is configured. Please configure an API key in the AI Assistant settings."
        return

    # Trim context log to prevent exceeding context window
    trimmed_context = engine.trim_context_log(context_text or "")
    system_prompt = prompts.build_system_prompt(
        context=trimmed_context,
        custom_rules=settings.custom_rules,
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
            provider_type=settings.provider_type,
            base_url=settings.base_url,
            api_key=api_key,
            model_name=settings.model_name,
            messages=messages,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
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
