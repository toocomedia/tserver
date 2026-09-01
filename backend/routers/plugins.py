"""
routers/plugins.py — System Plugin Manager routes.
Handles viewing installed plugins, toggling plugins, running install/uninstall scripts, and uploading plugin zip archives.
"""
import os
import shutil
import logging
from pathlib import Path
from urllib.parse import urlencode
from fastapi import APIRouter, Request, UploadFile, File, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from plugins.manager import PLUGIN_ID_RE, plugin_manager
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugin-manager", tags=["plugin-manager"])
legacy_router = APIRouter(prefix="/plugins", tags=["plugins-legacy"])


@legacy_router.get("/", include_in_schema=False)
@legacy_router.get("", include_in_schema=False)
async def legacy_plugins_redirect():
    """Redirect legacy /plugins/ link to /plugin-manager/."""
    return RedirectResponse("/plugin-manager/", status_code=301)


@legacy_router.get("/info/{plugin_id}", include_in_schema=False)
async def legacy_plugin_info_redirect(plugin_id: str):
    """Redirect legacy plugin info sub-page."""
    return RedirectResponse(f"/plugin-manager/info/{plugin_id}", status_code=301)


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def plugins_index(request: Request):
    """Plugins Management UI page."""
    # Do not run system commands while rendering the plugin list. Health is
    # checked only when a user enables a plugin, keeping this page responsive
    # even when an external dependency is slow or unavailable.
    plugins_list = plugin_manager.list_plugins(check_dependencies=False)
    return templates.TemplateResponse("pages/plugins.html", {
        "request": request,
        "active_page": "plugins",
        "plugins": plugins_list,
        "action_error": request.query_params.get("error"),
        "action_error_plugin_id": request.query_params.get("plugin_id"),
    })


@router.get("/info/{plugin_id}", response_class=HTMLResponse)
async def plugin_detail(request: Request, plugin_id: str):
    """Plugin Details Sub-page."""
    from fastapi import HTTPException
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise HTTPException(status_code=400, detail="Invalid plugin ID")
    
    plugin = plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    
    effective_plugin = plugin_manager._effective(plugin, check_dependencies=True)
    return templates.TemplateResponse(
        "pages/plugin_detail.html",
        {
            "request": request,
            "active_page": "plugins",
            "plugin": effective_plugin,
            "action_error": request.query_params.get("error"),
        },
    )


@router.get("/api/check/{plugin_id}")
async def check_plugin_api(plugin_id: str):
    """Run live dependency health check for a plugin."""
    from fastapi import HTTPException
    plugin = plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    effective_plugin = plugin_manager._effective(plugin, check_dependencies=True)
    return JSONResponse(effective_plugin)


@router.get("/assets/{plugin_id}/{filename}")
@legacy_router.get("/assets/{plugin_id}/{filename}", include_in_schema=False)
async def plugin_asset(plugin_id: str, filename: str):
    """Serve plugin static assets like icons."""
    import config
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise HTTPException(status_code=400, detail="Invalid plugin ID")
    plugin_dir = config.BASE_DIR / "plugins" / plugin_id
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = plugin_dir / filename
    if not file_path.exists() or not file_path.is_file():
        if filename == "icon.png":
            fallback_path = config.BASE_DIR / "static" / "NOIMG.png"
            if fallback_path.exists():
                return FileResponse(fallback_path)
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(file_path)


from services.task_manager_service import task_manager_service
from middleware.auth import wants_json


@router.post("/api/install")
@legacy_router.post("/api/install", include_in_schema=False)
async def install_plugin_api(
    request: Request,
    plugin_id: str = Form(...),
):
    """Run installation script for a plugin."""
    plugin = plugin_manager.get_plugin(plugin_id)
    plugin_label = plugin.get("name", plugin_id) if plugin else plugin_id
    
    if wants_json(request):
        task = await task_manager_service.spawn(
            category="plugin",
            action="install",
            target_id=plugin_id,
            label=f"Install {plugin_label}",
            runner=lambda task_rec: plugin_manager.run_plugin_script(
                plugin_id,
                "install",
                log_callback=task_rec.add_log,
            ),
            lock_type="exclusive",
        )
        return {
            "success": True,
            "task_id": task.id,
            "status": "running",
            "message": f"Installing {plugin_label}...",
        }

    success, message = await plugin_manager.run_plugin_script(
        plugin_id,
        "install",
    )
    if success:
        return RedirectResponse("/plugin-manager/", status_code=303)
    query = urlencode({"plugin_id": plugin_id, "error": message[:240]})
    return RedirectResponse(f"/plugin-manager/?{query}", status_code=303)


@router.post("/api/uninstall")
@legacy_router.post("/api/uninstall", include_in_schema=False)
async def uninstall_plugin_api(request: Request, plugin_id: str = Form(...)):
    """Run uninstallation script for a plugin."""
    plugin = plugin_manager.get_plugin(plugin_id)
    plugin_label = plugin.get("name", plugin_id) if plugin else plugin_id

    if wants_json(request):
        task = await task_manager_service.spawn(
            category="plugin",
            action="uninstall",
            target_id=plugin_id,
            label=f"Uninstall {plugin_label}",
            runner=lambda task_rec: plugin_manager.run_plugin_script(
                plugin_id,
                "uninstall",
                log_callback=task_rec.add_log,
            ),
            lock_type="exclusive",
        )
        return {
            "success": True,
            "task_id": task.id,
            "status": "running",
            "message": f"Uninstalling {plugin_label}...",
        }

    success, message = await plugin_manager.run_plugin_script(plugin_id, "uninstall")
    if success:
        return RedirectResponse("/plugin-manager/", status_code=303)
    return JSONResponse({"detail": message}, status_code=400)


@router.post("/api/purge-data")
@legacy_router.post("/api/purge-data", include_in_schema=False)
async def purge_plugin_data_api(
    request: Request,
    plugin_id: str = Form(...),
    confirmation: str = Form(...),
):
    """Permanently remove preserved plugin volumes after explicit confirmation."""
    plugin = plugin_manager.get_plugin(plugin_id)
    plugin_label = plugin.get("name", plugin_id) if plugin else plugin_id

    if wants_json(request):
        task = await task_manager_service.spawn(
            category="plugin",
            action="purge",
            target_id=plugin_id,
            label=f"Purge data: {plugin_label}",
            runner=lambda task_rec: plugin_manager.purge_plugin_data(plugin_id, confirmation),
            lock_type="exclusive",
        )
        return {
            "success": True,
            "task_id": task.id,
            "status": "running",
            "message": f"Purging {plugin_label} data...",
        }

    success, message = await plugin_manager.purge_plugin_data(plugin_id, confirmation)
    if success:
        return RedirectResponse("/plugin-manager/", status_code=303)
    return JSONResponse({"detail": message}, status_code=400)


@router.post("/api/toggle")
@legacy_router.post("/api/toggle", include_in_schema=False)
async def toggle_plugin(request: Request, plugin_id: str = Form(...), enabled: bool = Form(...)):
    """Enable or disable a plugin."""
    plugin = plugin_manager.get_plugin(plugin_id)
    plugin_label = plugin.get("name", plugin_id) if plugin else plugin_id
    action_label = "Enable" if enabled else "Disable"

    if wants_json(request):
        task = await task_manager_service.spawn(
            category="plugin",
            action="toggle",
            target_id=plugin_id,
            label=f"{action_label} {plugin_label}",
            runner=lambda task_rec: plugin_manager.toggle_plugin(plugin_id, enabled),
        )
        return {
            "success": True,
            "task_id": task.id,
            "status": "running",
            "message": f"{action_label}ing {plugin_label}...",
        }

    success, message = await plugin_manager.toggle_plugin(plugin_id, enabled)
    if success:
        return RedirectResponse("/plugin-manager/", status_code=303)
    query = urlencode({"plugin_id": plugin_id, "error": message[:240]})
    return RedirectResponse(f"/plugin-manager/?{query}", status_code=303)


@router.post("/api/upload")
@legacy_router.post("/api/upload", include_in_schema=False)
async def upload_plugin(request: Request, plugin_file: UploadFile = File(...)):
    """Upload and install a plugin zip package."""
    if not plugin_file.filename.endswith(".zip"):
        return JSONResponse({"detail": "Only .zip files are allowed."}, status_code=400)

    temp_path = Path("/tmp") / plugin_file.filename if os.name != "nt" else Path(os.getenv("TEMP", "C:/tmp")) / plugin_file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(plugin_file.file, buffer)

        success, message = plugin_manager.upload_plugin_zip(str(temp_path))
        if success:
            return RedirectResponse("/plugin-manager/", status_code=303)
        return JSONResponse({"detail": message}, status_code=400)
    except Exception as exc:
        logger.error("Plugin upload error: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
