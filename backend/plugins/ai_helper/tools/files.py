"""
tools/files.py — Confined, sandboxed Read-Only file manager tool handlers.
Supports flexible target resolution by domain, kind:id, or resource ID.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.file_manager import file_operations, file_targets

logger = logging.getLogger(__name__)


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


async def _resolve_file_target(db: AsyncSession, target_id: str, target_type: str = "") -> file_targets.FileTarget:
    """Resolves target_id string flexibly into a FileTarget."""
    clean_id = (target_id or "").strip()
    try:
        return file_targets.parse_target(clean_id)
    except Exception:
        pass

    targets = await file_targets.list_targets(db)
    clean_lower = clean_id.lower()

    # 1. Match by domain name or preset
    for t in targets:
        tid = t.get("id") or ""
        dom = (t.get("domain") or "").lower()
        preset = (t.get("preset") or "").lower()
        ttype = (t.get("target_type") or "").lower()

        if clean_lower and clean_lower in (dom, preset, tid.lower()):
            return file_targets.parse_target(tid)
        if target_type and target_type.lower() in (ttype, dom):
            if str(t.get("resource_id", "")) == clean_id or tid.endswith(f":{clean_id}"):
                return file_targets.parse_target(tid)
        if tid.endswith(f":{clean_id}"):
            return file_targets.parse_target(tid)

    # 2. Fallback to resource ID match
    for t in targets:
        if str(t.get("resource_id", "")) == clean_id:
            return file_targets.parse_target(t.get("id"))

    if targets:
        # If single target or exact partial match
        for t in targets:
            if clean_lower in (t.get("domain") or "").lower():
                return file_targets.parse_target(t.get("id"))

    raise ValueError(f"Managed application target '{clean_id}' not found.")


async def list_website_directory(
    db: AsyncSession,
    target_id: str = "",
    relative_path: str = "",
    target_type: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Lists files and folders inside a website/application root directory."""
    try:
        effective_id = target_id or kwargs.get("target") or ""
        target = await _resolve_file_target(db, effective_id, target_type)
        context = await file_targets.resolve_context(db, target, "application")
        result = file_operations.list_entries(context, relative_path or "")
        return {
            "status": "ok",
            "target_id": target.id,
            "domain": target.domain,
            "path": relative_path,
            "entries": result.get("entries", []),
            "has_more": result.get("has_more", False),
        }
    except Exception as exc:
        targets = await file_targets.list_targets(db)
        available = [t.get("id") for t in targets]
        return {
            "status": "error",
            "message": f"Could not list directory: {str(exc)}",
            "available_target_ids": available,
        }


async def read_website_file(
    db: AsyncSession,
    target_id: str = "",
    file_path: str = "",
    target_type: str = "",
    max_chars: int = 8000,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Reads a file in read-only mode from a verified website root directory."""
    try:
        effective_id = target_id or kwargs.get("target") or ""
        target = await _resolve_file_target(db, effective_id, target_type)
        context = await file_targets.resolve_context(db, target, "application")
        res = file_operations.read_text(context, file_path)
        content = res.get("content", "")

        if file_path.endswith(".env") or "secret" in file_path.lower():
            content = _mask_env_secrets(content)

        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... [Truncated {len(content) - max_chars} characters]"

        return {
            "status": "ok",
            "target_id": target.id,
            "domain": target.domain,
            "file_path": file_path,
            "size": res.get("size", len(content)),
            "content": content,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Could not read file: {str(exc)}",
        }
