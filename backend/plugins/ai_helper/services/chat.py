"""
services/chat.py — Multi-turn streaming chat pipeline with tool calling, context trimming, and session auto-tracking.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ai_helper import AiChatMessage
from plugins.ai_helper import engine, permissions, prompts, tools
from plugins.ai_helper.services.providers import decrypt_key, get_active_provider, get_provider
from plugins.ai_helper.services.sessions import generate_title_from_prompt, get_or_create_session

logger = logging.getLogger(__name__)


def _sanitize_history_content(text: str) -> str:
    """Removes unfulfilled XML pseudo tool calls from message history."""
    if not text:
        return ""
    clean = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", text)
    clean = re.sub(r"<function=[a-zA-Z0-9_]+>[\s\S]*?</function>", "", clean)
    clean = re.sub(r"<parameter=[a-zA-Z0-9_]+>[\s\S]*?</parameter>", "", clean)
    return clean.strip()


async def stream_ai_chat(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    context_key: Optional[str] = None,
    context_text: Optional[str] = None,
    provider_id: Optional[int] = None,
    model_name: Optional[str] = None,
    task_type: Optional[str] = "general",
    session_title: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Multi-turn streaming chat pipeline with tool calling support:
    1. Loads specified or active provider & decrypted API key.
    2. Builds modular system prompt (base + tools + action tags + context + custom rules).
    3. Loads previous conversation history strictly for this session and sanitizes history.
    4. Evaluates tool calling in a multi-turn loop if permissions permit.
    5. Executes requested panel tools and feeds results back to AI.
    6. Streams AI response chunks and persists the conversation.
    7. Updates or auto-titles the AiChatSession record.
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

    effective_model = model_name.strip() if model_name and model_name.strip() else active.model_name

    # Check permission policy
    policy = await permissions.get_or_create_policy(db)
    tools_enabled = (policy.global_mode != "disabled")

    trimmed_context = engine.trim_context_log(context_text or "")
    system_prompt = prompts.build_system_prompt(
        context=trimmed_context,
        custom_rules=active.custom_rules,
        include_tools_rules=tools_enabled,
    )

    # Manage or create AiChatSession
    session_record = await get_or_create_session(
        db=db,
        session_id=session_id,
        title=session_title,
        task_type=task_type or "general",
        context_key=context_key,
        provider_id=active.id,
        model_name=effective_model,
    )

    # Auto-generate title on first message if default
    if session_record.title in ("New Chat", "", None):
        if session_title and session_title.strip():
            session_record.title = session_title.strip()
        else:
            session_record.title = generate_title_from_prompt(user_message)

    # Fetch recent conversation history strictly for this session (last 10 messages)
    stmt = (
        select(AiChatMessage)
        .where(AiChatMessage.session_id == session_id)
        .order_by(AiChatMessage.id.desc())
        .limit(10)
    )
    result = await db.execute(stmt)
    recent_records = list(reversed(result.scalars().all()))

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    for record in recent_records:
        clean_history = _sanitize_history_content(record.content)
        if clean_history:
            messages.append({"role": record.role, "content": clean_history})

    messages.append({"role": "user", "content": user_message})

    # Save user message to database
    user_record = AiChatMessage(
        session_id=session_id,
        role="user",
        content=user_message,
        context_key=context_key,
    )
    db.add(user_record)
    session_record.message_count += 1
    session_record.updated_at = datetime.now()
    await db.commit()

    # Tool calling loop if enabled
    if tools_enabled:
        tool_defs = tools.get_tool_definitions(active.provider_type)
        max_tool_iterations = 3

        for _ in range(max_tool_iterations):
            try:
                tool_step = await engine.chat_completion_step(
                    provider_type=active.provider_type,
                    base_url=active.base_url,
                    api_key=api_key,
                    model_name=effective_model,
                    messages=messages,
                    tools=tool_defs,
                    temperature=active.temperature,
                    max_tokens=active.max_tokens,
                )
                tool_calls = tool_step.get("tool_calls") or []

                if not tool_calls:
                    # Check for XML pseudo tool call syntax emitted in text by some models
                    step_content = tool_step.get("content") or ""
                    if "<function=" in step_content or "<tool_call>" in step_content:
                        for m in re.finditer(r"<function=([a-zA-Z0-9_]+)>(.*?)</function>", step_content, re.DOTALL):
                            fn_name = m.group(1)
                            body = m.group(2)
                            params = {}
                            for pm in re.finditer(r"<parameter=([a-zA-Z0-9_]+)>(.*?)</parameter>", body, re.DOTALL):
                                params[pm.group(1).strip()] = pm.group(2).strip()
                            tool_calls.append({"id": f"call_{len(tool_calls)}", "name": fn_name, "arguments": params})

                if not tool_calls:
                    break

                raw_msg = tool_step.get("raw_message")
                if raw_msg:
                    messages.append(raw_msg)
                else:
                    messages.append({"role": "assistant", "content": tool_step.get("content") or ""})

                for tc in tool_calls:
                    fn_name = tc.get("name")
                    fn_args = tc.get("arguments") or {}
                    tc_id = tc.get("id") or f"call_{len(messages)}"

                    tool_output = await tools.execute_tool(
                        db=db,
                        tool_name=fn_name,
                        arguments=fn_args,
                        session_id=session_id,
                    )

                    tool_json_str = json.dumps(tool_output)
                    if active.provider_type == "anthropic":
                        messages.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tc_id,
                                    "content": tool_json_str,
                                }
                            ],
                        })
                    else:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": tool_json_str,
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[SYSTEM TOOL RESULT for {fn_name}]:\n{tool_json_str}\n\nPresent the results directly and clearly in clean Markdown now without repeating or outputting tool calls.",
                        })
            except Exception as exc:
                logger.warning("Tool calling step failed or skipped: %s. Continuing standard stream.", exc)
                break

    # Stream final assistant response
    full_response = []
    try:
        async for chunk in engine.stream_chat(
            provider_type=active.provider_type,
            base_url=active.base_url,
            api_key=api_key,
            model_name=effective_model,
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
        # Sanitize any raw tool call tags before persisting
        persisted_text = _sanitize_history_content(complete_text) or complete_text
        assistant_record = AiChatMessage(
            session_id=session_id,
            role="assistant",
            content=persisted_text,
            context_key=context_key,
        )
        db.add(assistant_record)
        session_record.message_count += 1
        session_record.updated_at = datetime.now()
        await db.commit()
