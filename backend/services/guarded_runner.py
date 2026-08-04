"""
Approved execution helpers for Resource Guard.

All panel-owned heavy commands and Docker containers must go through
one of these two helpers so profile limits are always applied.

  run_native(command, profile, label, cancel_fn)
      Wraps a host command in a systemd-run --scope if systemd is available,
      applying MemoryHigh, MemoryMax, CPUQuota, TasksMax, and a timeout.
      Falls back to a plain subprocess when systemd scope is not supported.

  run_docker(image, profile, label, env_file, ...)
      Wraps docker run with --memory, --memory-swap, --cpus, --pids-limit,
      and panel ownership labels derived from the profile.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from typing import Callable

from services.resource_guard_profiles import PROFILES

logger = logging.getLogger(__name__)

_SYSTEMD_RUN = shutil.which("systemd-run")


class LifecycleAdapter:
    """
    Base interface for plugin/dependency lifecycle adapters.

    A panel-owned adapter for a given service must implement these methods.
    Safe Install Mode will only offer a service as a stop candidate when its
    adapter is registered and all four methods are present.
    """

    async def stop(self) -> None:
        """Gracefully stop the service. Raises on failure."""
        raise NotImplementedError

    async def start(self) -> None:
        """Start the service and wait until it is ready. Raises on failure."""
        raise NotImplementedError

    async def is_running(self) -> bool:
        """Return True if the service is currently running."""
        raise NotImplementedError

    async def current_ram_mb(self) -> int:
        """Return current RAM usage in MB (best-effort)."""
        return 0


def _profile(name: str) -> dict:
    return PROFILES.get(name, PROFILES["native_light"])


def _systemd_scope_available() -> bool:
    """Return True if systemd-run --scope is usable on this host."""
    if _SYSTEMD_RUN is None or os.name == "nt":
        return False
    try:
        result = subprocess.run(
            ["systemd-run", "--scope", "--help"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


_SCOPE_AVAILABLE: bool | None = None


def _use_scope() -> bool:
    global _SCOPE_AVAILABLE
    if _SCOPE_AVAILABLE is None:
        _SCOPE_AVAILABLE = _systemd_scope_available()
    return _SCOPE_AVAILABLE


async def run_native(
    command: list[str],
    profile: str = "native_light",
    label: str = "panel-command",
    cancel_fn: Callable[[], None] | None = None,
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = True,
) -> tuple[int, str, str]:
    """
    Run *command* with limits derived from *profile*.

    Returns (returncode, stdout, stderr).

    When systemd-run --scope is available the command is wrapped in a transient
    unit with MemoryMax, CPUQuota, TasksMax, and a runtime timeout.
    When it is not available (e.g. container host, Windows dev), the command
    runs directly via subprocess.
    """
    prof = _profile(profile)
    timeout = prof.get("timeout") or 600
    ram_mb = prof.get("ram_mb", 50)
    cpu = prof.get("cpu", "0.25")

    # Build the safe unit name from the label (systemd unit name rules)
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:60]

    if _use_scope() and os.name != "nt":
        cpu_quota = f"{int(float(cpu) * 100)}%"
        wrapped = [
            "systemd-run",
            "--scope",
            f"--unit=srv-panel-{safe_label}",
            f"--property=MemoryMax={ram_mb}M",
            f"--property=MemoryHigh={int(ram_mb * 0.85)}M",
            f"--property=CPUQuota={cpu_quota}",
            "--property=TasksMax=256",
            "--",
            *command,
        ]
    else:
        wrapped = command

    proc = await asyncio.create_subprocess_exec(
        *wrapped,
        stdout=asyncio.subprocess.PIPE if capture_output else None,
        stderr=asyncio.subprocess.PIPE if capture_output else None,
        env={**os.environ, **(env or {})},
    )

    async def _cancel():
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    if cancel_fn is not None:
        # Wrap cancel_fn so it also terminates the subprocess
        original_cancel = cancel_fn
        def combined_cancel():
            original_cancel()
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(_cancel())
            )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        await _cancel()
        raise RuntimeError(f"Command timed out after {timeout}s: {command[0]}")

    stdout = (stdout_bytes or b"").decode(errors="replace")
    stderr = (stderr_bytes or b"").decode(errors="replace")
    return proc.returncode or 0, stdout, stderr


def build_docker_run_args(
    image: str,
    profile: str,
    label: str,
    *,
    container_name: str | None = None,
    env_file: str | None = None,
    extra_labels: dict[str, str] | None = None,
    ports: list[str] | None = None,
    volumes: list[str] | None = None,
    network: str | None = None,
    detach: bool = True,
    remove: bool = False,
    extra_args: list[str] | None = None,
) -> list[str]:
    """
    Build a `docker run` command list with profile limits applied.

    Callers use this to build the command, then execute it via run_native()
    or directly so the limits are always present.
    """
    prof = _profile(profile)
    ram_mb = prof.get("ram_mb", 128)
    cpu = prof.get("cpu", "0.5")

    # Enforce a deliberate swap policy: allow swap = 0 for builds/native,
    # allow a small multiple for runtime containers.
    is_runtime = profile.startswith("container_") or profile.startswith("database_")
    swap_mb = ram_mb * 2 if is_runtime else ram_mb

    args: list[str] = ["docker", "run"]

    if detach:
        args.append("-d")
    if remove:
        args.append("--rm")
    if container_name:
        args += ["--name", container_name]

    args += [
        f"--memory={ram_mb}m",
        f"--memory-swap={swap_mb}m",
        f"--cpus={cpu}",
        "--pids-limit=256",
        # Panel ownership labels
        "--label=managed-by=srv-panel",
        f"--label=srv-panel-profile={profile}",
        f"--label=srv-panel-label={label}",
    ]

    for k, v in (extra_labels or {}).items():
        args.append(f"--label={k}={v}")

    if env_file:
        args += ["--env-file", env_file]

    for port in (ports or []):
        args += ["-p", port]

    for vol in (volumes or []):
        args += ["-v", vol]

    if network:
        args += ["--network", network]

    if extra_args:
        args += extra_args

    args.append(image)
    return args
