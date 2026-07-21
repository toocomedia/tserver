"""
services/update_service.py — Light Git update checker & background update runner.
"""
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional
import httpx

from utils.shell import run

logger = logging.getLogger(__name__)

# Repository details
REPO_URL = "https://github.com/toocomedia/tserver.git"
GITHUB_API_URL = "https://api.github.com/repos/toocomedia/tserver/commits/main"

# 24-Hour Cache
CACHE_TTL_SECONDS = 86400

_cache_data: Optional[Dict[str, Any]] = None
_cache_timestamp: float = 0.0
_update_lock = asyncio.Lock()
_is_updating: bool = False


async def get_local_commit() -> Dict[str, str]:
    """Retrieve the current running panel git commit hash."""
    # 1. Check COMMIT_HASH file written by update.sh
    commit_file = Path(__file__).parent.parent / "COMMIT_HASH"
    if not commit_file.exists():
        commit_file = Path("/opt/srv-panel/app/COMMIT_HASH")
        
    if commit_file.exists():
        try:
            sha = commit_file.read_text().strip()
            if sha and len(sha) >= 7:
                return {"sha": sha, "short_sha": sha[:7]}
        except Exception:
            pass

    # 2. Try git rev-parse HEAD directly if inside a git clone
    try:
        res = await run(["git", "rev-parse", "HEAD"])
        if res.success and res.stdout.strip():
            sha = res.stdout.strip()
            return {"sha": sha, "short_sha": sha[:7]}
    except Exception:
        pass

    return {"sha": "unknown", "short_sha": "unknown"}


async def get_remote_commit() -> Dict[str, Any]:
    """Fetch the latest remote commit SHA from GitHub lightweight (git ls-remote or API fallback)."""
    # Method A: git ls-remote (No GitHub API rate limits, ~0.2s duration)
    try:
        res = await run(["git", "ls-remote", REPO_URL, "refs/heads/main"])
        if res.success and res.stdout.strip():
            full_line = res.stdout.strip()
            sha = full_line.split()[0]
            if len(sha) >= 7:
                return {
                    "sha": sha,
                    "short_sha": sha[:7],
                    "commit_message": "Latest main branch release",
                    "commit_date": "",
                    "source": "ls-remote",
                }
    except Exception as exc:
        logger.debug("git ls-remote failed: %s", exc)

    # Method B: GitHub REST API fallback
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            headers = {"User-Agent": "srv-panel/1.0", "Accept": "application/vnd.github.v3+json"}
            r = await client.get(GITHUB_API_URL, headers=headers)
            if r.status_code == 200:
                data = r.json()
                sha = data.get("sha", "")
                commit_info = data.get("commit", {})
                msg = commit_info.get("message", "").split("\n")[0]
                commit_date = commit_info.get("committer", {}).get("date", "")
                return {
                    "sha": sha,
                    "short_sha": sha[:7] if sha else "unknown",
                    "commit_message": msg,
                    "commit_date": commit_date,
                    "source": "github_api",
                }
    except Exception as exc:
        logger.warning("GitHub API check failed: %s", exc)

    return {
        "sha": "unknown",
        "short_sha": "unknown",
        "commit_message": "Could not check remote repository",
        "commit_date": "",
        "source": "error",
    }


async def check_updates(force: bool = False) -> Dict[str, Any]:
    """
    Check if a new update is available on GitHub.
    Uses 24-hour in-memory cache unless force=True.
    """
    global _cache_data, _cache_timestamp

    now = time.time()
    if not force and _cache_data and (now - _cache_timestamp < CACHE_TTL_SECONDS):
        return {**_cache_data, "cached": True}

    local_info = await get_local_commit()
    remote_info = await get_remote_commit()

    has_update = False
    if local_info["sha"] != "unknown" and remote_info["sha"] != "unknown":
        has_update = (local_info["sha"] != remote_info["sha"])
    elif local_info["sha"] == "unknown" and remote_info["sha"] != "unknown":
        # If local version is unknown, assume update might be available or prompt user
        has_update = False

    result = {
        "has_update": has_update,
        "local_sha": local_info["sha"],
        "local_short_sha": local_info["short_sha"],
        "remote_sha": remote_info["sha"],
        "remote_short_sha": remote_info["short_sha"],
        "commit_message": remote_info.get("commit_message", ""),
        "commit_date": remote_info.get("commit_date", ""),
        "last_checked": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "cached": False,
    }

    _cache_data = result
    _cache_timestamp = now
    return result


def _get_update_log_path() -> Path:
    log_dir = Path("/opt/srv-panel/backups")
    if not log_dir.exists():
        log_dir = Path(__file__).parent.parent.parent / "backups"
        log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "update.log"


async def trigger_update() -> Dict[str, Any]:
    """Start the background update process."""
    global _is_updating

    if _is_updating:
        return {"status": "error", "message": "An update is already in progress."}

    _is_updating = True
    log_path = _get_update_log_path()

    async def _run_update_task():
        global _is_updating
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"=== Starting Panel Update [{time.strftime('%Y-%m-%d %H:%M:%S')}] ===\n")
                f.flush()

                # Determine best update command
                cmd = ["sudo", "bash", "/opt/srv-panel/scripts/get-update.sh"]
                if not Path("/opt/srv-panel/scripts/get-update.sh").exists():
                    local_script = Path(__file__).parent.parent.parent / "scripts" / "get-update.sh"
                    if local_script.exists():
                        cmd = ["bash", str(local_script)]

                f.write(f"Executing: {' '.join(cmd)}\n")
                f.flush()

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=f,
                    stderr=asyncio.subprocess.STDOUT,
                )
                await proc.wait()
                f.write(f"\n=== Update finished with code {proc.returncode} ===\n")
        except Exception as exc:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\nUpdate failed with error: {exc}\n")
        finally:
            _is_updating = False

    asyncio.create_task(_run_update_task())
    return {"status": "ok", "message": "Update process started in background."}


async def get_update_status() -> Dict[str, Any]:
    """Check update log status."""
    log_path = _get_update_log_path()
    logs = ""
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            logs = "\n".join(lines[-40:])  # last 40 lines
        except Exception as exc:
            logs = f"Error reading log: {exc}"

    return {
        "is_updating": _is_updating,
        "log": logs,
    }
