"""
routers/plugins.py — System Plugin Manager routes.
Handles viewing installed plugins, toggling plugins, running install/uninstall scripts, and uploading plugin zip archives.
"""
import os
import shutil
import logging
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from plugins.manager import plugin_manager
from templating import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("/", response_class=HTMLResponse)
async def plugins_index(request: Request):
    """Plugins Management UI page."""
    plugins_list = plugin_manager.discover_plugins()
    return templates.TemplateResponse("pages/plugins.html", {
        "request": request,
        "active_page": "plugins",
        "plugins": plugins_list,
    })


@router.get("/assets/{plugin_id}/{filename}")
async def plugin_asset(plugin_id: str, filename: str):
    """Serve plugin static assets like icons."""
    import config
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
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


@router.post("/api/install")
async def install_plugin_api(request: Request, plugin_id: str = Form(...)):
    """Run installation script for a plugin."""
    success = plugin_manager.run_plugin_script(plugin_id, "install")
    if success:
        return RedirectResponse("/plugins/", status_code=303)
    return JSONResponse({"detail": f"Failed to install plugin '{plugin_id}'."}, status_code=400)


@router.post("/api/uninstall")
async def uninstall_plugin_api(request: Request, plugin_id: str = Form(...)):
    """Run uninstallation script for a plugin."""
    success = plugin_manager.run_plugin_script(plugin_id, "uninstall")
    if success:
        return RedirectResponse("/plugins/", status_code=303)
    return JSONResponse({"detail": f"Failed to uninstall plugin '{plugin_id}'."}, status_code=400)


@router.post("/api/toggle")
async def toggle_plugin(request: Request, plugin_id: str = Form(...), enabled: bool = Form(...)):
    """Enable or disable a plugin."""
    success = plugin_manager.toggle_plugin(plugin_id, enabled)
    if success:
        return RedirectResponse("/plugins/", status_code=303)
    return JSONResponse({"detail": "Cannot enable plugin before it is installed."}, status_code=400)


@router.post("/api/upload")
async def upload_plugin(request: Request, plugin_file: UploadFile = File(...)):
    """Upload and install a plugin zip package."""
    if not plugin_file.filename.endswith(".zip"):
        return JSONResponse({"detail": "Only .zip files are allowed."}, status_code=400)

    temp_path = Path("/tmp") / plugin_file.filename if os.name != "nt" else Path(os.getenv("TEMP", "C:/tmp")) / plugin_file.filename
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(plugin_file.file, buffer)

        res = plugin_manager.upload_plugin_zip(str(temp_path))
        if res:
            return RedirectResponse("/plugins/", status_code=303)
        return JSONResponse({"detail": "Invalid plugin zip structure."}, status_code=400)
    except Exception as exc:
        logger.error("Plugin upload error: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=500)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
