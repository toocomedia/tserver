"""Health and repair driver for the panel's core Git tools."""
from __future__ import annotations
import os, shutil, subprocess
from typing import Any

class GitDependencyService:
    dependency_id = "git"
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
    def get_status(self, *, force: bool = False): return self._probe()
    def get_cached_status(self): return self._probe()
    def install(self):
        if os.name == "nt": return False, "Git repair is only available on Linux."
        try:
            result = subprocess.run(["sudo", "-n", "apt-get", "install", "-y", "git", "openssh-client"], capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired: return False, "Git installation timed out."
        return (True, "Git & SSH are ready.") if result.returncode == 0 and self._probe()["healthy"] else (False, (result.stderr or result.stdout or "Git installation failed.")[-1000:])
    def get_install_guide(self): return {"supported": os.name != "nt", "command":"sudo apt-get install -y git openssh-client", "warning":"Git is required by panel update checks and hosted Git applications."}
    def get_uninstall_guide(self): return {"command":"Not available", "warning":"Git is a core panel runtime and cannot be removed here."}
