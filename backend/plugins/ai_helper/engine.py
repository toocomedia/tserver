"""
engine.py — Native zero-dependency async LLM streaming engine using httpx.
Supports all OpenAI-compatible endpoints and Anthropic Claude.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Request timeouts
CONNECT_TIMEOUT = 20.0
READ_TIMEOUT = 90.0


def trim_context_log(text: str, max_lines: int = 200, max_chars: int = 15000) -> str:
    """Trims large build logs or outputs to the most relevant recent lines."""
    if not text:
        return ""
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    trimmed = "\n".join(lines)
    if len(trimmed) > max_chars:
        trimmed = trimmed[-max_chars:]
    return trimmed


class AIProviderError(Exception):
    def __init__(self, message: str, status_code: int = 500, error_code: str = "ai_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


def _normalize_openai_url(base_url: str) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return "https://api.openai.com/v1/chat/completions"
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def _normalize_anthropic_url(base_url: str) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return "https://api.anthropic.com/v1/messages"
    if cleaned.endswith("/messages"):
        return cleaned
    return f"{cleaned}/messages"


async def _stream_openai_compatible(
    endpoint: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, str]],
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
                            content = delta.get("content")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to the AI provider endpoint. Please check the Base URL.", 502, "connect_error")
        except httpx.TimeoutException:
            raise AIProviderError("Request to AI provider timed out.", 504, "timeout")


async def _stream_anthropic(
    endpoint: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, str]],
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
                            if delta.get("type") == "text_delta":
                                text = delta.get("text")
                                if text:
                                    yield text
                        elif event_type == "message_stop":
                            break
                    except json.JSONDecodeError:
                        continue
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to the Anthropic endpoint. Please check the Base URL.", 502, "connect_error")
        except httpx.TimeoutException:
            raise AIProviderError("Request to Anthropic API timed out.", 504, "timeout")


async def stream_chat(
    provider_type: str,
    base_url: str,
    api_key: str,
    model_name: str,
    messages: List[Dict[str, str]],
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


async def test_api_connection(
    provider_type: str,
    base_url: str,
    api_key: str,
    model_name: str,
) -> Dict[str, any]:
    """Sends a minimal 1-token test prompt to verify credentials and measure latency."""
    import time
    start_time = time.perf_counter()
    test_messages = [
        {"role": "user", "content": "Respond with the single word 'OK'."}
    ]

    try:
        response_text = ""
        async for chunk in stream_chat(
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            messages=test_messages,
            temperature=0.0,
            max_tokens=10,
        ):
            response_text += chunk
            if len(response_text) >= 2:
                break

        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "success": True,
            "latency_ms": latency_ms,
            "sample_response": response_text.strip(),
            "error": None,
        }
    except AIProviderError as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "success": False,
            "latency_ms": latency_ms,
            "sample_response": None,
            "error": e.message,
            "status_code": e.status_code,
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return {
            "success": False,
            "latency_ms": latency_ms,
            "sample_response": None,
            "error": str(e),
            "status_code": 500,
        }
