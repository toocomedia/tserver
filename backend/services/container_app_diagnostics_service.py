"""Sanitized App Engine runtime diagnostics for the App page and AI evidence collection."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.domain import Domain
from models.ssl_cert import SslCert
from services import container_app_service as apps

_SENSITIVE = re.compile(r"(?i)\b(password|secret|token|api[_-]?key|database[_-]?url)\b\s*([=:])\s*[^\s,'\"]+")


def _redact(text: str) -> str:
    return _SENSITIVE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def _container_logs(name: str) -> str:
    result = apps._run(["docker", "logs", "--tail", "80", name], timeout=20)
    if result.returncode:
        return ""
    return _redact((result.stdout + result.stderr).strip())[-12_000:]


def _root_cause(app: ContainerApp, services: dict[str, Any], deployment: ContainerAppDeployment | None) -> str:
    if any(item.get("status") not in {"running", "restarting"} for item in services.values()):
        return "one_or_more_stack_services_not_running"
    if app.health_state == "degraded":
        return "verified_http_readiness_failed_while_process_is_running"
    if app.health_state == "unverified":
        return "no_verified_http_readiness_endpoint"
    if app.health_state == "failed" or (deployment and deployment.status == "failed"):
        return "deployment_or_process_failure"
    return "no_active_runtime_failure"


async def collect(db: AsyncSession, app: ContainerApp, domain: Domain) -> dict[str, Any]:
    deployment = await db.scalar(select(ContainerAppDeployment).where(
        ContainerAppDeployment.app_id == app.id,
    ).order_by(ContainerAppDeployment.id.desc()))
    cert = await db.scalar(select(SslCert).where(SslCert.full_domain == domain.name))
    services: dict[str, Any] = {}
    logs: dict[str, str] = {}
    if app.deploy_type == "official_stack":
        try:
            from services.official_stacks import compose_runtime, stack_runtime_service
            stack = compose_runtime.stack_from_runtime(app)
            services = await asyncio.to_thread(stack_runtime_service.inspect_stack_services, app.id, stack)
            logs = {name: await asyncio.to_thread(_container_logs, item["container_name"])
                    for name, item in services.items()}
        except Exception as exc:
            services = {"manifest": {"status": "invalid", "detail": str(exc)[:1000]}}
    else:
        status = apps._run(["docker", "inspect", "--format", "{{.State.Status}}", app.container_name], timeout=10)
        services = {"web": {"container_name": app.container_name,
                            "status": (status.stdout or "stopped").strip().lower() if status.returncode == 0 else "stopped"}}
        logs = {"web": await asyncio.to_thread(_container_logs, app.container_name)}
    stored = {}
    if deployment and deployment.diagnostics_json:
        try:
            stored = json.loads(deployment.diagnostics_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            stored = {"status": "invalid_saved_diagnostics"}
    return {
        "deployment": {"id": deployment.id if deployment else None, "status": deployment.status if deployment else None,
                       "stage": deployment.stage if deployment else None, "error": _redact((deployment.error or "")[:2000]) if deployment else None},
        "readiness": {"state": app.health_state, "detail": _redact((app.health_detail or "")[:2000])},
        "services": services, "recent_logs": logs,
        "reverse_proxy": {"configured": bool(domain.nginx_config_path), "config_path": domain.nginx_config_path or None,
                          "upstream": f"127.0.0.1:{app.host_port}"},
        "ssl": {"certificate_record": bool(cert), "public_check": "pending_or_external; never startup-gating"},
        "dns": {"state": "not_required_for_private_startup"},
        "snapshots": {"active": app.active_snapshot_id, "pending": app.pending_snapshot_id},
        "root_cause": _root_cause(app, services, deployment), "saved_deployment_diagnostics": stored,
    }
