"""
tools/docker_hub.py — Docker Hub registry search tool for AI Assistant.
Queries public Docker Hub API to find official and community container images.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

DOCKER_HUB_SEARCH_URL = "https://hub.docker.com/v2/search/repositories/"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESULTS_LIMIT = 15


async def search_docker_hub(
    query: str,
    max_results: int = 5,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Searches Docker Hub registry for images matching the query.
    Returns official status, description, star count, and pull statistics.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        return {"status": "error", "message": "Search query cannot be empty."}

    bounded_limit = max(1, min(int(max_results or 5), MAX_RESULTS_LIMIT))
    params = {
        "query": clean_query,
        "page_size": str(bounded_limit),
    }

    headers = {
        "User-Agent": "Docker-Client/24.0.0 (Linux)",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            resp = await client.get(DOCKER_HUB_SEARCH_URL, params=params, headers=headers)
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "status_code": resp.status_code,
                    "message": f"Docker Hub API returned HTTP {resp.status_code}.",
                }

            data = resp.json()
            raw_results: List[Dict[str, Any]] = data.get("results", [])

            formatted: List[Dict[str, Any]] = []
            for item in raw_results[:bounded_limit]:
                repo_name = item.get("repo_name") or item.get("name") or ""
                desc = (item.get("short_description") or item.get("description") or "").strip()
                stars = item.get("star_count", 0)
                pulls = item.get("pull_count", 0)
                is_official = bool(item.get("is_official", False))

                formatted.append({
                    "image": repo_name,
                    "description": desc,
                    "is_official": is_official,
                    "stars": stars,
                    "pulls": pulls,
                })

            return {
                "status": "ok",
                "query": clean_query,
                "total_count": data.get("count", len(formatted)),
                "results": formatted,
            }
    except httpx.TimeoutException:
        return {"status": "error", "message": "Docker Hub search request timed out (10s limit)."}
    except Exception as exc:
        logger.warning("Error querying Docker Hub search for '%s': %s", clean_query, exc)
        return {"status": "error", "message": f"Docker Hub search failed: {str(exc)}"}
