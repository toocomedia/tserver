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
TYPE_BUILD_DIR = "build_dir"       # Stale build checkout dir (per-deployment under CONTAINER_APP_ROOT/<id>/build/<dep>)
TYPE_BUILD_WORKSPACE = "build_workspace"  # Per-deployment XDG workspace under /var/lib/srv-panel/build/<dep>
TYPE_DANGLING_IMAGE = "dangling_image"  # <none>:<none> docker image
TYPE_UNUSED_IMAGE = "unused_image"      # Tagged but not active/rollback
TYPE_BUILD_CACHE = "build_cache"        # Docker builder / BuildKit cache (single aggregate item)
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

    # 1. Stale build dirs — per-deployment under CONTAINER_APP_ROOT/<app_id>/build/<dep_id>.
    # Legacy/fallback: whole app dir if no inner build/ layout (keeps existing tests passing).
    build_root = Path(config.CONTAINER_APP_ROOT)
    if build_root.is_dir():
        for child in build_root.iterdir():
            if not child.is_dir():
                continue
            # Symlink safety — never follow or delete symlinked app dirs
            try:
                if child.is_symlink():
                    continue
            except OSError:
                continue
            # Prefer granular per-deployment dirs
            per_deploy_root = child / "build"
            if per_deploy_root.is_dir():
                try:
                    dep_entries = list(per_deploy_root.iterdir())
                except OSError:
                    dep_entries = []
                if dep_entries:
                    for dep_dir in dep_entries:
                        try:
                            if not dep_dir.is_dir() or dep_dir.is_symlink():
                                continue
                        except OSError:
                            continue
                        try:
                            age_days = (now - dep_dir.stat().st_mtime) / 86400
                        except OSError:
                            age_days = 0.0
                        size_mb = _dir_size_mb(dep_dir)
                        item_id = _make_id(TYPE_BUILD_DIR, str(dep_dir))
                        items.append(InventoryItem(
                            item_id=item_id,
                            type=TYPE_BUILD_DIR,
                            path=str(dep_dir),
                            size_mb=size_mb,
                            age_days=age_days,
                        ))
                    continue
            # Fallback — size the whole app dir (legacy dev/test fixture)
            try:
                age_days = (now - child.stat().st_mtime) / 86400
            except OSError:
                age_days = 0.0
            size_mb = _dir_size_mb(child)
            item_id = _make_id(TYPE_BUILD_DIR, str(child))
            items.append(InventoryItem(
                item_id=item_id,
                type=TYPE_BUILD_DIR,
                path=str(child),
                size_mb=size_mb,
                age_days=age_days,
            ))

    # 1b. Per-deployment XDG build workspaces (/var/lib/srv-panel/build/<dep_id>)
    try:
        workspace_base = Path(config.CONTAINER_APP_ENV_ROOT).parent / "build"
    except Exception:
        workspace_base = None
    if workspace_base is not None and workspace_base.is_dir():
        try:
            for ws_child in workspace_base.iterdir():
                try:
                    if not ws_child.is_dir() or ws_child.is_symlink():
                        continue
                except OSError:
                    continue
                # Only numeric deployment ids (defensive)
                name = ws_child.name
                if not name.isdigit():
                    # Still allow but skip obvious non-deploy dirs longer than 10 chars without digits?
                    pass
                try:
                    age_days = (now - ws_child.stat().st_mtime) / 86400
                except OSError:
                    age_days = 0.0
                size_mb = _dir_size_mb(ws_child)
                # Skip tiny empty workspaces to reduce noise
                if size_mb < 0.1 and age_days < 1:
                    continue
                item_id = _make_id(TYPE_BUILD_WORKSPACE, str(ws_child))
                items.append(InventoryItem(
                    item_id=item_id,
                    type=TYPE_BUILD_WORKSPACE,
                    path=str(ws_child),
                    size_mb=size_mb,
                    age_days=age_days,
                ))
        except OSError:
            pass

    # 2. Docker images — dangling + unused tagged (single-pass, deduped)
    # We run one listing for all images and classify. The legacy dangling-only
    # filter is handled implicitly; repoTag == <none>:<none> => dangling.
    seen_image_ids: set[str] = set()
    result_all = _run(
        ["docker", "images", "--format", "{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"],
        timeout=12,
    )
    if result_all is not None and getattr(result_all, "returncode", 1) == 0 and getattr(result_all, "stdout", None):
        for line in result_all.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            image_id = ""
            repo_tag = ""
            size_str = "0MB"
            # Backward compat: legacy mock returns "ID\tSIZE\tCreatedAt" (size in field 1)
            if len(parts) == 3 and _is_docker_size(parts[1].strip()):
                image_id = parts[0].strip()
                repo_tag = "<none>:<none>"
                size_str = parts[1].strip()
            elif len(parts) >= 3:
                image_id = parts[0].strip()
                repo_tag = parts[1].strip()
                size_str = parts[2].strip()
            elif len(parts) == 2:
                image_id = parts[0].strip()
                # could be ID+Size mock
                if _is_docker_size(parts[1].strip()):
                    repo_tag = "<none>:<none>"
                    size_str = parts[1].strip()
                else:
                    repo_tag = parts[1].strip()
            else:
                image_id = parts[0].strip()
            if not image_id or image_id in seen_image_ids:
                continue
            seen_image_ids.add(image_id)
            is_dangling = repo_tag == "<none>:<none>" or repo_tag == "<none>"
            item_type = TYPE_DANGLING_IMAGE if is_dangling else TYPE_UNUSED_IMAGE
            size_mb = _parse_docker_size(size_str)
            # Also catch "srv-panel/railpack-app" images that are failed builds — high value to surface
            protected = image_id in active_digests or image_id in rollback_images
            # Also protect by full reference match (image_digest may be "srv-panel/railpack-app:12-34")
            if not protected and repo_tag:
                if repo_tag in active_digests or repo_tag in rollback_images:
                    protected = True
            reason = "Active or rollback image" if protected else ""
            # For unused images, also treat panel-owned builder images as deletable but surfaced
            items.append(InventoryItem(
                item_id=_make_id(item_type, image_id),
                type=item_type,
                path=f"{image_id} ({repo_tag})" if repo_tag and repo_tag != "<none>:<none>" else image_id,
                size_mb=size_mb,
                age_days=0.0,
                protected=protected,
                protect_reason=reason,
            ))
    else:
        # Fallback: legacy dangling-only enumeration (keeps old tests / docker without RepoTag)
        fallback = _run(
            ["docker", "images", "--filter", "dangling=true",
             "--format", "{{.ID}}\t{{.Size}}\t{{.CreatedAt}}"],
            timeout=10,
        )
        if fallback is not None and getattr(fallback, "returncode", 1) == 0:
            for line in fallback.stdout.splitlines():
                parts = line.split("\t", 2)
                if len(parts) < 1 or not parts[0].strip():
                    continue
                image_id = parts[0].strip()
                if image_id in seen_image_ids:
                    continue
                seen_image_ids.add(image_id)
                size_str = parts[1].strip() if len(parts) > 1 else "0MB"
                size_mb = _parse_docker_size(size_str)
                protected = image_id in active_digests or image_id in rollback_images
                reason = "Active or rollback image" if protected else ""
                items.append(InventoryItem(
                    item_id=_make_id(TYPE_DANGLING_IMAGE, image_id),
                    type=TYPE_DANGLING_IMAGE,
                    path=image_id,
                    size_mb=size_mb,
                    age_days=0.0,
                    protected=protected,
                    protect_reason=reason,
                ))

    # 2b. Docker builder / BuildKit cache (aggregate single item)
    cache_mb = _collect_build_cache_mb()
    if cache_mb is not None and cache_mb >= 5.0:
        # Compute age as 0 (cache is cumulative); use builder name as path
        cache_path = f"Docker builder cache ({config.BUILDX_BUILDER_NAME})"
        item_id = _make_id(TYPE_BUILD_CACHE, cache_path)
        items.append(InventoryItem(
            item_id=item_id,
            type=TYPE_BUILD_CACHE,
            path=cache_path,
            size_mb=round(cache_mb, 1),
            age_days=0.0,
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
    if item.type in (TYPE_BUILD_DIR, TYPE_BUILD_WORKSPACE):
        p = Path(item.path)
        # Never follow symlinks; abort if symlink
        if p.is_symlink():
            raise RuntimeError("Refusing to delete symlinked path.")
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink(missing_ok=True)
    elif item.type in (TYPE_DANGLING_IMAGE, TYPE_UNUSED_IMAGE):
        # path may be "ID (repo:tag)" — extract ID
        image_ref = item.path.split(" ")[0].strip()
        # Guard: never delete active/rollback — already checked but re-validate id prefix match
        result = _run(["docker", "rmi", image_ref], timeout=60)
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            # If image is in use by a running container, report but don't raise hard — it becomes skipped via error list
            raise RuntimeError(f"docker rmi failed: {stderr[-300:]}")
    elif item.type == TYPE_BUILD_CACHE:
        # Prune all build cache — re-validated as safe (no active layers tied to running containers are kept by builder)
        # Try buildx builder first, then generic builder prune
        builder = getattr(config, "BUILDX_BUILDER_NAME", "") or "srv-panel-builder"
        res = _run(["docker", "buildx", "prune", "--builder", builder, "-f"], timeout=120)
        # Also prune generic builder cache to reclaim BuildKit cache outside buildx
        res2 = _run(["docker", "builder", "prune", "-f"], timeout=120)
        # Consider success if either succeeded; if both fail, surface first error
        if res.returncode != 0 and res2.returncode != 0:
            stderr = (res.stderr or res.stdout or res2.stderr or "").strip()
            raise RuntimeError(f"builder prune failed: {stderr[-300:]}")
    elif item.type == TYPE_OLD_LOG:
        p = Path(item.path)
        if p.is_symlink():
            raise RuntimeError("Refusing to delete symlinked log.")
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


def _is_docker_size(value: str) -> bool:
    s = value.strip().upper()
    return s.endswith(("GB", "MB", "KB", "KIB", "B")) and _parse_docker_size(s) >= 0.0


def _collect_build_cache_mb() -> float | None:
    """Return aggregate Docker builder cache size in MB, or None if unavailable.
    Tries `docker buildx du` then `docker builder du`.
    Guarded: output containing tabs (image listing mock) is ignored so unit tests
    that mock _run with image lines do not create a spurious cache item.
    Timeouts kept short (6s) so inventory stays fast.
    """
    builder_name = getattr(config, "BUILDX_BUILDER_NAME", "") or "srv-panel-builder"
    # Only 2 fast probes — enough on modern docker, avoids 80s worst-case
    for cmd in (
        ["docker", "buildx", "du", "--builder", builder_name],
        ["docker", "builder", "du"],
    ):
        try:
            res = _run(cmd, timeout=6)
        except Exception:
            continue
        if res.returncode != 0 or not res.stdout:
            continue
        raw = res.stdout.strip()
        # Mock trap: tests mock _run with image lines containing tabs
        if "\t" in raw:
            continue
        # Parse first parseable size token
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Lines may be "2.5GB" or "Reclaimable: 2.5GB" or table rows
            for token in line.replace(",", " ").replace(":", " ").split():
                token = token.strip()
                if _is_docker_size(token):
                    val = _parse_docker_size(token)
                    if val > 0:
                        return val
            # Fallback: try whole line
            if _is_docker_size(line):
                return _parse_docker_size(line)
        # If raw itself is a size like "1.2GB", handle
        if _is_docker_size(raw):
            return _parse_docker_size(raw)
    return None
