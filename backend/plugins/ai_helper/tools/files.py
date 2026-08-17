"""
tools/files.py — Confined, sandboxed Read-Only file manager tool handlers.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.file_manager import file_operations, file_targets


def _mask_env_secrets(content: str) -> str:
    """Masks secret values in .env files."""
    lines = []
    secret_patterns = re.compile(r"(KEY|SECRET|PASS|TOKEN|AUTH|CREDENTIAL|PRIVATE)", re.IGNORECASE)
    for line in content.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if secret_patterns.search(k):
                lines.append(f"{k}=••••••••")
                continue
        lines.append(line)
    return "\n".join(lines)


async def list_website_directory(
    db: AsyncSession,
    target_id: str,
    relative_path: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Lists files and folders inside a website/application root directory."""
    try:
        target = file_targets.parse_target(target_id)
        context = await file_targets.resolve_context(db, target, "application")
        result = file_operations.list_entries(context, relative_path or "")
        return {
            "status": "ok",
            "target_id": target_id,
            "path": relative_path,
            "entries": result.get("entries", []),
            "has_more": result.get("has_more", False),
        }
    except Exception as exc:
        # Fallback to list available targets if target not found
        targets = await file_targets.list_targets(db)
        available = [t.get("id") for t in targets]
        return {
            "status": "error",
            "message": f"Could not list directory: {str(exc)}",
            "available_target_ids": available,
        }


async def read_website_file(
    db: AsyncSession,
    target_id: str,
    file_path: str,
    max_chars: int = 8000,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Reads a file in read-only mode from a verified website root directory."""
    try:
        target = file_targets.parse_target(target_id)
        context = await file_targets.resolve_context(db, target, "application")
        res = file_operations.read_text(context, file_path)
        content = res.get("content", "")

        # Mask secrets if .env or credentials
        if file_path.endswith(".env") or "secret" in file_path.lower():
            content = _mask_env_secrets(content)

        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... [Truncated {len(content) - max_chars} characters]"

        return {
            "status": "ok",
            "target_id": target_id,
            "file_path": file_path,
            "size": res.get("size", len(content)),
            "content": content,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Could not read file: {str(exc)}",
        }
