"""Source-aware update actions for hosted Python applications."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.hosted_app import HostedApp
from services import app_deployment_service, app_update_service

router = APIRouter(prefix="/apps", tags=["app-updates"])


@router.post("/{app_id}/updates/check")
async def check_update(
    app_id: int, db: AsyncSession = Depends(get_db)
):
    app = await _app(db, app_id)
    revision = await app_update_service.check_git_update(db, app)
    if revision.sha == app.deployed_revision:
        notice = "The app is already up to date."
    else:
        notice = f"Update available: {revision.sha[:8]}."
    return _redirect(app_id, notice)


@router.post("/{app_id}/updates/apply")
async def apply_update(
    app_id: int, db: AsyncSession = Depends(get_db)
):
    app = await _app(db, app_id)
    if app.source_type != "git":
        raise HTTPException(409, "ZIP updates are coming soon.")
    await app_update_service.check_git_update(db, app)
    revision = await app_update_service.assert_update_ready(db, app)
    deployment = await app_deployment_service.start(
        db, app, action="update", source_revision=revision
    )
    return RedirectResponse(
        f"/apps/{app_id}?deployment={deployment.id}#deployment",
        status_code=303,
    )


async def _app(db: AsyncSession, app_id: int) -> HostedApp:
    app = await db.get(HostedApp, app_id)
    if app is None:
        raise HTTPException(404, "Python app not found.")
    return app


def _redirect(app_id: int, notice: str) -> RedirectResponse:
    query = urlencode({"notice": notice})
    return RedirectResponse(f"/apps/{app_id}?{query}#updates", status_code=303)
