"""Health driver for the panel and hosted-app Python runtime."""
from __future__ import annotations
import os, re, shutil, subprocess, threading, time
from typing import Any

from services.platform_support_service import platform_support_service

class PythonDependencyService:
    dependency_id = "python"
    CACHE_SECONDS = 300.0
    def __init__(self):
        self._cache = None
        self._cache_at = 0.0
        self._cache_lock = threading.Lock()
    def _python(self):
        for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
            if path := shutil.which(name): return path
        return None
    def _probe(self) -> dict[str, Any]:
        command = self._python()
        version = None; error = None
        if command:
            try:
                result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=3)
                version = (result.stdout or result.stderr).strip()
                match = re.search(r"(\d+)\.(\d+)", version)
                if not match or (int(match.group(1)), int(match.group(2))) < (3, 11): error = "Hosted apps require Python 3.11 or newer."
                elif subprocess.run([command, "-c", "import venv, pip"], capture_output=True, timeout=3).returncode: error = "Python venv or pip is unavailable."
            except (OSError, subprocess.TimeoutExpired): error = "Python health check failed."
        else: error = "Python 3.11 or newer is required."
        return {"id":self.dependency_id,"installed":bool(command),"running":False,"can_toggle":False,"healthy":bool(command and not error),"state":"healthy" if command and not error else "not_installed","detected_version":version,"error":error}
    def get_status(self, *, force: bool = False):
        now = time.monotonic()
        with self._cache_lock:
            if not force and self._cache is not None and now - self._cache_at < self.CACHE_SECONDS:
                return dict(self._cache)
            self._cache = self._probe()
            self._cache_at = now
            return dict(self._cache)
    def get_cached_status(self):
        with self._cache_lock:
            if self._cache is not None:
                return dict(self._cache)
        installed = self._python() is not None
        return {"id": self.dependency_id, "installed": installed, "running": False,
                "can_toggle": False, "healthy": False, "state": "unknown" if installed else "not_installed",
                "detected_version": None, "error": None}
    def install(self): return False, "Python is installed with SRV Panel. Run the panel installer/update to repair this core runtime."
    def get_install_guide(self):
        info = platform_support_service.get()
        reason = "Python is installed or repaired by the SRV Panel installer/update."
        if info["selector"] == "ubuntu:22.04":
            reason = "Native hosted Python apps require Python 3.11 or newer; Ubuntu 22.04 provides Python 3.10 by default."
        return {"supported":False,"platform":info["pretty_name"],"unsupported_reason":reason,"command":"sudo bash /opt/srv-panel/scripts/update.sh","warning":"Do not remove Python: SRV Panel and hosted apps use it."}
    def get_uninstall_guide(self): return {"command":"Not available","warning":"Python is a core panel runtime and cannot be removed here."}
