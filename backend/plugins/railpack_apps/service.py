"""Plugin state: disabling deployment controls intentionally leaves app containers running."""
from __future__ import annotations

import shutil


class RailpackAppsService:
    def is_installed(self) -> bool:
        if shutil.which("railpack") is None:
            return False
        try:
            from dependencies import dependency_manager
            docker = dependency_manager.get_service("docker")
            if docker is None or not docker.get_status(force=True).get("healthy"):
                return False
            result = docker._run(
                ["docker", "inspect", "--format", "{{.State.Running}}", "srv-panel-buildkit"],
                timeout=5, privileged=True,
            )
            return result.returncode == 0 and result.stdout.strip() == "true"
        except Exception:
            return False

    def pause(self) -> None:
        # This is a deployment plugin, not a runtime owner. Existing apps keep running.
        return None

    def resume(self) -> None:
        if not self.is_installed():
            raise RuntimeError("Railpack CLI is not installed.")


railpack_apps_service = RailpackAppsService()
