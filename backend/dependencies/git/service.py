"""Health and repair driver for the panel's core Git tools."""
from __future__ import annotations
import os, shutil, subprocess, threading, time
from typing import Any

from services.platform_support_service import platform_support_service

class GitDependencyService:
    dependency_id = "git"
    CACHE_SECONDS = 300.0
    def __init__(self):
        self._cache = None
        self._cache_at = 0.0
        self._cache_lock = threading.Lock()
    def _probe(self) -> dict[str, Any]:
        git, ssh = shutil.which("git"), shutil.which("ssh")
        version = None
        if git:
            try: version = subprocess.run([git, "--version"], capture_output=True, text=True, timeout=3).stdout.strip()
            except (OSError, subprocess.TimeoutExpired): pass
        installed = bool(git and ssh)
        return {"id": self.dependency_id, "installed": installed, "running": False,
                "can_toggle": False, "healthy": installed, "state": "healthy" if installed else "not_installed",
                "detected_version": version, "error": None if installed else "Git and OpenSSH client are required."}
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
        installed = bool(shutil.which("git") and shutil.which("ssh"))
        return {"id": self.dependency_id, "installed": installed, "running": False,
                "can_toggle": False, "healthy": False, "state": "unknown" if installed else "not_installed",
                "detected_version": None, "error": None}
    def install(self):
        platform_error = platform_support_service.capability_error("core")
        if platform_error: return False, platform_error
        try:
            result = subprocess.run(["sudo", "-n", "apt-get", "install", "-y", "git", "openssh-client"], capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired: return False, "Git installation timed out."
        return (True, "Git & SSH are ready.") if result.returncode == 0 and self._probe()["healthy"] else (False, (result.stderr or result.stdout or "Git installation failed.")[-1000:])
    def get_install_guide(self): return platform_support_service.install_guide("core", "sudo apt-get install -y git openssh-client", "Git is required by panel update checks and hosted Git applications.")
    def get_uninstall_guide(self): return {"command":"Not available", "warning":"Git is a core panel runtime and cannot be removed here."}
