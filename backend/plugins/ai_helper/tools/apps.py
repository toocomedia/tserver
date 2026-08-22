"""
tools/apps.py — Tool handlers for PHP websites, Python apps, and Container apps inspection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.app_deployment import AppDeployment
from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from models.domain import Domain
from models.hosted_app import HostedApp
from models.php_website import PhpWebsite


async def get_apps_overview(
    db: AsyncSession,
    app_type: Optional[str] = "all",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieves overview of PHP, Python, and Container apps."""
    filter_type = (app_type or "all").lower().strip()
    result: Dict[str, Any] = {"status": "ok"}

    # Fetch domain names map
    dom_result = await db.execute(select(Domain))
    domains = {d.id: d.name for d in dom_result.scalars().all()}

    # 1. PHP Websites
    if filter_type in ("all", "php"):
        php_rows = (await db.execute(select(PhpWebsite))).scalars().all()
        result["php_websites"] = [
            {
                "id": p.id,
                "domain": domains.get(p.domain_id, "unknown"),
                "preset": p.preset,
                "php_version": p.php_version,
                "status": p.status,
                "webroot_path": p.root_path,
                "last_error": p.last_error,
            }
            for p in php_rows
        ]

    # 2. Python Apps
    if filter_type in ("all", "python"):
        py_rows = (await db.execute(select(HostedApp))).scalars().all()
        result["python_apps"] = [
            {
                "id": a.id,
                "domain": domains.get(a.domain_id, "unknown"),
                "runtime": a.runtime,
                "port": a.port,
                "repository": a.repository_url,
                "branch": a.branch,
                "status": a.status,
                "last_error": a.last_error,
            }
            for a in py_rows
        ]

    # 3. Container Apps (Docker / Railpack)
    if filter_type in ("all", "container"):
        c_rows = (await db.execute(select(ContainerApp))).scalars().all()
        result["container_apps"] = [
            {
                "id": c.id,
                "domain": domains.get(c.domain_id, "unknown"),
                "container_name": c.container_name,
                "build_mode": c.build_mode,
                "host_port": c.host_port,
                "internal_port": c.internal_port,
                "status": c.status,
                "repository": c.repository_url,
                "branch": c.branch,
                "last_error": c.last_error,
            }
            for c in c_rows
        ]

    return result


async def get_app_logs(
    db: AsyncSession,
    app_id: int,
    app_type: str,
    max_lines: int = 50,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieves recent deployment or build logs for an app."""
    kind = app_type.lower().strip()
    limit_lines = min(max(10, max_lines), 100)

    if kind == "container":
        stmt = (
            select(ContainerAppDeployment)
            .where(ContainerAppDeployment.app_id == app_id)
            .order_by(desc(ContainerAppDeployment.id))
            .limit(1)
        )
        dep = (await db.execute(stmt)).scalar_one_or_none()
        if not dep:
            return {"status": "ok", "app_id": app_id, "logs": "No deployment logs found for this container app."}

        raw_output = (dep.output or "") + ("\n" + dep.error if dep.error else "")
        lines = raw_output.strip().splitlines()[-limit_lines:]
        return {
            "status": "ok",
            "app_id": app_id,
            "deployment_id": dep.id,
            "stage": dep.stage,
            "deployment_status": dep.status,
            "recent_logs": "\n".join(lines),
        }

    elif kind == "python":
        stmt = (
            select(AppDeployment)
            .where(AppDeployment.app_id == app_id)
            .order_by(desc(AppDeployment.id))
            .limit(1)
        )
        dep = (await db.execute(stmt)).scalar_one_or_none()
        if not dep:
            return {"status": "ok", "app_id": app_id, "logs": "No deployment logs found for this Python app."}

        raw_output = (dep.output or "") + ("\n" + dep.error if dep.error else "")
        lines = raw_output.strip().splitlines()[-limit_lines:]
        return {
            "status": "ok",
            "app_id": app_id,
            "deployment_id": dep.id,
            "stage": dep.stage,
            "deployment_status": dep.status,
            "recent_logs": "\n".join(lines),
        }

    elif kind == "php":
        site = await db.get(PhpWebsite, app_id)
        if not site:
            return {"status": "error", "message": f"PHP website #{app_id} not found."}
        return {
            "status": "ok",
            "app_id": app_id,
            "status": site.status,
            "last_error": site.last_error or "No errors recorded.",
            "last_warning": site.last_warning,
        }

    return {"status": "error", "message": f"Unsupported app type: {app_type}"}


async def redeploy_app(
    db: AsyncSession,
    app_id: int,
    app_type: str = "container",
    reason: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Triggers a clean rebuild and container redeployment for an existing application."""
    kind = (app_type or "container").lower().strip()
    if kind == "container":
        app = await db.get(ContainerApp, app_id)
        if not app:
            return {"status": "error", "message": f"Container app #{app_id} not found."}
        try:
            from services import container_app_deployment_service
            dep = await container_app_deployment_service.queue_deployment(
                db, app, action="deploy" if app.status == "pending" else "redeploy"
            )
            await db.commit()
            return {
                "status": "ok",
                "app_id": app.id,
                "deployment_id": dep.id,
                "action": dep.action,
                "action_tag": f"[ACTION:APP_REDEPLOY:{app.id}]",
                "message": f"Redeployment queued successfully for {app.container_name} (Deployment #{dep.id}).",
            }
        except Exception as exc:
            return {"status": "error", "message": f"Could not queue redeployment: {str(exc)}"}

    return {"status": "error", "message": f"Redeployment not supported for app type '{app_type}'."}

