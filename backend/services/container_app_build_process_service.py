"""Cancellable subprocesses for Apps Engine image pulls and builds."""
from __future__ import annotations

import os
import subprocess
import threading

import config
from services.apps_engine import build_workspace

_lock = threading.Lock()
_processes: dict[int, subprocess.Popen[str]] = {}
_cancelled: set[int] = set()


class BuildCancelled(RuntimeError):
    pass


def run(deployment_id: int, command: list[str], timeout: int, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    with _lock:
        if deployment_id in _cancelled:
            raise BuildCancelled("Resource Guard cancelled this deployment to protect VPS memory.")
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    if command and command[0] == "railpack":
        proc_env["BUILDKIT_HOST"] = "docker-container://srv-panel-buildkit"
        workspace = build_workspace.prepare(deployment_id)
        proc_env["TMPDIR"] = str(workspace.temporary)
        proc_env["TEMP"] = str(workspace.temporary)
        proc_env["TMP"] = str(workspace.temporary)
        proc_env["XDG_RUNTIME_DIR"] = str(workspace.temporary)
        proc_env["XDG_CACHE_HOME"] = str(workspace.cache)
        proc_env["XDG_DATA_HOME"] = str(workspace.cache / "data")
        proc_env["XDG_STATE_HOME"] = str(workspace.cache / "state")
        proc_env["XDG_CONFIG_HOME"] = str(workspace.cache / "config")
        prefix = []
    else:
        prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    process = subprocess.Popen([*prefix, *command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=proc_env)
    with _lock:
        _processes[deployment_id] = process
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate(process)
        stdout, stderr = process.communicate()
        raise
    finally:
        with _lock:
            _processes.pop(deployment_id, None)
            cancelled = deployment_id in _cancelled
            _cancelled.discard(deployment_id)
    if cancelled:
        raise BuildCancelled("Resource Guard cancelled this deployment to protect VPS memory.")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def cancel(deployment_id: int) -> None:
    with _lock:
        _cancelled.add(deployment_id)
        process = _processes.get(deployment_id)
    if process is not None and process.poll() is None:
        _terminate(process)


def _terminate(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
