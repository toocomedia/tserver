"""System dependency management page and APIs."""
import asyncio
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pathlib import Path

from database import get_db
from dependencies import dependency_manager
from models.domain import Domain
from models.php_website import PhpWebsite
from templating import templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["dependencies"])

@router.get("/dependencies/assets/{dependency_id}", include_in_schema=False)
async def dependency_asset(dependency_id: str):
    service = dependency_manager.get_service(dependency_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Dependency asset not found.")
    metadata = dependency_manager._metadata.get(dependency_id, {})
    filename = str(metadata.get("icon") or "")
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Dependency asset not found.")
    path = Path("dependencies") / dependency_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Dependency asset not found.")
    return FileResponse(path, media_type="image/png")


@router.get("/dependencies", response_class=HTMLResponse)
async def dependencies_index(request: Request):
    return templates.TemplateResponse(
        "pages/dependencies.html",
        {
            "request": request,
            "active_page": "dependencies",
            "dependency_count": len(dependency_manager._services),
        },
    )


@router.get("/dependencies/{dependency_id}", response_class=HTMLResponse)
async def dependency_detail(request: Request, dependency_id: str):
    service = dependency_manager.get_service(dependency_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")

    if dependency_id == "php":
        return templates.TemplateResponse(
            "pages/php_dependency_detail.html",
            {
                "request": request,
                "active_page": "dependencies",
            },
        )
    dependency = dependency_manager.get_status(dependency_id)
    if dependency is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    dependency["dependents"] = dependency_manager.get_dependent_plugins(dependency_id)
    dependency["install_guide"] = service.get_install_guide()
    dependency["uninstall_guide"] = service.get_uninstall_guide()
    return templates.TemplateResponse(
        "pages/dependency_detail.html",
        {
            "request": request,
            "active_page": "dependencies",
            "dependency": dependency,
        },
    )


def _php_service():
    service = dependency_manager.get_service("php")
    if service is None:
        raise HTTPException(status_code=404, detail="PHP dependency is unavailable.")
    return service


@router.get("/api/dependencies/catalog-view", response_class=HTMLResponse)
async def dependency_catalog_view(request: Request):
    """Render dependency cards after the shell page is visible."""
    dependencies = await asyncio.to_thread(dependency_manager.get_all_statuses)
    return templates.TemplateResponse(
        "pages/partials/dependency_catalog.html",
        {"request": request, "dependencies": dependencies},
    )


@router.get("/api/dependencies/php/runtime-view", response_class=HTMLResponse)
async def php_runtime_view(request: Request):
    """Render PHP runtime data after the fast shell page has loaded."""
    dependency = await asyncio.to_thread(dependency_manager.get_status, "php", force=True)
    if dependency is None:
        raise HTTPException(status_code=404, detail="PHP dependency is unavailable.")
    return templates.TemplateResponse(
        "pages/partials/php_dependency_runtime.html",
        {"request": request, "dependency": dependency},
    )


@router.post("/api/dependencies/php/check-available")
async def php_check_available_versions(
    db: AsyncSession = Depends(get_db),
):
    """Refresh configured APT indexes before showing installable PHP versions."""
    service = _php_service()
    from services.resource_guard_service import resource_guard_service

    result = await resource_guard_service.preflight(db, "native_light")
    if not result["ok"]:
        return JSONResponse(
            {
                "success": False,
                "detail": f"Resource Guard blocked PHP availability check: {result['reason']}",
                "resource_guard": result,
            },
            status_code=409,
        )
    token = resource_guard_service.register(
        "dependency", "php", "normal", "Check PHP availability", profile="native_light"
    )
    try:
        success, message = await asyncio.to_thread(service.check_available_versions)
    finally:
        resource_guard_service.unregister(token)
    if not success:
        return JSONResponse({"success": False, "detail": message}, status_code=409)
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status("php", force=True),
    }


@router.post("/api/dependencies/php/enable-external-repository")
async def php_enable_external_repository(
    confirmation: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Explicitly add the fixed, reviewed PHP PPA and refresh APT."""
    expected = "ENABLE EXTERNAL PHP REPOSITORY"
    if confirmation.strip() != expected:
        return JSONResponse(
            {"success": False, "detail": f"Type {expected} to enable the external PHP repository."},
            status_code=409,
        )
    service = _php_service()
    from services.resource_guard_service import resource_guard_service

    result = await resource_guard_service.preflight(db, "native_light")
    if not result["ok"]:
        return JSONResponse(
            {
                "success": False,
                "detail": f"Resource Guard blocked the external PHP repository action: {result['reason']}",
                "resource_guard": result,
            },
            status_code=409,
        )
    token = resource_guard_service.register(
        "dependency", "php", "normal", "Enable external PHP repository", profile="native_light"
    )
    try:
        success, message = await asyncio.to_thread(service.enable_external_repository)
    finally:
        resource_guard_service.unregister(token)
    if not success:
        return JSONResponse({"success": False, "detail": message}, status_code=409)
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status("php", force=True),
    }


@router.post("/api/dependencies/php/versions/{version}/install")
async def php_install_version(
    version: str,
    db: AsyncSession = Depends(get_db),
):
    """Install one explicitly selected PHP-FPM version."""
    service = _php_service()
    from services.resource_guard_service import resource_guard_service

    result = await resource_guard_service.preflight(db, "native_light")
    if not result["ok"]:
        return JSONResponse(
            {
                "success": False,
                "detail": f"Resource Guard blocked PHP installation: {result['reason']}",
                "resource_guard": result,
            },
            status_code=409,
        )
    token = resource_guard_service.register(
        "dependency", "php", "normal", f"Install PHP {version}", profile="native_light"
    )
    try:
        success, message = await asyncio.to_thread(service.install_version, version)
    finally:
        resource_guard_service.unregister(token)
    if not success:
        return JSONResponse({"success": False, "detail": message}, status_code=409)
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status("php", force=True),
    }


@router.post("/api/dependencies/php/versions/{version}/uninstall")
async def php_uninstall_version(
    version: str,
    confirmation: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Remove one panel-managed PHP runtime without touching site files."""
    expected = f"REMOVE PHP {version}"
    if confirmation.strip() != expected:
        return JSONResponse(
            {"success": False, "detail": f"Type {expected} to uninstall this PHP version."},
            status_code=409,
        )
    sites = (await db.execute(
        select(PhpWebsite.id, Domain.name)
        .join(Domain, Domain.id == PhpWebsite.domain_id)
        .where(PhpWebsite.php_version == version)
        .order_by(Domain.name)
    )).all()
    if sites:
        return JSONResponse(
            {
                "success": False,
                "detail": f"PHP {version} is still used by managed PHP websites.",
                "sites": [{"id": site_id, "domain": domain} for site_id, domain in sites],
            },
            status_code=409,
        )
    service = _php_service()
    success, message = await asyncio.to_thread(service.uninstall_version, version)
    if not success:
        return JSONResponse({"success": False, "detail": message}, status_code=409)
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status("php", force=True),
    }


@router.get("/api/dependencies/status")
async def dependency_status():
    return {"dependencies": dependency_manager.get_all_statuses()}


@router.get("/api/dependencies/{dependency_id}/precheck")
async def dependency_precheck(
    dependency_id: str,
    action: str = Query(..., pattern="^(disable|uninstall|update)$"),
):
    result = dependency_manager.precheck(dependency_id, action)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    return result


@router.post("/api/dependencies/{dependency_id}/toggle")
async def dependency_toggle(
    dependency_id: str,
    enabled: bool = Form(...),
    confirmed: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    if not enabled:
        precheck = dependency_manager.precheck(dependency_id, "disable")
        if precheck is None:
            raise HTTPException(status_code=404, detail="Unknown dependency.")
        if not confirmed:
            return JSONResponse(
                {
                    "detail": "Confirmation is required before disabling a dependency.",
                    "precheck": precheck,
                },
                status_code=409,
            )
    else:
        # Guard preflight for enabling (may involve Docker pull)
        from services.resource_guard_service import resource_guard_service
        result = await resource_guard_service.preflight(db, "native_light")
        if not result["ok"]:
            return JSONResponse(
                {"detail": f"Resource Guard blocked dependency enable: {result['reason']}", "resource_guard": result},
                status_code=409,
            )

    success, message = await dependency_manager.toggle(dependency_id, enabled)
    if not success:
        return JSONResponse({"detail": message}, status_code=409)
    return RedirectResponse(f"/dependencies/{dependency_id}", status_code=303)


@router.post("/api/dependencies/{dependency_id}/install")
async def dependency_install(dependency_id: str, db: AsyncSession = Depends(get_db)):
    current = dependency_manager.get_status(dependency_id, force=True)
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    if current["healthy"]:
        return {
            "success": True,
            "message": "Dependency is already installed and healthy.",
            "status": current,
        }
    if current["installed"] and current.get("install_origin") == "external":
        return JSONResponse(
            {
                "success": False,
                "detail": "This dependency was installed outside SRV Panel. The panel will not reconfigure it automatically.",
            },
            status_code=409,
        )
    # Guard preflight for install (may be a Docker pull or heavy setup)
    from services.resource_guard_service import resource_guard_service
    service = dependency_manager.get_service(dependency_id)
    profile = str(getattr(service, "install_resource_profile", "plugin_install"))
    result = await resource_guard_service.preflight(db, profile)
    if not result["ok"]:
        return JSONResponse(
            {"success": False, "detail": f"Resource Guard blocked dependency install: {result['reason']}", "resource_guard": result},
            status_code=409,
        )
    guard_token = resource_guard_service.register(
        "dependency", dependency_id, "normal",
        f"Install dependency: {dependency_id}",
        profile=profile,
    )
    try:
        success, message = await dependency_manager.install(dependency_id)
    finally:
        resource_guard_service.unregister(guard_token)
    if not success:
        return JSONResponse(
            {"success": False, "detail": message},
            status_code=409,
        )
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status(dependency_id, force=True),
    }


@router.post("/api/dependencies/{dependency_id}/update/check")
async def dependency_update_check(dependency_id: str):
    current = dependency_manager.get_status(dependency_id, force=True)
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    success, message = await dependency_manager.check_update(dependency_id)
    if not success:
        return JSONResponse({"success": False, "detail": message}, status_code=409)
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status(dependency_id, cached=True),
    }


@router.post("/api/dependencies/{dependency_id}/update")
async def dependency_update(
    dependency_id: str,
    confirmation: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    current = dependency_manager.get_status(dependency_id, cached=True)
    service = dependency_manager.get_service(dependency_id)
    if current is None or service is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    expected_confirmation = str(current.get("update_confirmation") or "")
    if expected_confirmation and confirmation.strip() != expected_confirmation:
        return JSONResponse(
            {"detail": f"Type {expected_confirmation} to update this dependency."},
            status_code=409,
        )
    precheck = dependency_manager.precheck(dependency_id, "update")
    if precheck and precheck["blocked"]:
        return JSONResponse(
            {"detail": precheck.get("reason") or "Dependency update is blocked.", "precheck": precheck},
            status_code=409,
        )
    profile = str(getattr(service, "update_resource_profile", "plugin_install"))
    from services.resource_guard_service import resource_guard_service
    result = await resource_guard_service.preflight(db, profile)
    if not result["ok"]:
        return JSONResponse(
            {"detail": f"Resource Guard blocked dependency update: {result['reason']}", "resource_guard": result},
            status_code=409,
        )
    guard_token = resource_guard_service.register(
        "dependency", dependency_id, "normal",
        f"Update dependency: {dependency_id}",
        profile=profile,
    )
    try:
        success, message = await dependency_manager.update(dependency_id)
    finally:
        resource_guard_service.unregister(guard_token)
    if not success:
        return JSONResponse({"success": False, "detail": message}, status_code=409)
    return {
        "success": True,
        "message": message,
        "status": dependency_manager.get_status(dependency_id, force=True),
    }


@router.get("/api/dependencies/{dependency_id}/install-guide")
async def dependency_install_guide(dependency_id: str):
    service = dependency_manager.get_service(dependency_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    return service.get_install_guide()


@router.get("/api/dependencies/{dependency_id}/uninstall-guide")
async def dependency_uninstall_guide(dependency_id: str):
    service = dependency_manager.get_service(dependency_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    return {
        "precheck": dependency_manager.precheck(dependency_id, "uninstall"),
        "guide": service.get_uninstall_guide(),
    }
