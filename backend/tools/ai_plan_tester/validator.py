"""
tools/ai_plan_tester/validator.py — Multi-stage validator for AI AppSpecs and Compose plans.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import yaml

from tools.ai_plan_tester.catalog import BenchmarkApp


@dataclass
class ValidationIssue:
    severity: str  # "ERROR" | "WARNING" | "SUGGESTION"
    field: str
    message: str
    fix_advice: str


@dataclass
class ValidationResult:
    is_valid: bool
    status: str  # "PASS" | "FAIL"
    app_slug: str
    detected_source_type: str
    detected_services: List[str] = field(default_factory=list)
    detected_port: Optional[int] = None
    detected_database: str = "none"
    issues: List[ValidationIssue] = field(default_factory=list)
    compose_yaml: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARNING")


_PROHIBITED_CONTAINER_MOUNTS = (
    "/var/run/docker.sock", "/run/docker.sock", "/proc", "/sys", "/etc", "/dev", "/root"
)


def validate_plan_payload(
    plan_data: Dict[str, Any],
    app: BenchmarkApp,
) -> ValidationResult:
    """
    Validates a generated setup plan against schemas, security policies,
    port configurations, database connections, and YAML export fidelity.
    """
    issues: List[ValidationIssue] = []
    p = plan_data.get("payload") if isinstance(plan_data.get("payload"), dict) else plan_data
    app_spec = p.get("app_spec") if isinstance(p.get("app_spec"), dict) else None

    # 1. Action Type & Structure
    action_type = str(plan_data.get("action_type") or p.get("deploy_type") or "install").lower()
    src_type = str(p.get("source_type") or app.source_type).lower()

    # 2. Extract Services & Web Port
    services: Dict[str, Any] = {}
    web_port: Optional[int] = None
    web_service: str = ""

    if app_spec:
        raw_services = app_spec.get("services")
        if isinstance(raw_services, dict):
            services = raw_services
        web_service = str(app_spec.get("web_service_name") or "web")
        raw_port = app_spec.get("web_port")
        try:
            web_port = int(raw_port) if raw_port is not None else None
        except (ValueError, TypeError):
            issues.append(ValidationIssue(
                "ERROR", "app_spec.web_port",
                f"Web port is not a valid integer: {raw_port}",
                "Ensure web_port is formatted as an integer (e.g. 8080) instead of a string.",
            ))
    elif p.get("services") and isinstance(p.get("services"), dict):
        services = p.get("services")
        raw_port = p.get("internal_port")
        try:
            web_port = int(raw_port) if raw_port is not None else None
        except (ValueError, TypeError):
            web_port = None
    else:
        # Single app plan
        services = {"app": p}
        web_service = "app"
        raw_port = p.get("internal_port") or p.get("port")
        try:
            web_port = int(raw_port) if raw_port is not None else None
        except (ValueError, TypeError):
            web_port = None

    service_names = list(services.keys())
    if not service_names:
        issues.append(ValidationIssue(
            "ERROR", "services",
            "Plan does not define any container services.",
            "Define at least one primary web service in the services dictionary.",
        ))

    # Validate Port Range & Collisions
    if web_port is not None:
        if not (1 <= web_port <= 65535):
            issues.append(ValidationIssue(
                "ERROR", "port",
                f"Configured port {web_port} is outside valid range (1-65535).",
                "Change port to a standard application port (e.g. 80, 3000, 8080).",
            ))
    else:
        issues.append(ValidationIssue(
            "WARNING", "port",
            "Internal port is not explicitly declared.",
            "Declare internal_port or web_port to avoid fallback to default 3000.",
        ))

    # 3. Security Policy Audit
    for s_name, s_data in services.items():
        if not isinstance(s_data, dict):
            continue
        # Check volumes
        volumes = s_data.get("volumes") or []
        for v in volumes:
            mount_path = ""
            if isinstance(v, dict):
                mount_path = str(v.get("container_mount_path") or v.get("target") or "")
            elif isinstance(v, str):
                parts = v.split(":")
                mount_path = parts[1] if len(parts) > 1 else parts[0]
            for bad in _PROHIBITED_CONTAINER_MOUNTS:
                if mount_path.startswith(bad):
                    issues.append(ValidationIssue(
                        "ERROR", f"services.{s_name}.volumes",
                        f"Security violation: volume mounts forbidden path '{mount_path}'.",
                        f"Remove host mount '{mount_path}' and use an isolated data volume instead.",
                    ))
        # Check privileged mode
        if s_data.get("privileged") is True:
            issues.append(ValidationIssue(
                "ERROR", f"services.{s_name}.privileged",
                "Security violation: privileged container mode is strictly forbidden.",
                "Remove 'privileged: true' from service definition.",
            ))

    # 4. Database Attachment & Environment Audit
    db_kind = "none"
    env_map = p.get("environment_values") or p.get("nonsecret_settings") or {}
    if not isinstance(env_map, dict):
        env_map = {}
    env_keys = set(k.upper() for k in env_map.keys())

    # Check attached DBs or compose services
    for s_name in service_names:
        s_lower = s_name.lower()
        if any(k in s_lower for k in ("postgres", "psql", "pg")):
            db_kind = "postgresql"
        elif any(k in s_lower for k in ("maria", "mysql")):
            db_kind = "mariadb"
        elif any(k in s_lower for k in ("redis", "valkey", "keydb")):
            if db_kind == "none":
                db_kind = "redis"

    db_attachments = p.get("database_attachments") or []
    if isinstance(db_attachments, list) and db_attachments:
        db_kind = str(db_attachments[0].get("kind") or db_kind)

    # Check for missing database connection strings when DB is required
    if app.expected_database in ("postgresql", "mariadb"):
        has_db_env = any(k in env_keys for k in (
            "DATABASE_URL", "DB_URL", "DB_CONNECTION", "DB_HOST", "DATABASE_HOST", "POSTGRES_URL", "MYSQL_URL"
        ))
        has_db_service = any(k in ("db", "postgres", "mariadb", "mysql", "database") for k in service_names)
        if not has_db_env and not has_db_service and not db_attachments:
            issues.append(ValidationIssue(
                "WARNING", "environment_values",
                f"Application typically requires {app.expected_database} but no DATABASE_URL or database attachment was found.",
                f"Add DATABASE_URL template or attach a managed {app.expected_database} container.",
            ))

    # 5. YAML Export Fidelity
    compose_yaml = ""
    try:
        if app_spec:
            from services.apps_engine.app_spec import AppSpec
            from services.apps_engine.template_export import app_spec_to_compose_dict
            parsed_spec = AppSpec.from_dict(app_spec)
            c_dict = app_spec_to_compose_dict(parsed_spec, env_map)
            compose_yaml = yaml.dump(c_dict, sort_keys=False)
        elif p.get("stack_manifest") and isinstance(p.get("stack_manifest"), dict):
            compose_yaml = yaml.dump(p.get("stack_manifest"), sort_keys=False)
        else:
            # Single app compose representation
            single_compose = {
                "version": "3.8",
                "services": {
                    app.slug: {
                        "image": p.get("image_reference") or app.target,
                        "restart": "always",
                        "ports": [f"{web_port}:{web_port}"] if web_port else [],
                        "environment": env_map,
                    }
                }
            }
            compose_yaml = yaml.dump(single_compose, sort_keys=False)

        # Validate syntax of exported YAML
        yaml.safe_load(compose_yaml)
    except Exception as exc:
        issues.append(ValidationIssue(
            "ERROR", "compose_yaml",
            f"Failed to export valid Docker Compose YAML: {str(exc)}",
            "Ensure service definitions conform to standard Docker Compose syntax.",
        ))

    is_valid = not any(i.severity == "ERROR" for i in issues)
    return ValidationResult(
        is_valid=is_valid,
        status="PASS" if is_valid else "FAIL",
        app_slug=app.slug,
        detected_source_type=src_type,
        detected_services=service_names,
        detected_port=web_port,
        detected_database=db_kind,
        issues=issues,
        compose_yaml=compose_yaml,
        raw_payload=p,
    )
