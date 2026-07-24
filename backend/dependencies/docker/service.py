"""Safe Docker detection, status, and service-control driver."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import config


class DockerDependencyService:
    dependency_id = "docker"
    CACHE_SECONDS = 5.0
    COMMAND_TIMEOUT = 2.0

    def __init__(self) -> None:
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0
        self._cache_lock = threading.Lock()

    @staticmethod
    def _is_linux() -> bool:
        return os.name != "nt"

    @staticmethod
    def _command_prefix() -> list[str]:
        if os.name == "nt":
            return []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return []
        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(
        self,
        command: list[str],
        *,
        timeout: float | None = None,
        privileged: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [*self._command_prefix(), *command] if privileged else command
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or self.COMMAND_TIMEOUT,
            check=False,
            shell=False,
        )

    def is_installed(self) -> bool:
        return shutil.which("docker") is not None

    def _probe(self) -> dict[str, Any]:
        installed = self.is_installed()
        version = None
        running = False
        error = None

        if installed:
            try:
                version_result = self._run(["docker", "--version"], privileged=True)
                if version_result.returncode == 0:
                    version = version_result.stdout.strip()

                info_result = self._run(
                    ["docker", "info", "--format", "{{json .ServerVersion}}"],
                    privileged=True,
                )
                running = info_result.returncode == 0
                if not running:
                    error = (
                        info_result.stderr.strip()
                        or info_result.stdout.strip()
                        or "Docker daemon did not answer."
                    )
            except subprocess.TimeoutExpired:
                error = "Docker status check timed out."
            except OSError as exc:
                error = str(exc)

        state = "not_installed" if not installed else ("healthy" if running else "stopped")
        return {
            "id": self.dependency_id,
            "installed": installed,
            "running": running,
            "healthy": installed and running,
            "state": state,
            "detected_version": version,
            "error": error,
            "checked_at": time.time(),
        }

    def get_status(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        with self._cache_lock:
            if (
                not force
                and self._cache is not None
                and now - self._cache_at < self.CACHE_SECONDS
            ):
                return dict(self._cache)
            self._cache = self._probe()
            self._cache_at = now
            return dict(self._cache)

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_at = 0.0

    def toggle(self, enable: bool) -> tuple[bool, str]:
        if not self._is_linux():
            return False, "Docker service control is only available on Linux."

        commands = (
            [["systemctl", "enable", "--now", "docker.service", "docker.socket"]]
            if enable
            else [
                ["systemctl", "disable", "--now", "docker.socket"],
                ["systemctl", "disable", "--now", "docker.service"],
            ]
        )
        command_errors = []
        for command in commands:
            try:
                result = self._run(command, timeout=15, privileged=True)
            except subprocess.TimeoutExpired:
                command_errors.append(f"{' '.join(command)} timed out.")
                continue
            if result.returncode != 0:
                command_errors.append(
                    result.stderr.strip()
                    or result.stdout.strip()
                    or f"{' '.join(command)} failed."
                )

        self.invalidate()
        status = self.get_status(force=True)
        if enable and not status["healthy"]:
            return False, status["error"] or "Docker did not become healthy."
        if not enable and status["running"]:
            detail = "; ".join(command_errors)
            message = "Docker is still running after the stop request."
            return False, f"{message} {detail}".strip()
        if enable and command_errors:
            return False, "; ".join(command_errors)
        return True, "Docker enabled." if enable else "Docker disabled."

    @staticmethod
    def _installer_path() -> Path:
        deployed = Path("/opt/srv-panel/scripts/install_docker.sh")
        if deployed.is_file():
            return deployed
        return Path(__file__).resolve().parents[3] / "scripts" / "install_docker.sh"

    def install(self) -> tuple[bool, str]:
        if not self._is_linux():
            return False, "Docker installation is only available on supported Ubuntu servers."
        installer = self._installer_path().resolve()
        if not installer.is_file():
            return False, "Docker installer script is missing. Run the panel updater first."

        try:
            result = self._run(
                ["bash", str(installer)],
                timeout=900,
                privileged=True,
            )
        except subprocess.TimeoutExpired:
            return False, "Docker installation timed out after 15 minutes."
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Docker installation failed."
            if "password is required" in message.lower():
                message = (
                    "Docker installer permission is missing. Run: "
                    "sudo bash /opt/srv-panel/scripts/update.sh"
                )
            return False, message[-2000:]

        self.invalidate()
        status = self.get_status(force=True)
        if not status["healthy"]:
            return False, status["error"] or "Docker installed but the daemon is not healthy."
        return True, result.stdout.strip()[-2000:] or "Docker installed successfully."

    @staticmethod
    def _os_release() -> dict[str, str]:
        values: dict[str, str] = {}
        path = Path("/etc/os-release")
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        return values

    def get_install_guide(self) -> dict[str, Any]:
        release = self._os_release()
        supported = release.get("ID") == "ubuntu" and release.get("VERSION_ID") in {
            "22.04",
            "24.04",
        }
        return {
            "supported": supported,
            "platform": release.get("PRETTY_NAME") or platform.platform(),
            "command": "sudo bash /opt/srv-panel/scripts/install_docker.sh",
            "warning": "The panel installer uses Docker's official Ubuntu apt repository.",
        }

    def get_uninstall_guide(self) -> dict[str, Any]:
        return {
            "command": (
                "sudo systemctl stop docker.service docker.socket\n"
                "sudo apt-get purge docker-ce docker-ce-cli containerd.io "
                "docker-buildx-plugin docker-compose-plugin"
            ),
            "data_path": "/var/lib/docker",
            "warning": (
                "Docker data is preserved. Deleting /var/lib/docker is a separate, "
                "permanent action and is never performed by SRV Panel."
            ),
        }

    def list_containers(self) -> list[dict[str, Any]]:
        if not self.get_status()["healthy"]:
            return []
        try:
            result = self._run(
                ["docker", "ps", "-a", "--format", "{{json .}}"],
                privileged=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []

        containers: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            labels = str(data.get("Labels", ""))
            containers.append(
                {
                    "id": data.get("ID"),
                    "name": data.get("Names"),
                    "panel_managed": "srv-panel.plugin=" in labels,
                }
            )
        return containers

    @staticmethod
    def _parse_size_bytes(value: str) -> int:
        match = re.fullmatch(
            r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)\s*",
            value,
            re.IGNORECASE,
        )
        if not match:
            return 0
        amount = float(match.group(1))
        unit = match.group(2).lower()
        powers = {
            "b": 0,
            "kb": 1,
            "kib": 1,
            "mb": 2,
            "mib": 2,
            "gb": 3,
            "gib": 3,
            "tb": 4,
            "tib": 4,
        }
        base = 1024 if "i" in unit else 1000
        return int(amount * (base ** powers[unit]))

    @staticmethod
    def _parse_percent(value: str) -> float:
        try:
            return float(value.strip().removesuffix("%"))
        except (AttributeError, ValueError):
            return 0.0

    @staticmethod
    def _plugin_label(labels: str) -> str | None:
        for item in labels.split(","):
            key, separator, value = item.strip().partition("=")
            if separator and key == "srv-panel.plugin":
                return value
        return None

    def get_plugin_usage(
        self, plugin_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Aggregate live Docker stats by the required plugin ownership label."""
        requested = {
            plugin_id
            for plugin_id in plugin_ids
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", plugin_id)
        }
        if not requested or not self.get_status()["healthy"]:
            return {}

        try:
            listed = self._run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "label=srv-panel.plugin",
                    "--format",
                    "{{json .}}",
                ],
                timeout=10,
                privileged=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if listed.returncode != 0:
            return {}

        rows: dict[str, dict[str, Any]] = {}
        container_plugins: dict[str, str] = {}
        running_ids: list[str] = []
        for line in listed.stdout.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            plugin_id = self._plugin_label(str(data.get("Labels", "")))
            container_id = str(data.get("ID", ""))
            if (
                plugin_id not in requested
                or not re.fullmatch(r"[a-f0-9]{6,64}", container_id)
            ):
                continue

            row = rows.setdefault(
                plugin_id,
                {
                    "cpu": 0.0,
                    "memory_bytes": 0,
                    "memory_limit_bytes": 0,
                    "count": 0,
                    "containers": 0,
                    "running_containers": 0,
                    "status": "stopped",
                },
            )
            row["containers"] += 1
            state = str(data.get("State", "")).lower()
            container_status = str(data.get("Status", "")).lower()
            if state != "running":
                continue

            row["running_containers"] += 1
            row["status"] = (
                "unhealthy"
                if "(unhealthy)" in container_status
                else (
                    "starting"
                    if "health: starting" in container_status
                    else "running"
                )
            )
            container_plugins[container_id] = plugin_id
            running_ids.append(container_id)

        if not running_ids:
            return rows

        try:
            stats = self._run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    *running_ids,
                ],
                timeout=15,
                privileged=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return rows
        if stats.returncode != 0:
            return rows

        for line in stats.stdout.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            container_id = str(data.get("ID") or data.get("Container") or "")
            plugin_id = next(
                (
                    owner
                    for known_id, owner in container_plugins.items()
                    if known_id.startswith(container_id)
                    or container_id.startswith(known_id)
                ),
                None,
            )
            if plugin_id is None:
                continue

            used_text, separator, limit_text = str(
                data.get("MemUsage", "")
            ).partition("/")
            row = rows[plugin_id]
            row["cpu"] += self._parse_percent(str(data.get("CPUPerc", "")))
            row["memory_bytes"] += self._parse_size_bytes(used_text)
            if separator:
                row["memory_limit_bytes"] += self._parse_size_bytes(limit_text)
            try:
                row["count"] += int(data.get("PIDs") or 0)
            except (TypeError, ValueError):
                pass

        for row in rows.values():
            row["cpu"] = round(row["cpu"], 1)
        return rows

    def cleanup_plugin_resources(
        self,
        plugin_id: str,
        *,
        purge_data: bool = False,
    ) -> tuple[bool, str]:
        """Remove only resources carrying the plugin ownership label."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", plugin_id):
            return False, "Invalid plugin ID for Docker cleanup."
        if not self.get_status(force=True)["healthy"]:
            return False, "Docker must be healthy before owned resources can be removed safely."

        label = f"srv-panel.plugin={plugin_id}"
        resource_commands = [
            (
                ["docker", "ps", "-aq", "--filter", f"label={label}"],
                ["docker", "rm", "-f"],
                "containers",
            ),
            (
                ["docker", "network", "ls", "-q", "--filter", f"label={label}"],
                ["docker", "network", "rm"],
                "networks",
            ),
        ]
        if purge_data:
            resource_commands.append(
                (
                    ["docker", "volume", "ls", "-q", "--filter", f"label={label}"],
                    ["docker", "volume", "rm"],
                    "volumes",
                )
            )

        removed: dict[str, int] = {}
        for list_command, remove_command, resource_type in resource_commands:
            try:
                listed = self._run(list_command, timeout=10, privileged=True)
            except subprocess.TimeoutExpired:
                return False, f"Listing owned Docker {resource_type} timed out."
            if listed.returncode != 0:
                return False, (
                    listed.stderr.strip()
                    or f"Could not list owned Docker {resource_type}."
                )
            resource_ids = [item for item in listed.stdout.split() if item]
            if not resource_ids:
                removed[resource_type] = 0
                continue
            try:
                result = self._run(
                    [*remove_command, *resource_ids], timeout=60, privileged=True
                )
            except subprocess.TimeoutExpired:
                return False, f"Removing owned Docker {resource_type} timed out."
            if result.returncode != 0:
                return False, (
                    result.stderr.strip()
                    or f"Could not remove owned Docker {resource_type}."
                )
            removed[resource_type] = len(resource_ids)

        detail = ", ".join(f"{count} {name}" for name, count in removed.items())
        if not purge_data:
            detail = f"{detail}; volumes preserved"
        return True, detail
