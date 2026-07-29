"""Safe, static Python-project inspection used before hosted-app deployment."""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ENV_RE = re.compile(r"\b(?:getenv|environ\.get)\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)")
ENV_INDEX_RE = re.compile(r"environ\s*\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)")
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
SAMPLE_ENV_FILES = (".env.example", ".env.sample", ".env.template", "example.env")


def detect_project(root: Path) -> dict[str, object]:
    files = _files(root)
    package_manager, build = _package_manager(root)
    explicit, procfile = _explicit_entrypoint(root)
    candidates = _candidates(root, files)
    entrypoints = ([explicit] if explicit else []) + candidates
    entrypoints = list(dict.fromkeys(entrypoints))
    framework = _framework(root, files, entrypoints)
    start = _start_command(framework, entrypoints, procfile, package_manager)
    env_names, required_env = _environment_names(root, files)
    text = _project_text(root, files)
    database_evidence, postgres = _database_evidence(text)
    database_url_scheme = "postgresql+asyncpg" if "create_async_engine" in text else "postgresql"
    sqlite = any(token in text.lower() for token in ("sqlite", "sqlite3"))
    conda = (root / "environment.yml").exists() or (root / "environment.yaml").exists()
    warnings = []
    if conda: warnings.append("Conda projects are not supported by lightweight hosting.")
    if not entrypoints and not procfile: warnings.append("No supported web entrypoint was found.")
    if len(entrypoints) > 1: warnings.append("More than one web entrypoint was found.")
    if required_env: warnings.append("Project environment values need review.")
    if database_evidence: warnings.append("Database configuration needs review.")
    can_quick = bool(build and start and len(entrypoints) <= 1 and not conda and not required_env and not postgres)
    return {
        "framework": framework, "package_manager": package_manager,
        "build_command": build, "start_command": start or "",
        "entrypoints": entrypoints, "environment_names": sorted(env_names),
        "required_environment_names": sorted(required_env), "postgres_suspected": postgres,
        "environment_keys": [
            {"name": name, "required": name in required_env}
            for name in sorted(env_names)
        ],
        "database_evidence": database_evidence,
        "managed_postgres_recommended": postgres,
        "database_url_scheme": database_url_scheme,
        "sqlite_suspected": sqlite, "can_quick_deploy": can_quick,
        "warnings": warnings,
    }


def _files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if not any(part in SKIP_DIRS for part in path.parts)]


def _package_manager(root: Path) -> tuple[str, str]:
    if (root / "requirements.txt").exists(): return "pip", "pip install -r requirements.txt"
    if (root / "poetry.lock").exists(): return "poetry", "pip install poetry && poetry install --only main --no-interaction"
    if (root / "uv.lock").exists(): return "uv", "pip install uv && uv sync --active --no-dev"
    if (root / "Pipfile").exists(): return "pipenv", "pip install pipenv && pipenv sync --system"
    if (root / "pyproject.toml").exists(): return "pyproject", "pip install ."
    return "unknown", ""


def _explicit_entrypoint(root: Path) -> tuple[str | None, str | None]:
    entrypoint = None
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try: entrypoint = tomllib.loads(_read(pyproject)).get("tool", {}).get("fastapi", {}).get("entrypoint")
        except (tomllib.TOMLDecodeError, AttributeError): pass
    procfile = root / "Procfile"
    if procfile.exists():
        for line in _read(procfile).splitlines():
            if line.startswith("web:"): return entrypoint, line.removeprefix("web:").strip()
    return entrypoint, None


def _candidates(root: Path, files: list[Path]) -> list[str]:
    found = []
    for path in files:
        if path.name in {"asgi.py", "wsgi.py"}:
            found.append(f"{_module(root, path)}:application")
        try: tree = ast.parse(_read(path))
        except SyntaxError: continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)): continue
            value = node.value
            if not isinstance(value, ast.Call) or not _call_name(value).endswith(("FastAPI", "Flask")): continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name): found.append(f"{_module(root, path)}:{target.id}")
    return found


def _framework(root: Path, files: list[Path], entrypoints: list[str]) -> str:
    if (root / "manage.py").exists(): return "Django"
    text = "\n".join(_read(file).lower() for file in files)
    if "fastapi" in text or entrypoints: return "FastAPI"
    if "flask" in text: return "Flask"
    return "Python"


def _start_command(framework: str, entries: list[str], procfile: str | None, manager: str) -> str | None:
    if procfile: return procfile
    if framework == "Django":
        entry = next((item for item in entries if item.endswith(":application")), None)
        return f"uvicorn {entry} --host $HOST --port $PORT" if entry else None
    if not entries: return None
    command = "uvicorn" if framework == "FastAPI" else "gunicorn"
    prefix = "uv run " if manager == "uv" else "pipenv run " if manager == "pipenv" else ""
    if framework == "Flask": return f"{prefix}{command} --bind $HOST:$PORT {entries[0]}"
    return f"{prefix}{command} {entries[0]} --host $HOST --port $PORT"


def _environment_names(root: Path, files: list[Path]) -> tuple[set[str], set[str]]:
    names, required = set(), set()
    for sample_name in SAMPLE_ENV_FILES:
        sample = root / sample_name
        if sample.exists():
            for line in _read(sample).splitlines():
                value = line.split("#", 1)[0].strip()
                if not value or "=" not in value: continue
                key, sample_value = (part.strip() for part in value.split("=", 1))
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                    names.add(key)
                    if not sample_value: required.add(key)
    for path in files:
        text = _read(path)
        names.update(ENV_RE.findall(text))
        indexed = ENV_INDEX_RE.findall(text)
        names.update(indexed)
        required.update(indexed)
    return names, required


def _project_text(root: Path, files: list[Path]) -> str:
    extras = [root / name for name in (
        "requirements.txt", "pyproject.toml", "Pipfile", *SAMPLE_ENV_FILES,
    )]
    return "\n".join(_read(path) for path in [*files, *extras] if path.is_file())


def _database_evidence(text: str) -> tuple[list[str], bool]:
    lower = text.lower()
    evidence = []
    postgres_tokens = {
        "asyncpg": "asyncpg PostgreSQL driver",
        "psycopg": "psycopg PostgreSQL driver",
        "postgresql": "PostgreSQL configuration",
        "postgres": "PostgreSQL configuration",
    }
    for token, label in postgres_tokens.items():
        if token in lower and label not in evidence:
            evidence.append(label)
    if "database_url" in lower:
        evidence.append("DATABASE_URL configuration")
    if "sqlite" in lower or "sqlite3" in lower:
        evidence.append("SQLite configuration")
    return evidence, any("PostgreSQL" in item for item in evidence)


def _module(root: Path, path: Path) -> str:
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _read(path: Path) -> str:
    try: return path.read_text(encoding="utf-8", errors="ignore")[:500_000]
    except OSError: return ""
