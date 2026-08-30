"""Static repository inspection and Context Pack Collector for App Engine.

Collects raw facts (README setup sections, .env samples, and manifest files)
so the AI and deployment pipelines have direct, unadulterated source evidence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dependencies.git import repository_service

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

_COMPOSE_IMAGE_DB: dict[str, str] = {
    "postgres": "postgresql",
    "mariadb": "mariadb/mysql",
    "mysql": "mariadb/mysql",
    "mongo": "mongodb",
    "redis": "redis",
    "clickhouse": "clickhouse",
}

MANIFEST_FILENAMES = (
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "Dockerfile", "package.json", "mix.exs", "requirements.txt", "pyproject.toml",
    "Cargo.toml", "go.mod", "composer.json", "Gemfile", ".env.example", ".env.sample",
    "README.md", "INSTALL.md", "GUIDE.md",
)


def _parse_github_slug(url: str) -> tuple[str, str] | None:
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip(), re.IGNORECASE)
    if m:
        return m.group(1), m.group(2)
    return None


def _try_fast_raw_inspect(repository_url: str, branch: str = "main") -> dict[str, object] | None:
    """Fast probe: fetch manifests directly via raw GitHub HTTP before falling back to full git clone."""
    import tempfile
    import urllib.request

    slug = _parse_github_slug(repository_url)
    if not slug:
        return None
    owner, repo = slug
    target_branch = branch.strip() or "main"
    branches_to_try = [target_branch]
    if target_branch not in ("main", "master"):
        branches_to_try.extend(["main", "master"])
    elif target_branch == "main":
        branches_to_try.append("master")

    for br in branches_to_try:
        found_any = False
        with tempfile.TemporaryDirectory(prefix="srv-fast-inspect-") as temp_dir:
            temp_path = Path(temp_dir)
            for fname in MANIFEST_FILENAMES:
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{br}/{fname}"
                try:
                    req = urllib.request.Request(raw_url, headers={"User-Agent": "Barq-Apps-Engine"})
                    with urllib.request.urlopen(req, timeout=2.0) as resp:
                        if resp.status == 200:
                            content = resp.read()
                            (temp_path / fname).write_bytes(content)
                            found_any = True
                except Exception:
                    continue

            if found_any:
                return _analyze_directory(temp_path, repository_url, br)
    return None


def inspect_repository(repository_url: str, branch: str = "main", *, ssh_key_path: str | Path | None = None) -> dict[str, object]:
    """Inspect repository files and compile raw context pack for the deployment pipeline."""
    if not ssh_key_path and repository_url.strip().lower().startswith("https://github.com/"):
        try:
            fast_res = _try_fast_raw_inspect(repository_url, branch)
            if fast_res:
                return fast_res
        except Exception:
            pass

    with repository_service.temporary_clone(repository_url, branch, allow_default_branch=True, ssh_key_path=ssh_key_path) as checkout:
        return _analyze_directory(checkout.path, checkout.repository_url, checkout.branch)


def _analyze_directory(root: Path, repository_url: str, branch: str) -> dict[str, object]:
    files = {path.name for path in root.iterdir() if path.is_file()}
    runtime = _detect_runtime(files)
    has_dockerfile = "Dockerfile" in files
    build_mode = "dockerfile" if has_dockerfile else "railpack"

    # 1. Extract markdown setup instructions
    from services.apps_engine import doc_evidence
    env_sample = doc_evidence.parse_expanded_env_samples(root)
    documentation_evidence = doc_evidence.find_install_instructions(root, env_sample=env_sample)

    # 2. Parse Compose services if present
    compose_file = _find_compose(root)
    compose_info: dict[str, Any] = {}
    if compose_file:
        from services.apps_engine.compose_evidence import inspect_compose_evidence
        compose_info = inspect_compose_evidence(compose_file) or {}

    # 3. Collect raw manifest text snippets for the AI context pack
    manifest_snippets: dict[str, str] = {}
    for fname in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "package.json", "mix.exs", "requirements.txt", "pyproject.toml", "Cargo.toml", "go.mod"):
        p = root / fname
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                # Keep first 120 lines to keep context pack lean and fast
                lines = content.splitlines()[:120]
                manifest_snippets[fname] = "\n".join(lines)
            except Exception:
                pass

    # 4. Check for official image advice
    from services.apps_engine.source_image_advisor import advise_official_image
    advice = advise_official_image(repository_url, runtime)
    if not advice and compose_info.get("services"):
        for svc in compose_info["services"]:
            img = str(svc.get("image") or "").strip()
            if img and not any(db in img.lower() for db in ("postgres", "mysql", "mariadb", "redis", "mongo", "clickhouse", "kafka")):
                advice = {
                    "image": img,
                    "reason": f"Discovered official pre-built image '{img}' in docker-compose.yml",
                    "source": "compose",
                }
                break

    # 5. Extract datastores from compose and image references
    databases: list[dict[str, str]] = []
    db_types: list[str] = []
    if compose_file:
        for kind in _compose_databases(compose_file):
            if kind not in db_types:
                db_types.append(kind)
                databases.append({"kind": kind, "confidence": CONFIDENCE_HIGH, "reason": "docker-compose.yml service image"})

    # 6. Available panel capabilities
    panel_capabilities: list[dict[str, Any]] = []
    try:
        from services.apps_engine import database_provider_capabilities
        panel_capabilities = database_provider_capabilities.provider_capabilities(force=True)
    except Exception:
        pass

    internal_port = _detect_port(root, compose_info, runtime)

    res_dict: dict[str, object] = {
        "repository_url": repository_url,
        "branch": branch,
        "runtime": runtime,
        "framework": runtime,
        "build_mode": build_mode,
        "has_dockerfile": has_dockerfile,
        "internal_port": internal_port,
        "database_types": db_types,
        "database_detected": bool(db_types),
        "database_detections": databases,
        "database_suggestions": [],
        "env_sample": doc_evidence.ai_safe_env_sample(env_sample),
        "documentation_evidence": documentation_evidence,
        "storage_mount_suggestions": [],
        "package_scripts": {},
        "compose_info": compose_info,
        "manifest_snippets": manifest_snippets,
        "panel_capabilities": panel_capabilities,
        "context_pack": {
            "runtime": runtime,
            "manifests": list(manifest_snippets.keys()),
            "manifest_snippets": manifest_snippets,
            "env_keys": list(env_sample.keys()) if env_sample else [],
            "compose_services": [s.get("name") for s in compose_info.get("services", [])] if compose_info else [],
            "official_image": advice.get("image") if advice else None,
        },
    }
    if advice:
        res_dict["official_image_recommendation"] = advice
        res_dict["official_image_available"] = advice.get("image")
        res_dict["summary"] = f"Official pre-built image '{advice.get('image')}' detected in repository. Recommended over compiling from source."

    return res_dict


def _find_compose(root: Path) -> Path | None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        p = root / name
        if p.is_file():
            return p
    return None


def _compose_databases(compose_file: Path) -> list[str]:
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


def _detect_runtime(files: set[str]) -> str:
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


def _detect_port(root: Path, compose_info: dict, runtime: str) -> int:
    if compose_info and compose_info.get("detected_ports"):
        for p in compose_info["detected_ports"]:
            if 1 <= p <= 65535:
                return p
    dockerfile = root / "Dockerfile"
    if dockerfile.is_file():
        try:
            txt = dockerfile.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"EXPOSE\s+(\d{2,5})", txt, re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 65535:
                    return val
        except Exception:
            pass
    return {"Python": 8000, "PHP": 8080, "Go": 8080, "Java": 8080, "Elixir": 4000, "Static site": 80}.get(runtime, 3000)
