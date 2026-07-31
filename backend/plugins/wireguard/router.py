"""
backend/plugins/wireguard/router.py — FastAPI router for the WireGuard VPN plugin.

Routes:
  GET  /plugins/wireguard/              → Main UI page
  POST /plugins/wireguard/api/install   → Run install script
  POST /plugins/wireguard/api/peers     → Add a new peer
  POST /plugins/wireguard/api/peers/delete  → Remove a peer
  GET  /plugins/wireguard/api/peers/config  → Download .conf file
  POST /plugins/wireguard/api/restart   → Restart wg-quick tunnel
"""
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from templating import templates
from plugins.wireguard.service import wireguard_service

import config as panel_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plugins/wireguard", tags=["wireguard"])

SCRIPT_DIR = Path(__file__).parent / "scripts"

# In-memory store of peer private keys so users can download .conf
# immediately after creation. Keys are intentionally ephemeral —
# they are NOT stored on disk to avoid leaving plaintext credentials.
# Key: pubkey string → Value: {"name", "private_key", "peer_ip"}
_peer_download_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def wireguard_index(request: Request):
    """Render the WireGuard VPN management page."""
    from plugins.manager import plugin_manager
    from services.component_state import component_state_store

    plugin_info    = plugin_manager.get_plugin("wireguard")
    plugin_version = plugin_info["version"] if plugin_info else "1.0.0"

    # Read operation + last_error from persistent state store
    state = component_state_store.get("plugin", "wireguard")
    is_installing = state.operation in ("installing", "uninstalling")

    status = wireguard_service.get_status()
    peers  = wireguard_service.list_peers() if status["installed"] else []

    # Mark any peer that has a pending downloadable config
    for peer in peers:
        peer["has_download"] = peer["pubkey"] in _peer_download_cache

    server_endpoint = getattr(panel_config, "SERVER_IP", "") or ""

    # install_error: prefer ?error from URL, fall back to stored last_error
    url_error = request.query_params.get("error")
    install_error = url_error or (state.last_error if not status["installed"] else None)

    return templates.TemplateResponse("wireguard.html", {
        "request":         request,
        "active_page":     "plugins",
        "plugin_version":  plugin_version,
        "status":          status,
        "peers":           peers,
        "server_endpoint": server_endpoint,
        "is_installing":   is_installing,
        "install_error":   install_error,
        "notice":          request.query_params.get("notice"),
        "error":           request.query_params.get("error"),
    })


# ---------------------------------------------------------------------------
# Status API — used by the install progress poller
# ---------------------------------------------------------------------------

@router.get("/api/status")
async def wireguard_status():
    """Return lightweight JSON status for the install progress poller."""
    from services.component_state import component_state_store
    state  = component_state_store.get("plugin", "wireguard")
    status = wireguard_service.get_status()
    return JSONResponse({
        "installed":    status["installed"],
        "active":       status["active"],
        "operation":    state.operation,
        "last_error":   state.last_error,
    })


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

@router.post("/api/install")
async def install_wireguard(request: Request):
    """Trigger the WireGuard installation script via the plugin manager."""
    from plugins.manager import plugin_manager
    success, message = await plugin_manager.run_plugin_script("wireguard", "install")
    if success:
        return RedirectResponse("/plugins/wireguard/?notice=WireGuard+installed+successfully.", status_code=303)
    logger.error("WireGuard install failed: %s", message)
    error_snippet = message[:200].replace(" ", "+")
    return RedirectResponse(f"/plugins/wireguard/?error={error_snippet}", status_code=303)


# ---------------------------------------------------------------------------
# Peers — Add
# ---------------------------------------------------------------------------

@router.post("/api/peers")
async def add_peer(request: Request, name: str = Form(...)):
    """Create a new WireGuard peer and cache its private key for one download."""
    name = name.strip()[:64]
    if not name:
        return RedirectResponse("/plugins/wireguard/?error=Peer+name+cannot+be+empty.", status_code=303)

    try:
        peer = wireguard_service.add_peer(name)
    except Exception as exc:
        logger.error("add_peer failed: %s", exc)
        error = str(exc)[:200].replace(" ", "+")
        return RedirectResponse(f"/plugins/wireguard/?error={error}", status_code=303)

    # Cache private key so user can download .conf once
    _peer_download_cache[peer["pubkey"]] = {
        "name":        peer["name"],
        "private_key": peer["private_key"],
        "peer_ip":     peer["peer_ip"],
    }

    pubkey_safe = peer["pubkey"].replace("+", "%2B").replace("/", "%2F").replace("=", "%3D")
    return RedirectResponse(
        f"/plugins/wireguard/?notice=Peer+{name}+created.+Download+the+config+now.&highlight={pubkey_safe}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Peers — Delete
# ---------------------------------------------------------------------------

@router.post("/api/peers/delete")
async def delete_peer(pubkey: str = Form(...)):
    """Remove a peer by public key (pubkey passed as form field to avoid
    URL-routing issues with base64 characters like '/' and '+')."""
    removed = wireguard_service.remove_peer(pubkey)
    _peer_download_cache.pop(pubkey, None)

    if removed:
        return RedirectResponse("/plugins/wireguard/?notice=Peer+removed+successfully.", status_code=303)
    return RedirectResponse("/plugins/wireguard/?error=Peer+not+found+or+could+not+be+removed.", status_code=303)


# ---------------------------------------------------------------------------
# Peers — Download Config
# ---------------------------------------------------------------------------

@router.get("/api/peers/config")
async def download_peer_config(pubkey: str):
    """
    Generate and serve a WireGuard .conf file for the given peer.
    Pubkey is passed as ?pubkey=... query param to avoid URL routing
    issues with base64 characters ('/' and '+') in path segments.
    Private key is only available immediately after peer creation.
    """
    from urllib.parse import unquote
    pubkey = unquote(pubkey)

    cached = _peer_download_cache.get(pubkey)
    if not cached:
        raise HTTPException(
            400,
            "Config download is only available immediately after creating a peer. "
            "The private key is not stored — delete and recreate the peer to get a new config."
        )

    server_pubkey   = wireguard_service.get_server_pubkey()
    server_endpoint = getattr(panel_config, "SERVER_IP", "") or "YOUR_SERVER_IP"

    conf_text = wireguard_service.get_peer_config(
        peer_name        = cached["name"],
        peer_private_key = cached["private_key"],
        peer_ip          = cached["peer_ip"],
        server_pubkey    = server_pubkey,
        server_endpoint  = server_endpoint,
    )

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in cached["name"])
    filename  = f"wg-{safe_name}.conf"

    return PlainTextResponse(
        conf_text,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        media_type="text/plain",
    )


# ---------------------------------------------------------------------------
# Tunnel — Restart
# ---------------------------------------------------------------------------

@router.post("/api/restart")
async def restart_tunnel():
    """Restart wg-quick@wg0 systemd service."""
    ok = wireguard_service.tunnel_restart()
    if ok:
        return RedirectResponse("/plugins/wireguard/?notice=Tunnel+restarted.", status_code=303)
    return RedirectResponse("/plugins/wireguard/?error=Failed+to+restart+tunnel.+Check+server+logs.", status_code=303)


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

@router.post("/api/uninstall")
async def uninstall_wireguard(request: Request):
    """Trigger the WireGuard uninstallation script via the plugin manager."""
    from plugins.manager import plugin_manager
    # Clear the in-memory config cache on uninstall
    _peer_download_cache.clear()
    success, message = await plugin_manager.run_plugin_script("wireguard", "uninstall")
    if success:
        return RedirectResponse("/plugins/?notice=WireGuard+uninstalled+successfully.", status_code=303)
    logger.error("WireGuard uninstall failed: %s", message)
    error_snippet = message[:200].replace(" ", "+")
    return RedirectResponse(f"/plugins/wireguard/?error={error_snippet}", status_code=303)
