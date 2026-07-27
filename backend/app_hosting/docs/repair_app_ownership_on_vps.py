#!/usr/bin/env python3
"""Audit or repair hosted Python app identity on a VPS.

Run from backend/: python3 app_hosting/docs/repair_app_ownership_on_vps.py --apply
The default is read-only. --apply never starts an app.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from database import AsyncSessionLocal
from models.hosted_app import HostedApp
from sqlalchemy import select
from services import app_ownership_service, app_runtime_service
from utils import shell


def marker_owner(path: Path) -> int | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Description=SRV Panel Python app "):
                return int(line.rsplit(" ", 1)[-1])
    except (OSError, ValueError):
        return None
    return None


async def repair(apply: bool) -> int:
    async with AsyncSessionLocal() as db:
        apps = list((await db.scalars(select(HostedApp).order_by(HostedApp.id))).all())
        owners = {app.id for app in apps}
        expected_services = {app.id: app_ownership_service.service_name(app.id) for app in apps}
        env_owners = _path_owners(apps, "env_path")
        root_owners = _path_owners(apps, "work_dir")
        original = {app.id: (app.service_name, app.env_path, app.work_dir) for app in apps}
        if apply:
            for app in apps:
                if app.service_name != expected_services[app.id]:
                    app.service_name = f"__srv_repair_{app.id}__"
            await db.flush()
        failures = 0
        for app in apps:
            old_service, old_env, old_root = original[app.id]
            expected_service = app_ownership_service.service_name(app.id)
            expected_env = app_ownership_service.env_path(app.id)
            expected_root = app_ownership_service.work_dir(app.id)
            changed = (old_service, old_env, old_root) != (expected_service, str(expected_env), str(expected_root))
            state = "CHANGE" if changed else "OK"
            print(f"{state} app {app.id}: {old_service} -> {expected_service}, port {app.port}")
            if not apply:
                continue
            app_ownership_service.apply_identity(app)
            _move_owned_path(Path(old_root), Path(app.work_dir), root_owners.get(old_root, set()), app.id)
            copied_env = _copy_owned_environment(Path(old_env), expected_env, env_owners.get(old_env, set()), app.id)
            if copied_env:
                print("  copied protected environment to its app-owned path")
            legacy_unit = app_ownership_service.unit_path(old_service)
            if old_service != app.service_name and legacy_unit.exists() and marker_owner(legacy_unit) == app.id and old_service not in expected_services.values():
                await app_runtime_service.systemctl("disable", "--now", old_service, allow_missing=True)
                await shell.remove_path(legacy_unit)
                await app_runtime_service.systemctl("daemon-reload")
                print(f"  removed stale legacy unit {legacy_unit.name}")
            unit = app_ownership_service.unit_path(app.service_name)
            owner = marker_owner(unit) if unit.exists() else None
            if unit.exists() and owner == app.id and f"EnvironmentFile={app.env_path}" not in unit.read_text(encoding="utf-8"):
                await app_runtime_service.systemctl("disable", "--now", app.service_name, allow_missing=True)
                await shell.remove_path(unit)
                await app_runtime_service.systemctl("daemon-reload")
                print("  removed outdated unit owned by this app")
            if unit.exists() and owner != app.id:
                if owner in owners and expected_services[owner] != app.service_name:
                    await app_runtime_service.systemctl("disable", "--now", app.service_name, allow_missing=True)
                    await shell.remove_path(unit)
                    await app_runtime_service.systemctl("daemon-reload")
                    print(f"  removed stale duplicate unit marked for app {owner}")
                else:
                    print(f"  SKIP: {unit.name} has unknown or conflicting ownership.")
                    failures += 1
                    continue
            current = Path(app.work_dir) / "current"
            if not current.is_dir():
                print("  SKIP: no active release; deploy this app after repair.")
                continue
            if not expected_env.is_file() and app.postgres_mode == "external":
                print("  SKIP: external DATABASE_URL is missing; save it in the panel first.")
                failures += 1
                continue
            if not expected_env.is_file():
                await app_runtime_service.prepare_environment(app, current / "source")
                print("  rebuilt protected environment")
            await app_runtime_service.install_unit(app, current)
            print(f"  rebuilt {unit.name}; it remains stopped")
        if apply:
            await db.commit()
    return failures


def _path_owners(apps: list[HostedApp], field: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for app in apps:
        result.setdefault(str(getattr(app, field)), set()).add(app.id)
    return result


def _move_owned_path(source: Path, target: Path, owners: set[int], app_id: int) -> None:
    if source == target or not source.exists() or target.exists() or owners != {app_id}:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _copy_owned_environment(source: Path, target: Path, owners: set[int], app_id: int) -> bool:
    if source == target or not source.is_file() or target.exists() or owners != {app_id}:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    os.chmod(target, 0o600)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write only safe ownership repairs; never start apps.")
    args = parser.parse_args()
    failures = asyncio.run(repair(args.apply))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
