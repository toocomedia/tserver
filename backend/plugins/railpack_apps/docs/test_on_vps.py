#!/usr/bin/env python3
"""Read-only VPS smoke test for the Railpack Apps plugin.

Run from the deployed checkout:
  sudo /opt/srv-panel/venv/bin/python backend/plugins/railpack_apps/docs/test_on_vps.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def check(label: str, command: list[str]) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    print(("PASS" if ok else "FAIL") + " " + label)
    return ok


def check_buildkit() -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "srv-panel-buildkit"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        ok = result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    print(("PASS" if ok else "FAIL") + " Railpack BuildKit container")
    return ok


def check_plugin_code() -> bool:
    backend_root = Path(__file__).resolve().parents[3]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    try:
        from plugins.railpack_apps import router
        from services import container_app_service
        from services import container_app_database_service

        assert container_app_service.validate_port(3000) == 3000
        assert container_app_service.validate_image_reference("ghcr.io/example/app:1")
        assert container_app_database_service.parse_specs([
            {"kind": "redis", "provider": "docker", "environment_key": "REDIS_URL"}
        ])[0]["kind"] == "redis"
        paths = [route.path for route in router.router.routes]
        uninstall = next(index for index, path in enumerate(paths) if path.endswith("/{app_id}/uninstall"))
        control = next(index for index, path in enumerate(paths) if path.endswith("/{app_id}/{action}"))
        assert uninstall < control
        ok = True
    except Exception as exc:
        print(f"DETAIL plugin code: {exc}")
        ok = False
    print(("PASS" if ok else "FAIL") + " Railpack Apps Python code")
    return ok


def check_ui_regressions() -> bool:
    test = Path(__file__).resolve().parents[3] / "tests" / "test_railpack_apps_ui.py"
    return check("Railpack Apps Jinja and UI regression", [sys.executable, str(test)])


def main() -> int:
    checks = [
        ("Docker CLI", ["docker", "--version"]),
        ("Docker daemon", ["docker", "info"]),
        ("Docker BuildKit", ["docker", "buildx", "version"]),
        ("Railpack CLI", ["railpack", "--version"]),
        ("Nginx configuration", ["nginx", "-t"]),
    ]
    if shutil.which("systemctl"):
        checks.append(("Nginx service", ["systemctl", "is-active", "--quiet", "nginx"]))
    passed = [check(label, command) for label, command in checks]
    passed.append(check_buildkit())
    passed.append(check_plugin_code())
    passed.append(check_ui_regressions())
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main())
