"""Multi-container runtime orchestration for Official Vendor Stacks."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import config
from models.container_app import ContainerApp
from services import container_app_service as apps
from services.official_stacks.schema import OfficialStackDefinition, ServiceDefinition


def stack_network_name(app_id: int) -> str:
    return f"srv-stack-net-{app_id}"


def stack_container_name(app_id: int, service_name: str) -> str:
    return f"srv-stack-{app_id}-{service_name}"


def stack_volume_name(app_id: int, suffix: str) -> str:
    return f"srv-stack-{app_id}-{suffix}"


def stack_config_dir(app_id: int) -> Path:
    return apps.root(app_id) / "stack_configs"


def ensure_stack_network(app_id: int) -> str:
    name = stack_network_name(app_id)
    inspect_res = apps._run(["docker", "network", "inspect", name], timeout=15)
    if inspect_res.returncode != 0:
        create_res = apps._run([
            "docker", "network", "create", "--driver", "bridge",
            "--label", "srv-panel.stack=true",
            "--label", f"srv-panel.app-id={app_id}",
            name,
        ], timeout=30)
        if create_res.returncode != 0:
            raise RuntimeError(f"Could not create stack network '{name}': {create_res.stderr or create_res.stdout}")
    return name


def ensure_stack_volumes(app_id: int, stack: OfficialStackDefinition) -> List[str]:
    created_volumes: List[str] = []
    for svc in stack.services.values():
        for vol_def in svc.volumes:
            vol_name = stack_volume_name(app_id, vol_def.name_suffix)
            res = apps._run([
                "docker", "volume", "create",
                "--label", "srv-panel.stack=true",
                "--label", f"srv-panel.app-id={app_id}",
                vol_name,
            ], timeout=30)
            if res.returncode != 0:
                raise RuntimeError(f"Could not create stack volume '{vol_name}': {res.stderr or res.stdout}")
            created_volumes.append(vol_name)
    return created_volumes


def materialize_stack_configs(app_id: int, stack: OfficialStackDefinition) -> Dict[str, Path]:
    cfg_dir = stack_config_dir(app_id)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    materialized: Dict[str, Path] = {}
    for svc in stack.services.values():
        for cfg in svc.config_files:
            dest_file = cfg_dir / cfg.filename
            if cfg.content:
                dest_file.write_text(cfg.content, encoding="utf-8")
            dest_file.chmod(0o644)
            materialized[cfg.filename] = dest_file
    return materialized


def pull_stack_images(stack: OfficialStackDefinition) -> Dict[str, str]:
    digests: Dict[str, str] = {}
    for svc_name, svc in stack.services.items():
        image_ref = svc.image_reference
        pull_res = apps._run(["docker", "pull", image_ref], timeout=300)
        if pull_res.returncode != 0 and "/" in image_ref and not image_ref.startswith(("ghcr.io/", "quay.io/", "gcr.io/", "docker.io/")):
            # Try ghcr.io fallback (e.g. ghcr.io/plausible/community-edition:v3.2.1 or ghcr.io/plausible/analytics:...)
            candidates = [
                f"ghcr.io/{image_ref}",
                f"ghcr.io/plausible/community-edition:{image_ref.split(':')[-1]}" if "plausible" in image_ref else None,
            ]
            for cand in candidates:
                if cand:
                    retry_res = apps._run(["docker", "pull", cand], timeout=300)
                    if retry_res.returncode == 0:
                        pull_res = retry_res
                        image_ref = cand
                        # Tag as requested image_ref locally so run_cmd finds it directly
                        apps._run(["docker", "tag", cand, svc.image_reference], timeout=20)
                        break
        if pull_res.returncode != 0:
            raise RuntimeError(f"Failed to pull image '{image_ref}' for service '{svc_name}': {pull_res.stderr or pull_res.stdout}")
        insp = apps._run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image_ref], timeout=20)
        digest = (insp.stdout or "").strip() or image_ref
        digests[svc_name] = digest
    return digests


def compile_stack_environment(
    app: ContainerApp,
    stack: OfficialStackDefinition,
    domain_name: str,
    vault_secrets: Dict[str, str],
    settings: Dict[str, str],
) -> Dict[str, str]:
    """Builds the complete runtime environment for any official vendor stack server-side."""
    env = dict(stack.default_environment)
    env.update(settings)
    env.setdefault("BASE_URL", f"https://{domain_name}")
    for key, val in vault_secrets.items():
        env[key] = val

    format_kwargs = {
        k: quote_plus(v) for k, v in vault_secrets.items()
    }
    for svc_key in stack.services:
        format_kwargs[svc_key] = stack_container_name(app.id, svc_key)

    for env_key, tmpl in stack.url_templates.items():
        try:
            env[env_key] = tmpl.format(**format_kwargs)
        except Exception:
            pass

    return env


def start_service_container(
    app_id: int,
    stack: OfficialStackDefinition,
    service_name: str,
    env_file: Path,
    host_port: Optional[int] = None,
) -> None:
    svc = stack.services.get(service_name)
    if svc is None:
        raise ValueError(f"Service '{service_name}' not defined in stack '{stack.catalog_id}'.")

    cname = stack_container_name(app_id, service_name)
    net_name = stack_network_name(app_id)

    # Remove any existing container with same name
    apps._run(["docker", "rm", "-f", cname], timeout=30)

    run_cmd = [
        "docker", "run", "-d",
        "--name", cname,
        "--network", net_name,
        "--network-alias", service_name,
        "--network-alias", cname,
        "--restart", "unless-stopped",
        "--memory", f"{svc.memory_limit_mb}m",
        "--cpus", str(svc.cpu_limit),
        "--label", "srv-panel.stack=true",
        "--label", f"srv-panel.app-id={app_id}",
        "--label", f"srv-panel.stack-service={service_name}",
        "--label", f"srv-panel.stack-catalog={stack.catalog_id}",
    ]

    # Universal network aliases so any container can discover databases and caches
    img_lower = (svc.image_reference or "").lower()
    sname_lower = service_name.lower()

    if any(k in img_lower or k in sname_lower for k in ("postgres", "pgsql", "psql")):
        for alias in ["postgres", "db", "database", "plausible_db"]:
            if alias != service_name:
                run_cmd.extend(["--network-alias", alias])
    elif any(k in img_lower or k in sname_lower for k in ("mysql", "mariadb")):
        for alias in ["mysql", "mariadb", "db", "database"]:
            if alias != service_name:
                run_cmd.extend(["--network-alias", alias])
    elif any(k in img_lower or k in sname_lower for k in ("redis", "keydb", "valkey")):
        for alias in ["redis", "cache"]:
            if alias != service_name:
                run_cmd.extend(["--network-alias", alias])
    elif any(k in img_lower or k in sname_lower for k in ("clickhouse", "influx", "timescale")):
        for alias in ["clickhouse", "events", "events_db", "plausible_events_db"]:
            if alias != service_name:
                run_cmd.extend(["--network-alias", alias])

    # Environment file
    if env_file and env_file.is_file():
        run_cmd.extend(["--env-file", str(env_file)])

    # Per-service environment overrides & sensible database fallbacks
    for k, v in svc.environment_defaults.items():
        run_cmd.extend(["-e", f"{k}={v}"])

    if ("postgres" in img_lower or "postgres" in sname_lower) and "POSTGRES_USER" not in svc.environment_defaults:
        run_cmd.extend(["-e", "POSTGRES_USER=postgres"])

    # Volume mounts
    for vol in svc.volumes:
        full_vol_name = stack_volume_name(app_id, vol.name_suffix)
        ro_flag = ":ro" if vol.read_only else ""
        run_cmd.extend(["-v", f"{full_vol_name}:{vol.container_mount_path}{ro_flag}"])

    # Config file mounts
    for cfg in svc.config_files:
        cfg_path = stack_config_dir(app_id) / cfg.filename
        if cfg_path.is_file():
            ro_flag = ":ro" if cfg.read_only else ""
            run_cmd.extend(["-v", f"{cfg_path}:{cfg.container_target_path}{ro_flag}"])

    # Port publishing (ONLY for web entrypoint and loopback 127.0.0.1)
    if host_port and (svc.is_web_entrypoint or service_name == stack.web_service_name):
        port_to_publish = stack.web_internal_port or (svc.internal_ports[0] if svc.internal_ports else 8000)
        run_cmd.extend(["-p", f"127.0.0.1:{host_port}:{port_to_publish}"])

    run_cmd.append(svc.image_reference)
    if svc.command:
        run_cmd.extend(svc.command)

    res = apps._run(run_cmd, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"Service container '{cname}' failed to start: {res.stderr or res.stdout}")


async def wait_service_health(
    app_id: int,
    stack: OfficialStackDefinition,
    service_name: str,
    host_port: Optional[int] = None,
) -> None:
    svc = stack.services.get(service_name)
    hc = svc.health_check if svc else None
    cname = stack_container_name(app_id, service_name)
    retries = hc.retries if hc else 20
    interval = hc.interval_seconds if hc else 4
    start_period = hc.start_period_seconds if hc else 20
    deadline = time.time() + (retries * interval) + start_period

    is_web = bool(host_port and svc and (svc.is_web_entrypoint or service_name == stack.web_service_name))

    if hc and hc.probe_type == "command" and hc.command and not is_web:
        last_error = ""
        raw_cmd = list(hc.command)
        # Replace localhost with 127.0.0.1 to avoid BusyBox IPv6 [::1] connection refused
        sanitized_cmd = [arg.replace("http://localhost:", "http://127.0.0.1:").replace("localhost", "127.0.0.1") for arg in raw_cmd]
        # If healthcheck is pg_isready with a non-postgres user or db name, ensure fallback to postgres user
        if "pg_isready" in sanitized_cmd and "-U" in sanitized_cmd:
            u_idx = sanitized_cmd.index("-U")
            if u_idx + 1 < len(sanitized_cmd) and sanitized_cmd[u_idx + 1] != "postgres":
                # Check if user wanted database name or user
                sanitized_cmd[u_idx + 1] = "postgres"

        has_shell_tokens = any(token in sanitized_cmd for token in ["||", "&&", "|", ";", ">", "<", "$"])
        if has_shell_tokens:
            exec_cmd = ["docker", "exec", cname, "sh", "-c", " ".join(sanitized_cmd)]
        else:
            exec_cmd = ["docker", "exec", cname, *sanitized_cmd]

        while time.time() < deadline:
            probe = apps._run(exec_cmd, timeout=hc.timeout_seconds)
            if probe.returncode == 0:
                break
            last_error = (probe.stderr or probe.stdout or f"exit code {probe.returncode}").strip()
            await asyncio.sleep(interval)
        else:
            log_res = apps._run(["docker", "logs", "--tail", "25", cname], timeout=10)
            c_logs = (log_res.stdout or log_res.stderr or "").strip()
            detail = f"\n[container logs]\n{c_logs}" if c_logs else ""
            raise RuntimeError(f"Service '{service_name}' failed health check ({hc.command}) within timeout: {last_error}{detail}")

    # For web entrypoint with host_port, always verify HTTP loopback responsiveness
    if is_web:
        from services import container_app_deployment_progress_service as progress
        probe_path = hc.http_path if (hc and hc.http_path) else (stack.web_health_path or "/api/health")
        wait_timeout = int(deadline - time.time()) if deadline > time.time() else 45
        await progress.wait_for_http(
            host_port,
            path=probe_path,
            timeout_seconds=max(wait_timeout, 30),
        )


def stop_stack(app_id: int, stack: OfficialStackDefinition) -> None:
    for svc_name in reversed(stack.startup_order):
        cname = stack_container_name(app_id, svc_name)
        apps._run(["docker", "stop", "--time", "15", cname], timeout=25)


def remove_stack_containers(app_id: int, stack: OfficialStackDefinition) -> None:
    for svc_name in stack.services:
        cname = stack_container_name(app_id, svc_name)
        apps._run(["docker", "rm", "-f", cname], timeout=20)
    net_name = stack_network_name(app_id)
    apps._run(["docker", "network", "rm", net_name], timeout=15)


def purge_stack_volumes(app_id: int, stack: OfficialStackDefinition) -> None:
    for svc in stack.services.values():
        for vol in svc.volumes:
            vname = stack_volume_name(app_id, vol.name_suffix)
            apps._run(["docker", "volume", "rm", "-f", vname], timeout=15)


def inspect_stack_services(app_id: int, stack: OfficialStackDefinition) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for svc_name, svc in stack.services.items():
        cname = stack_container_name(app_id, svc_name)
        insp = apps._run(["docker", "inspect", "--format", "{{.State.Status}}", cname], timeout=10)
        status = (insp.stdout or "stopped").strip().lower() if insp.returncode == 0 else "stopped"
        results[svc_name] = {
            "service_name": svc_name,
            "container_name": cname,
            "status": status,
            "is_running": status == "running",
            "is_web": svc.is_web_entrypoint,
            "image": svc.image_reference,
        }
    return results
