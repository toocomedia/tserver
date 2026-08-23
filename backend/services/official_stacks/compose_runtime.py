"""Panel-owned Docker Compose rendering and lifecycle for approved App Engine stacks."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from models.container_app import ContainerApp
from services import container_app_service as apps
from services.official_stacks.catalog import get_stack
from services.official_stacks.manifest_validator import validate_stack_manifest
from services.official_stacks.schema import OfficialStackDefinition, ServiceDefinition, stack_from_dict, stack_to_dict


def project_name(app_id: int) -> str:
    return f"srv-stack-{app_id}"


def compose_path(app_id: int) -> Path:
    return apps.root(app_id) / "stack.compose.json"


def environment_dir(app_id: int) -> Path:
    return apps.root(app_id) / "stack_env"


def manifest_json(stack: OfficialStackDefinition) -> str:
    validate_stack_manifest(stack)
    return json.dumps(stack_to_dict(stack), sort_keys=True, separators=(",", ":"))


def stack_from_runtime(app: ContainerApp | Any) -> OfficialStackDefinition:
    """Prefer the persisted snapshot manifest; retain a read-only legacy fallback."""
    raw = getattr(app, "stack_services", None)
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            stack = stack_from_dict(data)
            return validate_stack_manifest(stack)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Saved stack manifest is invalid: {exc}") from exc
    from services.official_stacks import stack_runtime_service
    legacy = stack_runtime_service.load_app_stack_manifest(int(app.id))
    if legacy is not None:
        return validate_stack_manifest(legacy)
    catalog_id = str(getattr(app, "stack_catalog_id", "") or "")
    stack = get_stack(catalog_id)
    if stack is None:
        raise RuntimeError("Stack manifest is missing. Use Repair; no containers or volumes were removed.")
    return validate_stack_manifest(stack)


def resolved_images(stack: OfficialStackDefinition) -> OfficialStackDefinition:
    """Pull each trusted image and replace its tag with Docker's immutable digest."""
    services: dict[str, ServiceDefinition] = {}
    for name, service in stack.services.items():
        image = service.pinned_digest or service.image_reference
        pull = apps._run(["docker", "pull", image], timeout=300)
        if pull.returncode:
            raise RuntimeError(f"Could not pull '{image}' for '{name}': {(pull.stderr or pull.stdout)[-1000:]}")
        inspect = apps._run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image], timeout=20)
        digest = (inspect.stdout or "").strip()
        if not digest or "@sha256:" not in digest:
            raise RuntimeError(f"Docker did not return an immutable digest for '{image}'.")
        services[name] = replace(service, image_reference=digest, pinned_digest=digest)
    pinned = replace(stack, services=services)
    return validate_stack_manifest(pinned)


def service_environments(
    app: ContainerApp | Any,
    stack: OfficialStackDefinition,
    domain_name: str,
    vault_secrets: dict[str, str],
    settings: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Build isolated service environments; secret values never share an app-wide env file."""
    environments = {name: dict(service.environment_defaults) for name, service in stack.services.items()}
    web_env = environments[stack.web_service_name]
    web_env.setdefault("BASE_URL", f"https://{domain_name}")
    web_env.update(stack.default_environment)
    web_env.update(settings)
    for requirement in stack.required_secrets:
        if requirement.key not in vault_secrets:
            raise RuntimeError(f"Generated secret '{requirement.key}' is missing.")
        target = requirement.service_name or stack.web_service_name
        env_key = requirement.environment_key or requirement.key
        environments[target][env_key] = vault_secrets[requirement.key]
    values = {key: quote_plus(value) for key, value in vault_secrets.items()}
    values.update({name: name for name in stack.services})
    for env_key, template in stack.url_templates.items():
        try:
            web_env[env_key] = template.format(**values)
        except (KeyError, ValueError) as exc:
            raise RuntimeError(f"Could not render stack environment '{env_key}'.") from exc
    return environments


def render_compose(
    app: ContainerApp | Any,
    stack: OfficialStackDefinition,
    environments: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Render JSON, a valid YAML subset, so no YAML parser executes untrusted source."""
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}
    network = f"srv-stack-net-{app.id}"
    env_root = environment_dir(int(app.id))
    for name, service in stack.services.items():
        env_file = env_root / f"{name}.env"
        spec: dict[str, Any] = {
            "container_name": f"srv-stack-{app.id}-{name}",
            "image": service.pinned_digest or service.image_reference,
            "restart": "unless-stopped",
            "env_file": [str(env_file)],
            "networks": [network],
            "mem_limit": f"{service.memory_limit_mb}m",
            "cpus": str(service.cpu_limit),
            "pids_limit": 256,
            "security_opt": ["no-new-privileges:true"],
            "labels": {
                "srv-panel.stack": "true",
                "srv-panel.app-id": str(app.id),
                "srv-panel.stack-service": name,
                "srv-panel.stack-catalog": stack.catalog_id,
            },
        }
        if service.command:
            spec["command"] = list(service.command)
        if service.depends_on:
            spec["depends_on"] = {
                dependency: {"condition": "service_healthy" if stack.services[dependency].health_check else "service_started"}
                for dependency in service.depends_on
            }
        if service.volumes:
            spec["volumes"] = []
            for volume in service.volumes:
                full_name = f"srv-stack-{app.id}-{volume.name_suffix}"
                spec["volumes"].append(f"{full_name}:{volume.container_mount_path}{':ro' if volume.read_only else ''}")
                volumes[full_name] = {"name": full_name, "labels": {"srv-panel.stack": "true", "srv-panel.app-id": str(app.id)}}
        health = service.health_check
        if health and health.probe_type == "command" and health.command:
            spec["healthcheck"] = {
                "test": ["CMD", *health.command], "interval": f"{health.interval_seconds}s",
                "timeout": f"{health.timeout_seconds}s", "retries": health.retries,
                "start_period": f"{health.start_period_seconds}s",
            }
        if name == stack.web_service_name:
            spec["ports"] = [f"127.0.0.1:{app.host_port}:{stack.web_internal_port}"]
        services[name] = spec
    return {
        "services": services,
        "volumes": volumes,
        "networks": {network: {"name": network, "driver": "bridge", "labels": {"srv-panel.stack": "true", "srv-panel.app-id": str(app.id)}}},
    }


def write_project(
    app: ContainerApp | Any,
    stack: OfficialStackDefinition,
    environments: dict[str, dict[str, str]],
) -> Path:
    root = apps.root(int(app.id))
    root.mkdir(parents=True, exist_ok=True)
    env_root = environment_dir(int(app.id))
    env_root.mkdir(parents=True, exist_ok=True)
    for service_name, values in environments.items():
        apps.write_env(env_root / f"{service_name}.env", values)
    path = compose_path(int(app.id))
    path.write_text(json.dumps(render_compose(app, stack, environments), indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _compose(app_id: int, *args: str, timeout: int = 120):
    return apps._run(["docker", "compose", "--project-name", project_name(app_id), "--file", str(compose_path(app_id)), *args], timeout=timeout)


def validate_project(app_id: int) -> None:
    result = _compose(app_id, "config", "--quiet", timeout=60)
    if result.returncode:
        raise RuntimeError(f"Generated Compose configuration is invalid: {(result.stderr or result.stdout)[-1500:]}")


def up(app_id: int) -> None:
    validate_project(app_id)
    result = _compose(app_id, "up", "--detach", "--remove-orphans", timeout=180)
    if result.returncode:
        raise RuntimeError(f"Compose could not start stack: {(result.stderr or result.stdout)[-1500:]}")


def start(app_id: int) -> None:
    result = _compose(app_id, "start", timeout=90)
    if result.returncode:
        raise RuntimeError(f"Compose could not start stack: {(result.stderr or result.stdout)[-1000:]}")


def stop(app_id: int) -> None:
    result = _compose(app_id, "stop", "--timeout", "15", timeout=90)
    if result.returncode:
        raise RuntimeError(f"Compose could not stop stack: {(result.stderr or result.stdout)[-1000:]}")


def down(app_id: int) -> None:
    result = _compose(app_id, "down", "--remove-orphans", timeout=120)
    if result.returncode:
        raise RuntimeError(f"Compose could not remove stack containers: {(result.stderr or result.stdout)[-1000:]}")

