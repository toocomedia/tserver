"""
engine.py — Native zero-dependency async LLM streaming and tool-calling engine using httpx.
Supports all OpenAI-compatible endpoints and Anthropic Claude.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

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


# -------------------------------------------------------------------
# Non-Streaming Tool Calling Step
# -------------------------------------------------------------------

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
    Returns: {
        "content": str | None,
        "tool_calls": [ {"id": "...", "name": "...", "arguments": {...}} ]
    }
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
            for tc in raw_tool_calls:
                func_data = tc.get("function") or {}
                fn_name = func_data.get("name")
                fn_args_raw = func_data.get("arguments") or "{}"
                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                except Exception:
                    fn_args = {}
                parsed_tools.append({
                    "id": tc.get("id") or f"call_{len(parsed_tools)}",
                    "name": fn_name,
                    "arguments": fn_args,
                })

            return {
                "content": content,
                "tool_calls": parsed_tools,
                "raw_message": choice_msg,
            }
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to AI provider endpoint.", 502, "connect_error")
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


# -------------------------------------------------------------------
# Streaming Generator
# -------------------------------------------------------------------

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
                            # Check for reasoning / thought deltas (DeepSeek R1, Ollama, OpenRouter, Groq)
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


async def test_api_connection(
    provider_type: str,
    base_url: str,
    api_key: str,
    model_name: str,
) -> Dict[str, Any]:
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


def _normalize_models_url(base_url: str, provider_type: str) -> str:
    cleaned = (base_url or "").strip().rstrip("/")
    if provider_type == "anthropic":
        if not cleaned:
            return "https://api.anthropic.com/v1/models"
        if cleaned.endswith("/messages"):
            return cleaned[:-9] + "/models"
        if not cleaned.endswith("/models"):
            return f"{cleaned}/models"
        return cleaned

    if not cleaned:
        return "https://api.openai.com/v1/models"
    if cleaned.endswith("/chat/completions"):
        return cleaned[:-17] + "/models"
    if not cleaned.endswith("/models"):
        return f"{cleaned}/models"
    return cleaned


async def fetch_available_models(
    provider_type: str,
    base_url: str,
    api_key: str,
) -> List[str]:
    """Fetches the list of model IDs dynamically from the provider's /models endpoint."""
    if not api_key:
        raise AIProviderError("API key is required to fetch models.", 400)

    endpoint = _normalize_models_url(base_url, provider_type)

    if provider_type == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
        try:
            response = await client.get(endpoint, headers=headers)
            if response.status_code != 200:
                error_text = response.text
                if response.status_code == 401:
                    raise AIProviderError("Invalid API key or unauthorized.", 401)
                raise AIProviderError(f"Failed to fetch models (HTTP {response.status_code}): {error_text[:200]}", response.status_code)

            data = response.json()
            models_list = []

            if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                for item in data["data"]:
                    if isinstance(item, dict) and "id" in item:
                        models_list.append(str(item["id"]))
                    elif isinstance(item, str):
                        models_list.append(item)
            elif isinstance(data, dict) and "models" in data and isinstance(data["models"], list):
                for item in data["models"]:
                    if isinstance(item, dict) and "name" in item:
                        models_list.append(str(item["name"]).replace("models/", ""))
                    elif isinstance(item, dict) and "id" in item:
                        models_list.append(str(item["id"]))
                    elif isinstance(item, str):
                        models_list.append(item)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item:
                        models_list.append(str(item["id"]))
                    elif isinstance(item, str):
                        models_list.append(item)

            models_list = sorted(list(set(models_list)))
            return models_list
        except httpx.ConnectError:
            raise AIProviderError("Could not connect to the provider endpoint to fetch models. Check Base URL.", 502)
        except httpx.TimeoutException:
            raise AIProviderError("Timed out while fetching models from provider.", 504)
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError(f"Error parsing models response: {str(exc)}", 500)
