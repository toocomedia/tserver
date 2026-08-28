"""
tools/ai_plan_tester/runner.py — AI execution runner for plan generation & trace capture.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai_helper.services import action_plans, setup_plan_builder
from plugins.ai_helper.services.providers import get_active_provider
from plugins.ai_helper.services.chat import _ACTIVITY_PREFIX, stream_ai_chat
from tools.ai_plan_tester.catalog import BenchmarkApp
from tools.ai_plan_tester.validator import ValidationResult, validate_plan_payload

logger = logging.getLogger(__name__)


@dataclass
class ToolActivityRecord:
    tool: str
    status: str
    label: str
    detail: str
    timestamp_ms: int


@dataclass
class RunResult:
    app: BenchmarkApp
    provider_name: str
    model_name: str
    activities: List[ToolActivityRecord] = field(default_factory=list)
    turn1_prompt: str = ""
    turn1_response: str = ""
    turn2_prompt: str = ""
    turn2_response: str = ""
    plan_id: Optional[str] = None
    plan_data: Dict[str, Any] = field(default_factory=dict)
    validation: Optional[ValidationResult] = None
    duration_ms: int = 0
    error: Optional[str] = None


async def run_ai_test(
    app: BenchmarkApp,
    db: AsyncSession,
    offline: bool = False,
) -> RunResult:
    """
    Executes a dry-run test for a benchmark app using the active panel AI provider.
    Runs Turn 1 (inspection) and Turn 2 (synthesis), captures tool traces, and validates.
    """
    start_time = time.perf_counter()
    provider = await get_active_provider(db)
    provider_name = provider.name if provider else "Offline Simulator"
    model_name = provider.model_name if provider else "deterministic"

    if offline or not provider or not provider.is_enabled:
        return await _run_offline_simulation(app, db, provider_name, model_name, start_time)

    session_id = f"test_{app.slug[:16]}_{uuid.uuid4().hex[:8]}"
    activities: List[ToolActivityRecord] = []
    plan_id: Optional[str] = None
    plan_data: Dict[str, Any] = {}

    target_domain = f"{app.slug}.test.internal"
    turn1_prompt = f"Please analyze and configure this application for domain {target_domain}:\n{app.target}"
    turn1_chunks: List[str] = []

    try:
        # Turn 1: Inspection & questionnaire
        async for chunk in stream_ai_chat(
            db=db,
            session_id=session_id,
            user_message=turn1_prompt,
            task_type="app_deploy",
            context_key=app.slug,
        ):
            if chunk.startswith(_ACTIVITY_PREFIX):
                act_data = json.loads(chunk[len(_ACTIVITY_PREFIX):])
                activities.append(ToolActivityRecord(
                    tool=act_data.get("tool", "tool"),
                    status=act_data.get("status", "done"),
                    label=act_data.get("label", ""),
                    detail=act_data.get("detail", ""),
                    timestamp_ms=int((time.perf_counter() - start_time) * 1000),
                ))
            else:
                turn1_chunks.append(chunk)

        turn1_full = "".join(turn1_chunks)
        m_plan = re.search(r"\[ACTION:APP_SETUP_PLAN:([a-z0-9_]+)\]", turn1_full, re.IGNORECASE)
        if m_plan:
            plan_id = m_plan.group(1).split(":")[0].strip()

        turn2_prompt = ""
        turn2_full = ""

        # Turn 2: Confirm configuration if interview tags were emitted and no plan yet
        if not plan_id:
            default_method = (
                "compose_stack" if app.is_multi_container
                else (f"registry_image:{app.target}" if app.source_type == "image" else "git_build")
            )
            answers = [
                "Setup interview answers:",
                f"deployment_method: {default_method}",
            ]
            if app.expected_database in ("postgresql", "mariadb"):
                answers.append(f"provider.{app.expected_database}: docker")
            if "admin_email" in turn1_full.lower():
                answers.append("admin_email: admin@example.test")

            turn2_prompt = "\n".join(answers)
            turn2_chunks: List[str] = []

            async for chunk in stream_ai_chat(
                db=db,
                session_id=session_id,
                user_message=turn2_prompt,
                task_type="app_deploy",
                context_key=app.slug,
            ):
                if chunk.startswith(_ACTIVITY_PREFIX):
                    act_data = json.loads(chunk[len(_ACTIVITY_PREFIX):])
                    activities.append(ToolActivityRecord(
                        tool=act_data.get("tool", "tool"),
                        status=act_data.get("status", "done"),
                        label=act_data.get("label", ""),
                        detail=act_data.get("detail", ""),
                        timestamp_ms=int((time.perf_counter() - start_time) * 1000),
                    ))
                else:
                    turn2_chunks.append(chunk)

            turn2_full = "".join(turn2_chunks)
            m_plan2 = re.search(r"\[ACTION:APP_SETUP_PLAN:([a-z0-9_]+)\]", turn2_full, re.IGNORECASE)
            if m_plan2:
                plan_id = m_plan2.group(1).split(":")[0].strip()

        # Retrieve the verified plan from database
        if plan_id:
            fetched_plan = await action_plans.get_action_plan(db, plan_id)
            if fetched_plan:
                plan_data = fetched_plan

        # If LLM didn't return a plan ID, try server-side automatic plan builder
        if not plan_data:
            auto_plan = await setup_plan_builder.build_automatic_setup_plan(
                db=db,
                session_id=session_id,
                user_id=1,
                source_type=app.source_type,
                repository_url=app.target if app.source_type == "git" else "",
                image_reference=app.target if app.source_type == "image" else "",
                domain_name=target_domain,
            )
            plan_id = auto_plan.plan_id
            plan_data = await action_plans.get_action_plan(db, plan_id) or {}

        val_result = validate_plan_payload(plan_data, app)
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        return RunResult(
            app=app,
            provider_name=provider_name,
            model_name=model_name,
            activities=activities,
            turn1_prompt=turn1_prompt,
            turn1_response=turn1_full,
            turn2_prompt=turn2_prompt,
            turn2_response=turn2_full,
            plan_id=plan_id,
            plan_data=plan_data,
            validation=val_result,
            duration_ms=duration_ms,
        )

    except Exception as exc:
        logger.exception("AI test run failed for %s: %s", app.slug, exc)
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return RunResult(
            app=app,
            provider_name=provider_name,
            model_name=model_name,
            activities=activities,
            turn1_prompt=turn1_prompt,
            duration_ms=duration_ms,
            error=str(exc),
        )


async def _run_offline_simulation(
    app: BenchmarkApp,
    db: AsyncSession,
    provider_name: str,
    model_name: str,
    start_time: float,
) -> RunResult:
    """Fast offline simulation mode using local inspection and plan builders."""
    session_id = f"sim_{app.slug[:16]}_{uuid.uuid4().hex[:8]}"
    target_domain = f"{app.slug}.test.internal"
    activities = [
        ToolActivityRecord("inspect_app_source", "done", "Simulated local inspection", app.target, 5),
        ToolActivityRecord("propose_app_spec_plan", "done", "Synthesizing validated plan", target_domain, 25),
    ]
    auto_plan = await setup_plan_builder.build_automatic_setup_plan(
        db=db,
        session_id=session_id,
        user_id=1,
        source_type=app.source_type,
        repository_url=app.target if app.source_type == "git" else "",
        image_reference=app.target if app.source_type == "image" else "",
        domain_name=target_domain,
    )
    plan_data = await action_plans.get_action_plan(db, auto_plan.plan_id) or {}
    val_result = validate_plan_payload(plan_data, app)
    duration_ms = int((time.perf_counter() - start_time) * 1000)

    return RunResult(
        app=app,
        provider_name="Offline Simulation Engine",
        model_name="deterministic-builder",
        activities=activities,
        turn1_prompt=f"Simulate setup for {app.target}",
        turn1_response="Generated configuration in dry-run mode.",
        plan_id=auto_plan.plan_id,
        plan_data=plan_data,
        validation=val_result,
        duration_ms=duration_ms,
    )
