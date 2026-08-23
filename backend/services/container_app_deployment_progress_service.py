"""Persisted status and output helpers for container app deployments."""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from models.container_app import ContainerApp
from models.container_app_deployment import ContainerAppDeployment
from services import container_app_service as apps


async def stage(db: AsyncSession, deployment: ContainerAppDeployment, name: str, message: str) -> None:
    deployment.stage = name
    append_log(deployment, name, message)
    await db.commit()


def append_log(deployment: ContainerAppDeployment, stage_name: str, message: str) -> None:
    deployment.output = (deployment.output + f"[{stage_name}] {message}\n")[-80_000:]


def container_logs(app: ContainerApp) -> str:
    result = apps._run(["docker", "logs", "--tail", "120", app.container_name], timeout=20)
    if result.returncode:
        return ""
    output = (result.stdout + result.stderr).strip()
    return f"\n[runtime logs]\n{output}\n" if output else ""


def runtime_error_summary(app: ContainerApp) -> str:
    logs = container_logs(app).lower()
    if "password authentication failed" in logs:
        return "Database password rejected. Rotate credentials, then use Redeploy."
    return "App did not start its private HTTP service. Check runtime logs."


async def wait_for_http(port: int, path: str = "/", timeout_seconds: int = 45, host_header: str = "localhost") -> None:
    health_path = path if (path and path.startswith("/")) else f"/{path or ''}"
    request_data = f"GET {health_path} HTTP/1.1\r\nHost: {host_header or 'localhost'}\r\nConnection: close\r\n\r\n".encode("utf-8")
    deadline = asyncio.get_event_loop().time() + max(10, timeout_seconds)
    last_status_code: int | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=1.5)
            writer.write(request_data)
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            writer.close()
            await writer.wait_closed()
            if line.startswith(b"HTTP/"):
                parts = line.split()
                if len(parts) >= 2:
                    code = int(parts[1])
                    last_status_code = code
                    if 200 <= code < 400:
                        return
        except (OSError, asyncio.TimeoutError, ValueError, IndexError):
            pass
        await asyncio.sleep(1)
    if last_status_code is not None:
        if last_status_code == 404:
            raise RuntimeError(f"Container returned HTTP 404 Not Found on verified path '{health_path}' within {timeout_seconds}s. Review source or vendor evidence before changing it.")
        raise RuntimeError(f"Container returned HTTP {last_status_code} on '{health_path}' within {timeout_seconds}s (expected 2xx/3xx). Check application logs.")
    raise RuntimeError(f"Container did not return a healthy HTTP response on {health_path} within {timeout_seconds}s.")
