"""Safe AI proposal tool for evidence-backed Compose AppSpec plans."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.services import action_plans
from services.apps_engine.app_spec_codec import app_spec_to_dict
from services.apps_engine.security_policy import validate_app_spec

_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SECRET_NAME = re.compile(r"(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)")


async def propose_app_spec_plan(
    db: AsyncSession,
    app_spec: dict[str, Any],
    domain_name: str,
    evidence: list[str] | None = None,
    environment_values: dict[str, str] | None = None,
    summary: str = "",
    confidence: float = 1.0,
    reasoning: str = "",
    session_id: str | None = None,
    user_id: int | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Validate and persist an AiActionPlan only; never inspect, deploy, or create secrets."""
    if user_id is None:
        return {"status": "error", "message": "AI setup drafts require an authenticated panel user."}
    clean_domain = (domain_name or "").strip().lower()
    if not clean_domain and session_id:
        from models.ai_helper import AiChatSession
        stmt = select(AiChatSession.target_domain).where(AiChatSession.session_id == session_id)
        stored_domain = (await db.execute(stmt)).scalar_one_or_none()
        if stored_domain:
            clean_domain = stored_domain.strip().lower()
    if not clean_domain:
        return {"status": "error", "message": "AppSpec plan requires a target domain."}
    clean_evidence = [
        str(item).strip()[:1024]
        for item in (evidence or [])
        if isinstance(item, str) and item.strip()
    ][:12]
    if not clean_evidence:
        return {"status": "error", "message": "AppSpec plan requires source, registry, or official documentation evidence."}
    try:
        spec = validate_app_spec(app_spec)
        values = _environment_values(environment_values)
    except (TypeError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    serialized = app_spec_to_dict(spec)
    normalized = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
    payload = {
        "deploy_type": "app_spec",
        "domain_name": clean_domain,
        "app_spec": serialized,
        "app_spec_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "environment_values": values,
        "evidence": clean_evidence,
    }
    plan = await action_plans.create_action_plan(
        db=db,
        session_id=session_id or "default_session",
        action_type="app_spec_install",
        payload=payload,
        summary=summary or f"Deploy {spec.display_name}",
        confidence=confidence,
        reasoning=reasoning,
        user_id=user_id,
    )
    return {
        "status": "ok",
        "plan_id": plan.plan_id,
        "summary": plan.summary,
        "confidence": plan.confidence,
        "message": "Reviewed AppSpec plan created. No app, snapshot, or secret was created.",
    }


def _environment_values(raw: dict[str, str] | None) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("AppSpec environment values must be an object.")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _ENV.fullmatch(key) or _SECRET_NAME.search(key):
            raise ValueError("AppSpec environment values cannot contain secret or invalid names.")
        if not isinstance(value, str) or len(value) > 4096 or "\n" in value or "\r" in value:
            raise ValueError("AppSpec environment values must be single-line strings.")
        result[key] = value
    return result


def args_from_stack_inspection(stack_args: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert source-inspection manifest into canonical AppSpec tool input."""
    if not isinstance(stack_args, dict) or not isinstance(stack_args.get("stack_manifest"), dict):
        return None
    evidence = [str(item) for item in stack_args.get("evidence") or [] if str(item).strip()]
    spec = validate_app_spec(stack_args["stack_manifest"])
    return {
        "app_spec": app_spec_to_dict(spec),
        "domain_name": str(stack_args.get("domain_name") or ""),
        "environment_values": stack_args.get("nonsecret_settings") or {},
        "evidence": evidence,
        "summary": str(stack_args.get("summary") or f"Deploy {spec.display_name}"),
        "reasoning": str(stack_args.get("reasoning") or "Evidence-backed Compose AppSpec."),
    }
