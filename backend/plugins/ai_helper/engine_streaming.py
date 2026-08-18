"""
engine_streaming.py — Native async LLM streaming with reasoning/thinking delta capture.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List

import httpx

from plugins.ai_helper.engine import (
    AIProviderError,
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
    _normalize_anthropic_url,
    _normalize_openai_url,
)

logger = logging.getLogger(__name__)


async def _stream_openai_compatible(
    endpoint: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)) as client:
        try:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_bytes = await response.aread()
                    error_str = error_bytes.decode(errors="replace")
                    try:
                        error_json = json.loads(error_str)
                        err_msg = error_json.get("error", {}).get("message") or error_str
                    except Exception:
                        err_msg = error_str

                    if response.status_code == 401:
                        raise AIProviderError("Invalid API key or unauthorized.", 401, "invalid_api_key")
                    elif response.status_code == 429:
                        raise AIProviderError("Rate limit reached or insufficient credits.", 429, "rate_limit")
                    else:
                        raise AIProviderError(f"Provider error ({response.status_code}): {err_msg}", response.status_code)

                in_think_block = False
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            # Capture reasoning/thinking deltas (DeepSeek R1, Groq, Ollama, OpenRouter)
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
                            if reasoning:
                                if not in_think_block:
                                    in_think_block = True
                                    yield "<think>"
                                yield reasoning

                            content = delta.get("content")
                            if content:
                                if in_think_block:
                                    in_think_block = False
                                    yield "</think>\n"
                                yield content
                    except json.JSONDecodeError:
                        continue

                if in_think_block:
                    yield "</think>\n"
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to the AI provider endpoint. Please check the Base URL.", 502, "connect_error")
        except httpx.TimeoutException:
            raise AIProviderError("Request to AI provider timed out.", 504, "timeout")


async def _stream_anthropic(
    endpoint: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
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

    payload = {
        "model": model_name,
        "messages": chat_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if system_prompt.strip():
        payload["system"] = system_prompt.strip()

    async with httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)) as client:
        try:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    error_bytes = await response.aread()
                    error_str = error_bytes.decode(errors="replace")
                    try:
                        error_json = json.loads(error_str)
                        err_msg = error_json.get("error", {}).get("message") or error_str
                    except Exception:
                        err_msg = error_str

                    if response.status_code == 401:
                        raise AIProviderError("Invalid Anthropic API key.", 401, "invalid_api_key")
                    elif response.status_code == 429:
                        raise AIProviderError("Anthropic rate limit reached or insufficient credits.", 429, "rate_limit")
                    else:
                        raise AIProviderError(f"Anthropic error ({response.status_code}): {err_msg}", response.status_code)

                in_think_block = False
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    try:
                        chunk = json.loads(data_str)
                        event_type = chunk.get("type")
                        if event_type == "content_block_delta":
                            delta = chunk.get("delta") or {}
                            delta_type = delta.get("type")
                            if delta_type == "thinking_delta":
                                think_text = delta.get("thinking")
                                if think_text:
                                    if not in_think_block:
                                        in_think_block = True
                                        yield "<think>"
                                    yield think_text
                            elif delta_type == "text_delta":
                                if in_think_block:
                                    in_think_block = False
                                    yield "</think>\n"
                                text = delta.get("text")
                                if text:
                                    yield text
                        elif event_type in ("message_stop", "content_block_stop"):
                            if in_think_block and event_type == "message_stop":
                                in_think_block = False
                                yield "</think>\n"
                            if event_type == "message_stop":
                                break
                    except json.JSONDecodeError:
                        continue

                if in_think_block:
                    yield "</think>\n"
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to the Anthropic endpoint. Please check the Base URL.", 502, "connect_error")
        except httpx.TimeoutException:
            raise AIProviderError("Request to Anthropic API timed out.", 504, "timeout")


async def stream_chat(
    provider_type: str,
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> AsyncGenerator[str, None]:
    """Universal streaming chat generator supporting retry on transient errors."""
    attempts = 0
    max_attempts = 2

    while attempts < max_attempts:
        attempts += 1
        try:
            if provider_type == "anthropic":
                endpoint = _normalize_anthropic_url(base_url)
                async for chunk in _stream_anthropic(endpoint, api_key, model_name, messages, temperature, max_tokens):
                    yield chunk
            else:
                endpoint = _normalize_openai_url(base_url)
                async for chunk in _stream_openai_compatible(endpoint, api_key, model_name, messages, temperature, max_tokens):
                    yield chunk
            return
        except AIProviderError as e:
            if attempts < max_attempts and e.status_code in (429, 502, 503, 504):
                logger.warning("Transient AI error (%s). Retrying in 1s...", e.message)
                await asyncio.sleep(1.0)
                continue
            raise e
        except Exception as e:
            logger.error("Unexpected error in stream_chat: %s", e)
            raise AIProviderError(f"Unexpected error communicating with AI: {str(e)}", 500)
