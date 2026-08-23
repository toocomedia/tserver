"""Cancellable subprocesses for Apps Engine image pulls and builds."""
from __future__ import annotations

import os
import subprocess
import threading

import config
from services.apps_engine import build_workspace
from services.apps_engine import build_secrets

import time

_lock = threading.Lock()
_processes: dict[int, subprocess.Popen[str]] = {}
_live_output: dict[int, list[str]] = {}
_cancelled: set[int] = set()


class BuildCancelled(RuntimeError):
    pass


def get_live_output(deployment_id: int) -> str:
    with _lock:
        chunks = _live_output.get(deployment_id)
        if chunks:
            return "".join(chunks)[-80_000:]
    return ""


def run(deployment_id: int, command: list[str], timeout: int, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    with _lock:
        if deployment_id in _cancelled:
            raise BuildCancelled("Resource Guard cancelled this deployment to protect VPS memory.")
        _live_output[deployment_id] = []

    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    sensitive_values = [
        value for key, value in (env or {}).items()
        if value and (key in build_secrets.LEGACY_SECRET_NAMES or key.endswith(build_secrets.LEGACY_SECRET_SUFFIXES))
    ]

    def redact(line: str) -> str:
        for value in sensitive_values:
            line = line.replace(value, "[REDACTED]")
        return line
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
    prefix = ["sudo", "-n"] if hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO else []
    process = subprocess.Popen(
        [*prefix, *command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=proc_env,
    )
    with _lock:
        _processes[deployment_id] = process

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _read_stream(stream, chunks_list):
        try:
            for line in iter(stream.readline, ""):
                line = redact(line)
                chunks_list.append(line)
                with _lock:
                    if deployment_id in _live_output:
                        _live_output[deployment_id].append(line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_read_stream, args=(process.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=_read_stream, args=(process.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    start_time = time.time()
    try:
        while True:
            ret = process.poll()
            if ret is not None:
                break
            if time.time() - start_time > timeout:
                _terminate(process)
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.1)
        t_out.join(timeout=2)
        t_err.join(timeout=2)
    finally:
        with _lock:
            _processes.pop(deployment_id, None)
            _live_output.pop(deployment_id, None)
            cancelled = deployment_id in _cancelled
            _cancelled.discard(deployment_id)
    if cancelled:
        raise BuildCancelled("Resource Guard cancelled this deployment to protect VPS memory.")
    return subprocess.CompletedProcess(command, process.returncode, "".join(stdout_chunks), "".join(stderr_chunks))


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
