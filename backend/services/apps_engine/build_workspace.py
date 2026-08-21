"""Private per-deployment directories for build tools and caches."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import config


@dataclass(frozen=True)
class BuildWorkspace:
    root: Path
    temporary: Path
    cache: Path


def prepare(deployment_id: int) -> BuildWorkspace:
    if deployment_id < 1:
        raise RuntimeError("Invalid deployment workspace.")
    base = Path(config.CONTAINER_APP_ENV_ROOT).parent / "build"
    root = base / str(deployment_id)
    if root.exists() and root.is_symlink():
        raise RuntimeError("Build workspace is unsafe.")
    temporary, cache = root / "tmp", root / "cache"
    for path in (root, temporary, cache):
        if path.exists() and path.is_symlink():
            raise RuntimeError("Build workspace is unsafe.")
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    return BuildWorkspace(root=root, temporary=temporary, cache=cache)
