"""Disk cleanup inventory and safe deletion for the Resource Guard.

Two-phase design:
  1. inventory()  — dry-run: returns what *could* be deleted with size/age.
  2. run_cleanup() — caller selects a subset by item_id; server re-validates
                     protection rules before each deletion.

Protected items (never deleted):
  - Docker volumes
  - Backup archives (CONTAINER_APP_BACKUP_ROOT)
  - Active container image (image_digest of a running/deployed app)
  - Rollback image (previous_image of an app)

Registers a native_light Guard token during cleanup execution.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from services.container_app_service import _run
from services.resource_guard_service import resource_guard_service

logger = logging.getLogger(__name__)

# Item types
TYPE_BUILD_DIR = "build_dir"       # Stale build checkout dir
TYPE_DANGLING_IMAGE = "dangling_image"  # <none>:<none> docker image
TYPE_OLD_LOG = "old_log"           # Panel log file older than threshold

_LOG_DIRS: list[str] = ["/var/log/srv-panel"]
_LOG_MAX_AGE_DAYS = 30


@dataclass
class InventoryItem:
    item_id: str
    type: str
    path: str          # human-readable path or image ID
    size_mb: float
    age_days: float
    protected: bool = False
    protect_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "type": self.type,
            "path": self.path,
            "size_mb": self.size_mb,
            "age_days": round(self.age_days, 1),
            "protected": self.protected,
            "protect_reason": self.protect_reason,
        }


async def inventory(
    active_digests: set[str],
    rollback_images: set[str],
) -> list[dict[str, Any]]:
    """Return full inventory (deletable + protected items clearly marked)."""
    items = await asyncio.to_thread(
        _build_inventory, active_digests, rollback_images
    )
    return [i.to_dict() for i in items]


async def run_cleanup(
    include_ids: list[str],
    active_digests: set[str],
    rollback_images: set[str],
) -> dict[str, Any]:
    """Delete items whose item_id is in *include_ids*.

    Protection is re-checked server-side before each deletion.
    Returns: {deleted, skipped, freed_mb, errors}
    """
    token = resource_guard_service.register(
        "container_app", "disk-cleanup", "background",
        "Disk cleanup", profile="native_light",
    )
    try:
        result = await asyncio.to_thread(
            _execute_cleanup, include_ids, active_digests, rollback_images
        )
    finally:
        resource_guard_service.unregister(token)
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_inventory(
    active_digests: set[str],
    rollback_images: set[str],
) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    now = time.time()

    # 1. Stale build dirs (container app roots)
    build_root = Path(config.CONTAINER_APP_ROOT)
    if build_root.is_dir():
        for child in build_root.iterdir():
            if not child.is_dir():
                continue
            age_days = (now - child.stat().st_mtime) / 86400
            size_mb = _dir_size_mb(child)
            item_id = _make_id(TYPE_BUILD_DIR, str(child))
            items.append(InventoryItem(
                item_id=item_id,
                type=TYPE_BUILD_DIR,
                path=str(child),
                size_mb=size_mb,
                age_days=age_days,
            ))

    # 2. Dangling docker images (<none>:<none>)
    result = _run(
        ["docker", "images", "--filter", "dangling=true",
         "--format", "{{.ID}}\t{{.Size}}\t{{.CreatedAt}}"],
        timeout=30,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 1:
                continue
            image_id = parts[0].strip()
            size_str = parts[1].strip() if len(parts) > 1 else "0MB"
            item_id = _make_id(TYPE_DANGLING_IMAGE, image_id)
            size_mb = _parse_docker_size(size_str)
            age_days = 0.0  # docker doesn't give easy age — unknown

            protected = image_id in active_digests or image_id in rollback_images
            reason = ""
            if protected:
                reason = "Active or rollback image"

            items.append(InventoryItem(
                item_id=item_id,
                type=TYPE_DANGLING_IMAGE,
                path=image_id,
                size_mb=size_mb,
                age_days=age_days,
                protected=protected,
                protect_reason=reason,
            ))

    # 3. Old panel log files
    for log_dir_str in _LOG_DIRS:
        log_dir = Path(log_dir_str)
        if not log_dir.is_dir():
            continue
        for log_file in log_dir.glob("**/*.log"):
            age_days = (now - log_file.stat().st_mtime) / 86400
            if age_days < _LOG_MAX_AGE_DAYS:
                continue
            size_mb = log_file.stat().st_size / (1024 * 1024)
            item_id = _make_id(TYPE_OLD_LOG, str(log_file))
            items.append(InventoryItem(
                item_id=item_id,
                type=TYPE_OLD_LOG,
                path=str(log_file),
                size_mb=size_mb,
                age_days=age_days,
            ))

    return items


def _execute_cleanup(
    include_ids: list[str],
    active_digests: set[str],
    rollback_images: set[str],
) -> dict[str, Any]:
    all_items = _build_inventory(active_digests, rollback_images)
    by_id = {item.item_id: item for item in all_items}

    deleted: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    freed_mb = 0.0

    for item_id in include_ids:
        item = by_id.get(item_id)
        if item is None:
            skipped.append(f"{item_id}: not found in inventory")
            continue

        # Re-check protection
        if item.protected:
            skipped.append(f"{item.path}: {item.protect_reason}")
            continue

        # Never delete volumes or backups (extra safety check by path/type)
        if _is_backup(item) or _is_volume(item):
            skipped.append(f"{item.path}: protected category")
            continue

        try:
            _delete_item(item)
            freed_mb += item.size_mb
            deleted.append(item.path)
        except Exception as exc:
            errors.append(f"{item.path}: {exc}")

    return {
        "deleted": deleted,
        "skipped": skipped,
        "freed_mb": round(freed_mb, 1),
        "errors": errors,
    }


def _delete_item(item: InventoryItem) -> None:
    if item.type == TYPE_BUILD_DIR:
        p = Path(item.path)
        if p.is_dir():
            shutil.rmtree(p)
    elif item.type == TYPE_DANGLING_IMAGE:
        result = _run(["docker", "rmi", item.path], timeout=60)
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"docker rmi failed: {stderr[-300:]}")
    elif item.type == TYPE_OLD_LOG:
        p = Path(item.path)
        p.unlink(missing_ok=True)


def _is_backup(item: InventoryItem) -> bool:
    backup_root = config.CONTAINER_APP_BACKUP_ROOT
    return item.path.startswith(backup_root)


def _is_volume(item: InventoryItem) -> bool:
    # Volumes are not collected, but guard against path-injection
    return "volume" in item.type.lower()


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 1)


def _make_id(item_type: str, path: str) -> str:
    digest = hashlib.sha256(f"{item_type}:{path}".encode()).hexdigest()[:12]
    return f"{item_type}:{digest}"


def _parse_docker_size(size_str: str) -> float:
    """Parse docker size strings like '123MB', '1.2GB', '456kB'."""
    s = size_str.strip().upper()
    try:
        if s.endswith("GB"):
            return float(s[:-2]) * 1024
        if s.endswith("MB"):
            return float(s[:-2])
        if s.endswith("KB") or s.endswith("KIB"):
            return float(s.rstrip("BKI")) / 1024
        if s.endswith("B"):
            return float(s[:-1]) / (1024 * 1024)
        return float(s)
    except ValueError:
        return 0.0
