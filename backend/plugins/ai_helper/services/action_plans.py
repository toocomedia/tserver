"""
services/action_plans.py — Immutable server-side action plans and lifecycle management.
Enforces cryptographic hashing, TTL expiration, and replay prevention.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ai_helper import AiActionEvent, AiActionPlan

logger = logging.getLogger(__name__)

# Default time-to-live for an unapplied proposal plan (30 minutes)
DEFAULT_PLAN_TTL_MINUTES = 30


def _compute_hash(payload: Dict[str, Any]) -> str:
    """Computes a deterministic SHA-256 hash of the payload dictionary."""
    normalized_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()


async def create_action_plan(
    db: AsyncSession,
    session_id: str,
    action_type: str,
    payload: Dict[str, Any],
    summary: str = "",
    confidence: float = 1.0,
    reasoning: str = "",
    user_id: Optional[int] = None,
    ttl_minutes: int = DEFAULT_PLAN_TTL_MINUTES,
) -> AiActionPlan:
    """
    Creates and persists an immutable AI Action Plan.
    Returns the created AiActionPlan record.
    """
    plan_id = f"plan_{uuid.uuid4().hex[:16]}"
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = _compute_hash(payload)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    plan = AiActionPlan(
        plan_id=plan_id,
        user_id=user_id,
        session_id=session_id,
        action_type=action_type,
        status="awaiting_approval",
        payload_json=payload_json,
        payload_hash=payload_hash,
        summary=summary[:512] if summary else f"Proposed {action_type.replace('_', ' ')}",
        confidence=max(0.0, min(1.0, float(confidence))),
        reasoning=reasoning,
        expires_at=expires_at,
    )
    db.add(plan)

    event = AiActionEvent(
        plan_id=plan_id,
        event_type="created",
        user_id=user_id,
        details=f"Plan created with hash {payload_hash[:8]}",
    )
    db.add(event)

    await db.commit()
    await db.refresh(plan)
    logger.info("AI Action Plan [%s] created: %s (hash: %s)", plan_id, summary, payload_hash[:8])
    return plan


async def get_action_plan(
    db: AsyncSession, plan_id: str, *, user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves an action plan by opaque ID.
    Enforces TTL expiration check and returns a structured dictionary.
    """
    stmt = select(AiActionPlan).where(AiActionPlan.plan_id == plan_id)
    plan = (await db.execute(stmt)).scalar_one_or_none()
    if not plan:
        return None
    if plan.user_id is not None and plan.user_id != user_id:
        return None

    now = datetime.now(timezone.utc)
    # Check for expiration
    plan_exp = plan.expires_at if plan.expires_at.tzinfo else plan.expires_at.replace(tzinfo=timezone.utc)
    if plan.status == "awaiting_approval" and now > plan_exp:
        plan.status = "expired"
        event = AiActionEvent(plan_id=plan_id, event_type="expired", details="Plan expired due to TTL")
        db.add(event)
        await db.commit()
        await db.refresh(plan)

    try:
        parsed_payload = json.loads(plan.payload_json)
    except Exception:
        parsed_payload = {}

    return {
        "plan_id": plan.plan_id,
        "action_type": plan.action_type,
        "status": plan.status,
        "summary": plan.summary,
        "confidence": plan.confidence,
        "reasoning": plan.reasoning,
        "payload": parsed_payload,
        "payload_hash": plan.payload_hash,
        "session_id": plan.session_id,
        "user_id": plan.user_id,
        "is_expired": plan.status == "expired",
        "expires_at": plan.expires_at.isoformat() if plan.expires_at else None,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


async def mark_plan_applied(
    db: AsyncSession, plan_id: str, user_id: Optional[int] = None,
    *, expected_hash: str | None = None, expected_action_type: str | None = None,
) -> Dict[str, Any]:
    """
    Marks a plan as applied (executed / imported into wizard).
    Replay-protected: rejects plans that are not in 'awaiting_approval' status.
    """
    stmt = select(AiActionPlan).where(AiActionPlan.plan_id == plan_id)
    plan = (await db.execute(stmt)).scalar_one_or_none()
    if not plan:
        raise ValueError(f"Action plan '{plan_id}' not found.")
    if plan.user_id is not None and plan.user_id != user_id:
        raise ValueError("This action plan belongs to another user.")
    if expected_action_type and plan.action_type != expected_action_type:
        raise ValueError("Action plan type is not valid for this operation.")
    if expected_hash and plan.payload_hash != expected_hash:
        raise ValueError("Action plan payload hash does not match.")
    try:
        parsed = json.loads(plan.payload_json)
    except Exception as exc:
        raise ValueError("Action plan payload is invalid.") from exc
    if _compute_hash(parsed) != plan.payload_hash:
        raise ValueError("Action plan payload integrity check failed.")

    if plan.status == "applied":
        raise ValueError("This action plan has already been applied (replay prevented).")

    if plan.status == "expired":
        raise ValueError("This action plan has expired. Please ask the AI to generate a fresh proposal.")

    if plan.status != "awaiting_approval":
        raise ValueError(f"Cannot apply action plan in '{plan.status}' status.")

    now = datetime.now(timezone.utc)
    plan_exp = plan.expires_at if plan.expires_at.tzinfo else plan.expires_at.replace(tzinfo=timezone.utc)
    if now > plan_exp:
        plan.status = "expired"
        await db.commit()
        raise ValueError("This action plan has expired.")

    plan.status = "applied"
    event = AiActionEvent(
        plan_id=plan_id,
        event_type="applied",
        user_id=user_id,
        details="Plan applied to deployment wizard",
    )
    db.add(event)
    await db.commit()
    await db.refresh(plan)

    logger.info("AI Action Plan [%s] marked as applied", plan_id)
    return {"status": "ok", "plan_id": plan_id, "plan_status": "applied"}
