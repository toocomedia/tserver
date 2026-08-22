"""
tools/app_setup.py — Application source inspection and proposal generator for App Engine.
Generates immutable server-side AiActionPlan records for wizard autofill.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.services import action_plans
from services import container_app_image_inspect_service, container_app_inspection_service

logger = logging.getLogger(__name__)


async def inspect_app_source(
    db: AsyncSession,
    source_type: str = "image",
    repository_url: str = "",
    branch: str = "main",
    image_reference: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Runs native panel repository or registry image inspection.
    Detects runtime, build mode, internal ports, environment variables, and database needs.
    """
    stype = (source_type or "").lower().strip()
    if stype == "git":
        repo = repository_url.strip()
        if not repo:
            return {"status": "error", "message": "Repository URL is required for Git inspection."}
        try:
            res = container_app_inspection_service.inspect_repository(repo, branch.strip() or "main")
            return {"status": "ok", "source_type": "git", "inspection": res}
        except Exception as exc:
            return {"status": "error", "message": f"Git inspection failed: {str(exc)}"}

    elif stype == "image":
        image = image_reference.strip()
        if not image:
            return {"status": "error", "message": "Image reference is required for Docker inspection."}
        try:
            res = await container_app_image_inspect_service.inspect_image(image)
            return {"status": "ok", "source_type": "image", "inspection": res}
        except Exception as exc:
            return {"status": "error", "message": f"Docker image inspection failed: {str(exc)}"}

    return {"status": "error", "message": f"Unsupported source type '{source_type}'. Must be 'git' or 'image'."}


async def propose_app_install(
    db: AsyncSession,
    session_id: Optional[str] = None,
    source_type: str = "image",
    repository_url: str = "",
    branch: str = "main",
    image_reference: str = "",
    internal_port: int = 3000,
    build_mode: str = "railpack",
    environment_values: Optional[Dict[str, str]] = None,
    database_attachments: Optional[List[Dict[str, str]]] = None,
    storage_mounts: Optional[List[Dict[str, str]]] = None,
    domain_name: str = "",
    summary: str = "",
    confidence: float = 1.0,
    reasoning: str = "",
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Creates and saves a validated server-side AiActionPlan for application installation.
    Returns the opaque plan_id for UI action rendering.
    """
    try:
        port = int(internal_port)
        if port < 1 or port > 65535:
            port = 3000
    except (ValueError, TypeError):
        port = 3000

    clean_envs: Dict[str, str] = {}
    if isinstance(environment_values, dict):
        for k, v in environment_values.items():
            if isinstance(k, str) and k.strip():
                clean_envs[k.strip()] = str(v) if v is not None else ""

    clean_dbs: List[Dict[str, str]] = []
    if isinstance(database_attachments, list):
        for item in database_attachments:
            if isinstance(item, dict) and item.get("kind"):
                clean_dbs.append({
                    "kind": str(item.get("kind", "")).strip().lower(),
                    "provider": str(item.get("provider", "docker")).strip().lower(),
                    "environment_key": str(item.get("environment_key", "DATABASE_URL")).strip(),
                })

    clean_mounts: List[Dict[str, str]] = []
    if isinstance(storage_mounts, list):
        for item in storage_mounts:
            if isinstance(item, dict) and item.get("mount_path"):
                clean_mounts.append({
                    "label": str(item.get("label", "data")).strip().lower(),
                    "mount_path": str(item.get("mount_path", "")).strip(),
                })

    payload = {
        "source_type": (source_type or "image").strip().lower(),
        "repository_url": repository_url.strip(),
        "branch": branch.strip() or "main",
        "image_reference": image_reference.strip(),
        "internal_port": port,
        "build_mode": (build_mode or "railpack").strip().lower(),
        "environment_values": clean_envs,
        "database_attachments": clean_dbs,
        "storage_mounts": clean_mounts,
        "domain_name": domain_name.strip(),
    }

    sess_id = session_id or "default_session"
    plan_summary = summary or f"Install {image_reference or repository_url or 'Application'}"

    plan = await action_plans.create_action_plan(
        db=db,
        session_id=sess_id,
        action_type="app_install",
        payload=payload,
        summary=plan_summary,
        confidence=confidence,
        reasoning=reasoning,
        user_id=user_id,
    )

    return {
        "status": "ok",
        "plan_id": plan.plan_id,
        "summary": plan.summary,
        "confidence": plan.confidence,
        "action_tag": f"[ACTION:APP_PLAN:{plan.plan_id}]",
        "message": f"Action plan created successfully with ID {plan.plan_id}.",
    }
