"""
plugins/ai_helper/router_dev_tester.py — Web playground router for AI Spec & Plan Dev Testing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from plugins.ai_helper.services.providers import get_active_provider
from templating import templates
from tools.ai_plan_tester.catalog import find_app_by_slug, get_catalog, resolve_app_target
from tools.ai_plan_tester.reporter import format_app_report_text, save_app_output_files
from tools.ai_plan_tester.runner import run_ai_test

logger = logging.getLogger(__name__)
dev_tester_router = APIRouter()


class RunTestRequest(BaseModel):
    app_slug: Optional[str] = None
    custom_target: Optional[str] = None
    offline: bool = False


@dev_tester_router.get("/spec-tester", response_class=HTMLResponse)
async def spec_tester_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Renders the interactive AI Spec & Plan Dev Tester playground page."""
    provider = await get_active_provider(db)
    catalog = get_catalog()

    return templates.TemplateResponse("ai_spec_tester.html", {
        "request": request,
        "active_page": "ai_helper",
        "provider": provider,
        "catalog": catalog,
    })


@dev_tester_router.get("/api/spec-tester/catalog")
async def get_catalog_api():
    """Returns the benchmark catalog in JSON format."""
    catalog = get_catalog()
    return {
        "catalog": [
            {
                "name": app.name,
                "slug": app.slug,
                "tier": app.tier,
                "source_type": app.source_type,
                "target": app.target,
                "expected_port": app.expected_port,
                "expected_database": app.expected_database,
                "is_multi_container": app.is_multi_container,
                "description": app.description,
            }
            for app in catalog
        ]
    }


@dev_tester_router.post("/api/spec-tester/run")
async def run_test_api(payload: RunTestRequest, db: AsyncSession = Depends(get_db)):
    """Executes a dry-run AI plan test and returns trace, YAML, JSON, and audit results."""
    target_app = None

    if payload.custom_target and payload.custom_target.strip():
        target_app = resolve_app_target(payload.custom_target.strip())
    elif payload.app_slug and payload.app_slug.strip():
        target_app = find_app_by_slug(payload.app_slug.strip())
        if not target_app:
            target_app = resolve_app_target(payload.app_slug.strip())
    else:
        target_app = get_catalog()[0]

    try:
        run_res = await run_ai_test(target_app, db, offline=payload.offline)

        # Save files to reports/output/<app_slug>
        out_dir = Path("reports/output")
        save_app_output_files(run_res, out_dir)

        val = run_res.validation
        report_txt = format_app_report_text(run_res)

        return JSONResponse({
            "status": "ok",
            "app": {
                "name": target_app.name,
                "slug": target_app.slug,
                "tier": target_app.tier,
                "target": target_app.target,
                "source_type": target_app.source_type,
                "expected_port": target_app.expected_port,
                "expected_database": target_app.expected_database,
            },
            "provider_name": run_res.provider_name,
            "model_name": run_res.model_name,
            "duration_ms": run_res.duration_ms,
            "plan_id": run_res.plan_id,
            "plan_data": run_res.plan_data,
            "compose_yaml": val.compose_yaml if val else "",
            "activities": [
                {
                    "tool": act.tool,
                    "status": act.status,
                    "label": act.label,
                    "detail": act.detail,
                    "timestamp_ms": act.timestamp_ms,
                }
                for act in run_res.activities
            ],
            "turn1_response": run_res.turn1_response,
            "turn2_response": run_res.turn2_response,
            "validation": {
                "is_valid": val.is_valid if val else False,
                "verdict": val.status if val else "FAIL",
                "error_count": val.error_count if val else 1,
                "warning_count": val.warning_count if val else 0,
                "detected_services": val.detected_services if val else [],
                "detected_port": val.detected_port if val else None,
                "detected_database": val.detected_database if val else "none",
                "issues": [
                    {
                        "severity": issue.severity,
                        "field": issue.field,
                        "message": issue.message,
                        "fix_advice": issue.fix_advice,
                    }
                    for issue in (val.issues if val else [])
                ],
            },
            "report_text": report_txt,
            "error": run_res.error,
        })
    except Exception as exc:
        logger.exception("Dev tester API error for %s: %s", target_app.slug, exc)
        return JSONResponse({
            "status": "error",
            "message": str(exc),
        }, status_code=500)
