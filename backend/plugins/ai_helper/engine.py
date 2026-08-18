"""
engine.py — Native zero-dependency async LLM engine facade.
Coordinates streaming, tool-calling completion steps, and model discovery.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

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


# Re-export completion and streaming functions
from plugins.ai_helper.engine_completion import chat_completion_step  # noqa: E402
from plugins.ai_helper.engine_streaming import stream_chat  # noqa: E402


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
