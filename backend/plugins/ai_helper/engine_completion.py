"""
engine_completion.py — Non-streaming completion & tool calling steps for LLMs.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from plugins.ai_helper.engine import (
    AIProviderError,
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
    _normalize_anthropic_url,
    _normalize_openai_url,
)

logger = logging.getLogger(__name__)


async def chat_completion_step(
    provider_type: str,
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """
    Executes a single non-streaming chat turn to evaluate if the model wants to call tools.
    """
    if provider_type == "anthropic":
        return await _anthropic_completion_step(
            base_url, api_key, model_name, messages, tools, temperature, max_tokens
        )
    return await _openai_completion_step(
        base_url, api_key, model_name, messages, tools, temperature, max_tokens
    )


async def _openai_completion_step(
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    endpoint = _normalize_openai_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)) as client:
        try:
            response = await client.post(endpoint, headers=headers, json=payload)
            if response.status_code != 200:
                error_str = response.text
                try:
                    error_json = response.json()
                    err_msg = error_json.get("error", {}).get("message") or error_str
                except Exception:
                    err_msg = error_str
                raise AIProviderError(f"Provider error ({response.status_code}): {err_msg}", response.status_code)

            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return {"content": "", "tool_calls": []}

            choice_msg = choices[0].get("message") or {}
            content = choice_msg.get("content") or ""
            raw_tool_calls = choice_msg.get("tool_calls") or []

            parsed_tools = []
            normalized_tool_calls = []
            for i, tc in enumerate(raw_tool_calls):
                func_data = tc.get("function") or {}
                fn_name = func_data.get("name") or tc.get("name") or "unknown"
                fn_args_raw = func_data.get("arguments") or tc.get("arguments") or "{}"
                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else (fn_args_raw or {})
                except Exception:
                    fn_args = {}

                tc_id = tc.get("id") or f"call_{i}_{fn_name}"
                parsed_tools.append({
                    "id": tc_id,
                    "name": fn_name,
                    "arguments": fn_args,
                })
                normalized_tool_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "arguments": json.dumps(fn_args) if isinstance(fn_args, dict) else str(fn_args_raw),
                    },
                })

            normalized_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": content if content else None,
            }
            if normalized_tool_calls:
                normalized_msg["tool_calls"] = normalized_tool_calls

            return {
                "content": content,
                "tool_calls": parsed_tools,
                "raw_message": normalized_msg,
            }
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to the AI provider endpoint.", 502, "connect_error")
        except httpx.TimeoutException:
            raise AIProviderError("Request to AI provider timed out.", 504, "timeout")


async def _anthropic_completion_step(
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    endpoint = _normalize_anthropic_url(base_url)
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    system_prompt = ""
    chat_messages = []
    for m in messages:
        if m.get("role") == "system":
            system_prompt += (m.get("content") or "") + "\n\n"
        else:
            chat_messages.append({"role": m.get("role"), "content": m.get("content")})

    payload: Dict[str, Any] = {
        "model": model_name,
        "messages": chat_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_prompt.strip():
        payload["system"] = system_prompt.strip()
    if tools:
        payload["tools"] = tools

    async with httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)) as client:
        try:
            response = await client.post(endpoint, headers=headers, json=payload)
            if response.status_code != 200:
                error_str = response.text
                try:
                    error_json = response.json()
                    err_msg = error_json.get("error", {}).get("message") or error_str
                except Exception:
                    err_msg = error_str
                raise AIProviderError(f"Anthropic error ({response.status_code}): {err_msg}", response.status_code)

            data = response.json()
            content_blocks = data.get("content") or []
            text_blocks = []
            parsed_tools = []

            for block in content_blocks:
                if block.get("type") == "text":
                    text_blocks.append(block.get("text") or "")
                elif block.get("type") == "tool_use":
                    parsed_tools.append({
                        "id": block.get("id") or f"tool_{len(parsed_tools)}",
                        "name": block.get("name"),
                        "arguments": block.get("input") or {},
                    })

            return {
                "content": "\n".join(text_blocks),
                "tool_calls": parsed_tools,
                "raw_message": {"role": "assistant", "content": content_blocks},
            }
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to Anthropic endpoint.", 502, "connect_error")
        except httpx.TimeoutException:
            raise AIProviderError("Request to Anthropic API timed out.", 504, "timeout")
