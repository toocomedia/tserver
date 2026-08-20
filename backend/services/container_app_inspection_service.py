"""Static repository inspection for the Railpack Apps builder.

Detection precedence (highest → lowest):
  1. docker-compose.yml  → confidence HIGH
  2. Dockerfile EXPOSE   → confidence MEDIUM
  3. package.json / runtime marker → confidence MEDIUM
  4. lockfile text match → confidence LOW  (returned as suggestions, not auto-applied)
"""
from __future__ import annotations

import re
from pathlib import Path

from dependencies.git import repository_service

# Confidence constants
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

# Map Compose image prefixes to canonical DB kind
_COMPOSE_IMAGE_DB: dict[str, str] = {
    "postgres":   "postgresql",
    "mariadb":    "mariadb/mysql",
    "mysql":      "mariadb/mysql",
    "mongo":      "mongodb",
    "redis":      "redis",
}


def inspect_repository(repository_url: str, branch: str, *, ssh_key_path: str | Path | None = None) -> dict[str, object]:
    with repository_service.temporary_clone(repository_url, branch, allow_default_branch=True, ssh_key_path=ssh_key_path) as checkout:
        root = checkout.path
        files = {path.name for path in root.iterdir() if path.is_file()}
        text = _read_sources(root)
        runtime = _runtime(files)

        # Detect databases with confidence levels
        databases, suggestions = _databases_with_confidence(root, files, text)

        build_mode = "dockerfile" if "Dockerfile" in files else "railpack"
        return {
            "repository_url": checkout.repository_url,
            "branch": checkout.branch,
            "runtime": runtime,
            "build_mode": build_mode,
            "internal_port": _port(text, runtime),
            "database_types": [d["kind"] for d in databases],
            "database_detected": bool(databases),
            "database_detections": databases,          # [{kind, confidence}]
            "database_suggestions": suggestions,       # [{kind, confidence, reason}] — LOW only
        }


def _databases_with_confidence(
    root: Path, files: set[str], text: str
) -> tuple[list[dict], list[dict]]:
    """Return (confirmed, suggestions) with confidence attached.

    confirmed → HIGH or MEDIUM  (auto-applied)
    suggestions → LOW only      (shown to user, not applied)
    """
    confirmed: dict[str, dict] = {}   # kind → entry
    suggested: dict[str, dict] = {}   # kind → entry

    def _add(kind: str, confidence: str, reason: str) -> None:
        if confidence in (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM):
            existing = confirmed.get(kind)
            if existing is None or _rank(confidence) > _rank(existing["confidence"]):
                confirmed[kind] = {"kind": kind, "confidence": confidence, "reason": reason}
        else:  # LOW
            if kind not in confirmed:
                suggested[kind] = {"kind": kind, "confidence": confidence, "reason": reason}

    # 1. Compose services → HIGH
    compose_file = _find_compose(root)
    if compose_file:
        for kind in _compose_databases(compose_file):
            _add(kind, CONFIDENCE_HIGH, "docker-compose.yml service image")

    # 2. Dockerfile EXPOSE + FROM → MEDIUM
    if "Dockerfile" in files:
        dockerfile_text = (root / "Dockerfile").read_text(encoding="utf-8", errors="ignore")
        for kind in _dockerfile_databases(dockerfile_text):
            _add(kind, CONFIDENCE_MEDIUM, "Dockerfile FROM/RUN reference")

    # 3. package.json dependencies → MEDIUM
    if "package.json" in files:
        pkg_text = (root / "package.json").read_text(encoding="utf-8", errors="ignore")
        for kind in _text_markers(pkg_text.lower()):
            _add(kind, CONFIDENCE_MEDIUM, "package.json dependency")

    # 4. Lockfile / full source text → LOW (suggestions only)
    lower = text.lower()
    for kind in _text_markers(lower):
        if kind not in confirmed:
            _add(kind, CONFIDENCE_LOW, "source/lockfile text match")

    return list(confirmed.values()), list(suggested.values())


def _rank(confidence: str) -> int:
    return {CONFIDENCE_HIGH: 2, CONFIDENCE_MEDIUM: 1, CONFIDENCE_LOW: 0}.get(confidence, 0)


def _find_compose(root: Path) -> Path | None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        p = root / name
        if p.is_file():
            return p
    return None


def _compose_databases(compose_file: Path) -> list[str]:
    """Extract DB kinds from Compose image: lines."""
    text = compose_file.read_text(encoding="utf-8", errors="ignore")
    kinds: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("image:"):
            image_val = stripped[len("image:"):].strip().strip("'\"").lower()
            for prefix, kind in _COMPOSE_IMAGE_DB.items():
                if image_val.startswith(prefix + ":") or image_val == prefix:
                    if kind not in kinds:
                        kinds.append(kind)
    return kinds


def _dockerfile_databases(dockerfile_text: str) -> list[str]:
    """Check FROM lines in a Dockerfile for known DB images."""
    lower = dockerfile_text.lower()
    kinds: list[str] = []
    for line in lower.splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") or stripped.startswith("run "):
            for prefix, kind in _COMPOSE_IMAGE_DB.items():
                if prefix in stripped and kind not in kinds:
                    kinds.append(kind)
    return kinds


_TEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "postgresql":   ("asyncpg", "psycopg", "postgresql", "postgres", "pg8000", "pgx"),
    "mariadb/mysql": ("mariadb", "mysql", "pymysql", "mysqlclient", "mysql-connector"),
    "mongodb":      ("mongodb", "mongoose", "pymongo", "mongo-driver"),
    "redis":        ("redis", "ioredis", "go-redis"),
    "sqlite":       ("sqlite", "sqlite3"),
}


def _text_markers(lower_text: str) -> list[str]:
    return [kind for kind, tokens in _TEXT_MARKERS.items() if any(t in lower_text for t in tokens)]


def _runtime(files: set[str]) -> str:
    if "package.json" in files:
        return "Node.js"
    if files & {"requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock"}:
        return "Python"
    if "composer.json" in files:
        return "PHP"
    if "go.mod" in files:
        return "Go"
    if "Gemfile" in files:
        return "Ruby"
    if files & {"pom.xml", "build.gradle", "build.gradle.kts"}:
        return "Java"
    if "Cargo.toml" in files:
        return "Rust"
    if "index.html" in files:
        return "Static site"
    return "Detected by Railpack"


def _port(text: str, runtime: str) -> int:
    for pattern in (r"EXPOSE\s+(\d{2,5})", r"(?:--port|port\s*[=:])\\s*(\\d{2,5})", r"PORT\s*\|\|\s*(\d{2,5})"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 65535:
                return value
    return {"Python": 8000, "PHP": 8080, "Go": 8080, "Java": 8080, "Static site": 80}.get(runtime, 3000)


def _read_sources(root: Path) -> str:
    names = (
        "Dockerfile", "Procfile", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "railpack.json", "nixpacks.toml", "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock",
        "Gemfile", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
        "config/database.yml", "config/database.php",
    )
    texts = []
    for name in names:
        path = root / name
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="ignore")[:200_000])
    for path in list(root.glob("*.py"))[:20]:
        texts.append(path.read_text(encoding="utf-8", errors="ignore")[:100_000])
    return "\n".join(texts)
