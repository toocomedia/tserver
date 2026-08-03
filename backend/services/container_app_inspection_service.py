"""Static repository inspection for the Railpack Apps builder."""
from __future__ import annotations

import re
from pathlib import Path

from dependencies.git import repository_service


def inspect_repository(repository_url: str, branch: str) -> dict[str, object]:
    with repository_service.temporary_clone(repository_url, branch, allow_default_branch=True) as checkout:
        root = checkout.path
        files = {path.name for path in root.iterdir() if path.is_file()}
        text = _read_sources(root)
        runtime = _runtime(files)
        databases = _databases(text)
        return {
            "repository_url": checkout.repository_url,
            "branch": checkout.branch,
            "runtime": runtime,
            "build_mode": "dockerfile" if "Dockerfile" in files else "railpack",
            "internal_port": _port(text, runtime),
            "database_types": databases,
            "database_detected": bool(databases),
        }


def _runtime(files: set[str]) -> str:
    if "package.json" in files:
        return "Node.js"
    if files & {"requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock"}:
        return "Python"
    if "composer.json" in files:
        return "PHP"
    if "go.mod" in files:
        return "Go"
    if "Cargo.toml" in files:
        return "Rust"
    return "Detected by Railpack"


def _port(text: str, runtime: str) -> int:
    for pattern in (r"EXPOSE\s+(\d{2,5})", r"(?:--port|port\s*[=:])\s*(\d{2,5})", r"PORT\s*\|\|\s*(\d{2,5})"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 65535:
                return value
    return {"Python": 8000, "PHP": 8080, "Go": 8080}.get(runtime, 3000)


def _databases(text: str) -> list[str]:
    lower = text.lower()
    markers = {
        "postgresql": ("postgres", "psycopg", "asyncpg", "pg8000"),
        "mariadb/mysql": ("mariadb", "mysql", "pymysql", "mysqlclient"),
        "mongodb": ("mongodb", "mongoose", "pymongo"),
        "redis": ("redis", "ioredis"),
        "sqlite": ("sqlite", "sqlite3"),
    }
    return [name for name, values in markers.items() if any(value in lower for value in values)]


def _read_sources(root: Path) -> str:
    names = ("Dockerfile", "Procfile", "package.json", "railpack.json", "nixpacks.toml")
    texts = []
    for name in names:
        path = root / name
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="ignore")[:200_000])
    for path in list(root.glob("*.py"))[:20]:
        texts.append(path.read_text(encoding="utf-8", errors="ignore")[:100_000])
    return "\n".join(texts)
