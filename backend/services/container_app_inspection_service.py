"""Static repository inspection for the Railpack Apps builder.

Detection precedence (highest → lowest):
  1. docker-compose.yml  → confidence HIGH
  2. Dockerfile EXPOSE   → confidence MEDIUM
  3. package.json / runtime marker → confidence MEDIUM
  4. lockfile text match → confidence LOW  (returned as suggestions, not auto-applied)
"""
from __future__ import annotations

import json
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
    "clickhouse": "clickhouse",
}


def inspect_repository(repository_url: str, branch: str, *, ssh_key_path: str | Path | None = None) -> dict[str, object]:
    with repository_service.temporary_clone(repository_url, branch, allow_default_branch=True, ssh_key_path=ssh_key_path) as checkout:
        root = checkout.path
        files = {path.name for path in root.iterdir() if path.is_file()}
        text = _read_sources(root)
        runtime = _runtime(files)
        framework = _framework(root, files, text, runtime)

        # Detect databases with confidence levels
        databases, suggestions = _databases_with_confidence(root, files, text)

        # Extract environment template (.env.example / .env.sample / TEMPLATE.env)
        from services.apps_engine import doc_evidence
        env_sample = doc_evidence.parse_expanded_env_samples(root)

        # Extract markdown installation instructions (GUIDE.md, INSTALL.md, README.md setup sections)
        documentation_evidence = doc_evidence.find_install_instructions(root, env_sample=env_sample)

        # Extract package scripts
        package_scripts = _parse_package_scripts(root)

        # Detect storage mount needs (SQLite, uploads, CMS data)
        storage_mounts = _detect_storage_mounts(framework, databases, files, text)

        # Compose summary if present
        compose_info = _parse_compose_details(root)

        has_dockerfile = "Dockerfile" in files
        has_app_manifest = bool(files & {
            "package.json", "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock",
            "composer.json", "go.mod", "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts",
            "Cargo.toml", "deno.json", "bun.lockb", "railpack.json", "Procfile", "index.html"
        })
        # If repository provides a Dockerfile, default to dockerfile mode to use author-defined dependencies;
        # otherwise default to Railpack.
        build_mode = "dockerfile" if has_dockerfile else "railpack"

        from services.apps_engine.source_image_advisor import advise_official_image
        advice = advise_official_image(checkout.repository_url, framework)

        res_dict = {
            "repository_url": checkout.repository_url,
            "branch": checkout.branch,
            "runtime": runtime,
            "framework": framework,
            "build_mode": build_mode,
            "has_dockerfile": has_dockerfile,
            "internal_port": _port(text, runtime, framework, compose_info),
            "database_types": [d["kind"] for d in databases],
            "database_detected": bool(databases),
            "database_detections": databases,          # [{kind, confidence}]
            "database_suggestions": suggestions,       # [{kind, confidence, reason}] — LOW only
            "env_sample": doc_evidence.ai_safe_env_sample(env_sample),  # secret names retained; values omitted
            "documentation_evidence": documentation_evidence,
            "storage_mount_suggestions": storage_mounts, # [{label, mount_path, reason}]
            "package_scripts": package_scripts,
            "compose_info": compose_info,
        }
        if advice:
            res_dict["official_image_recommendation"] = advice
        return res_dict


def _framework(root: Path, files: set[str], text: str, runtime: str) -> str:
    # 1. Primary check: Top-level package.json dependencies and package name
    pkg_path = root / "package.json"
    if pkg_path.is_file():
        try:
            pkg_data = json.loads(pkg_path.read_text(encoding="utf-8", errors="ignore"))
            pkg_name = str(pkg_data.get("name") or "").lower()
            deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
            deps_lower = {k.lower() for k in deps}
            if "next" in deps_lower or "next.config.js" in files or "next.config.mjs" in files or "next.config.ts" in files:
                return "Next.js"
            if "nuxt" in deps_lower or "nuxt.config.js" in files or "nuxt.config.ts" in files:
                return "Nuxt"
            if "@remix-run/node" in deps_lower or "@remix-run/react" in deps_lower or "remix.config.js" in files:
                return "Remix"
            if "astro" in deps_lower or "astro.config.mjs" in files:
                return "Astro"
            if "@sveltejs/kit" in deps_lower or "svelte.config.js" in files:
                return "SvelteKit"
            if "n8n" in pkg_name or "@n8n/core" in deps_lower or "n8n-core" in deps_lower:
                return "n8n"
            if "ghost" in pkg_name or "ghost" in deps_lower:
                return "Ghost"
            if "strapi" in pkg_name or "@strapi/strapi" in deps_lower:
                return "Strapi"
            if any(k in deps_lower for k in ("express", "fastify", "nestjs", "@nestjs/core")):
                return "Express/NestJS"
        except Exception:
            pass

    lower_text = text.lower()
    if "next.config.js" in files or "next.config.mjs" in files or "next.config.ts" in files or '"next"' in lower_text:
        return "Next.js"
    if "nuxt.config.js" in files or "nuxt.config.ts" in files or '"nuxt"' in lower_text:
        return "Nuxt"
    if "remix.config.js" in files or '"@remix-run' in lower_text:
        return "Remix"
    if "astro.config.mjs" in files or '"astro"' in lower_text:
        return "Astro"
    if "svelte.config.js" in files or '"@sveltejs/kit"' in lower_text:
        return "SvelteKit"
    if "manage.py" in files or "django" in lower_text:
        return "Django"
    if "fastapi" in lower_text:
        return "FastAPI"
    if "flask" in lower_text:
        return "Flask"
    if "artisan" in files or "laravel/framework" in lower_text:
        return "Laravel"
    if "bin/rails" in files or "rails" in lower_text:
        return "Ruby on Rails"
    if "strapi" in lower_text:
        return "Strapi"
    if "ghost" in lower_text:
        return "Ghost"
    if "pocketbase" in lower_text:
        return "PocketBase"
    if runtime == "Node.js" and ("express" in lower_text or "fastify" in lower_text or "nestjs" in lower_text):
        return "Express/NestJS"
    return runtime


def _parse_env_sample(root: Path) -> dict[str, str]:
    """Parse any environment template file using doc_evidence."""
    from services.apps_engine import doc_evidence
    return doc_evidence.parse_expanded_env_samples(root)


def _parse_package_scripts(root: Path) -> dict[str, str]:
    pkg_path = root / "package.json"
    if pkg_path.is_file():
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8", errors="ignore"))
            scripts = data.get("scripts")
            if isinstance(scripts, dict):
                from services.apps_engine.doc_evidence import redact_secret_values
                return {k: redact_secret_values(v) for k, v in scripts.items() if isinstance(v, str)}
        except Exception:
            pass
    return {}


def _detect_storage_mounts(framework: str, databases: list[dict], files: set[str], text: str) -> list[dict[str, str]]:
    """Detect persistent volume storage paths."""
    mounts: list[dict[str, str]] = []
    lower = text.lower()
    
    # 1. SQLite persistence
    if "sqlite" in lower or any(d.get("kind") == "sqlite" for d in databases):
        mounts.append({"label": "data", "mount_path": "/app/data", "reason": "Persistent SQLite storage"})
    
    # 2. Framework-specific persistent mounts
    if framework == "Ghost":
        mounts.append({"label": "content", "mount_path": "/var/lib/ghost/content", "reason": "Ghost uploads and themes"})
    elif framework == "Strapi":
        mounts.append({"label": "uploads", "mount_path": "/app/public/uploads", "reason": "Media and file uploads"})
    elif framework == "PocketBase":
        mounts.append({"label": "pb-data", "mount_path": "/pb_data", "reason": "PocketBase database and uploads"})
    elif framework == "n8n":
        mounts.append({"label": "n8n-data", "mount_path": "/home/node/.n8n", "reason": "n8n workflow and credential storage"})
    elif "uploads" in lower or "upload_dir" in lower:
        mounts.append({"label": "uploads", "mount_path": "/app/uploads", "reason": "Application uploads storage"})
        
    return mounts


def _parse_compose_details(root: Path) -> dict[str, object]:
    compose_file = _find_compose(root)
    if not compose_file:
        return {}
    from services.apps_engine.compose_evidence import inspect_compose_evidence
    return inspect_compose_evidence(compose_file)


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
    "clickhouse":   ("clickhouse", "click_house"),
    "sqlite":       ("sqlite", "sqlite3"),
}


def _text_markers(lower_text: str) -> list[str]:
    return [kind for kind, tokens in _TEXT_MARKERS.items() if any(t in lower_text for t in tokens)]


def _databases(text: str) -> list[str]:
    """Compatibility helper returning database kinds from text markers."""
    return _text_markers(text.lower())


def _runtime(files: set[str]) -> str:
    if "package.json" in files:
        return "Node.js"
    if "mix.exs" in files:
        return "Elixir"
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


def _port(text: str, runtime: str, framework: str = "", compose_info: dict | None = None) -> int:
    if compose_info and compose_info.get("detected_ports"):
        for p in compose_info["detected_ports"]:
            if 1 <= p <= 65535:
                return p
    for pattern in (r"EXPOSE\s+(\d{2,5})", r"(?:--port|port\s*[=:])\s*(\d{2,5})", r"PORT\s*\|\|\s*(\d{2,5})"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 65535:
                return value
    if framework == "Ghost":
        return 2368
    if framework == "Strapi":
        return 1337
    if framework == "PocketBase":
        return 8090
    if framework == "n8n":
        return 5678
    if framework in ("Next.js", "Nuxt", "Remix", "Astro", "SvelteKit"):
        return 3000
    if framework == "Django":
        return 8000
    if framework == "FastAPI":
        return 8000
    if framework == "Flask":
        return 5000
    return {"Python": 8000, "PHP": 8080, "Go": 8080, "Java": 8080, "Elixir": 4000, "Static site": 80}.get(runtime, 3000)


def _read_sources(root: Path) -> str:
    names = (
        "Dockerfile", "Procfile", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "railpack.json", "nixpacks.toml", "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock",
        "Gemfile", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
        "mix.exs", "mix.lock", "config/runtime.exs", "config/config.exs", "config/prod.exs",
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
