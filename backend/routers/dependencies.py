"""System dependency management page and APIs."""
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from dependencies import dependency_manager
from templating import templates

router = APIRouter(tags=["dependencies"])


@router.get("/dependencies", response_class=HTMLResponse)
async def dependencies_index(request: Request):
    dependencies = dependency_manager.get_all_statuses()
    return templates.TemplateResponse(
        "pages/dependencies.html",
        {
            "request": request,
            "active_page": "dependencies",
            "dependencies": dependencies,
        },
    )


@router.get("/dependencies/{dependency_id}", response_class=HTMLResponse)
async def dependency_detail(request: Request, dependency_id: str):
    dependency = dependency_manager.get_status(dependency_id)
    service = dependency_manager.get_service(dependency_id)
    if dependency is None or service is None:
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


@router.get("/api/dependencies/status")
async def dependency_status():
    return {"dependencies": dependency_manager.get_all_statuses()}


@router.get("/api/dependencies/{dependency_id}/precheck")
async def dependency_precheck(
    dependency_id: str,
    action: str = Query(..., pattern="^(disable|uninstall)$"),
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

    success, message = await dependency_manager.toggle(dependency_id, enabled)
    if not success:
        return JSONResponse({"detail": message}, status_code=409)
    return RedirectResponse(f"/dependencies/{dependency_id}", status_code=303)


@router.post("/api/dependencies/{dependency_id}/install")
async def dependency_install(dependency_id: str):
    current = dependency_manager.get_status(dependency_id, force=True)
    if current is None:
        raise HTTPException(status_code=404, detail="Unknown dependency.")
    if current["healthy"]:
        return {
            "success": True,
            "message": "Dependency is already installed and healthy.",
            "status": current,
        }
    success, message = await dependency_manager.install(dependency_id)
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
