"""Trusted administrator Python-app deployment orchestration."""
from __future__ import annotations
import asyncio
import os, re, secrets, shutil, subprocess, tempfile, zipfile
from pathlib import Path, PurePosixPath
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import config
from dependencies import dependency_manager
from models.hosted_app import HostedApp
from services import nginx_service
from services import app_hosting_health_service
from services import app_project_detector
from utils import shell

ROOT = Path(config.APP_HOSTING_ROOT)
ENV_ROOT = Path(config.APP_HOSTING_ENV_ROOT)
GIT_URL_RE = re.compile(r"^(https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+|git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+)$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

def _app_dir(app_id: int) -> Path: return ROOT / str(app_id)
def _service_unit(app: HostedApp) -> Path: return Path("/etc/systemd/system") / f"{app.service_name}.service"
def suggest_project(path: Path) -> dict[str, object]: return app_project_detector.detect_project(path)

def _github_https_url(repository_url: str) -> str | None:
    match = re.fullmatch(r"git@github\.com:([A-Za-z0-9._/-]+)", repository_url)
    return f"https://github.com/{match.group(1)}" if match else None

def _clone_error(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout or "Git clone failed.").strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return " ".join(lines[-3:])[:500] if lines else "Git clone failed without an error message."

def _ensure_runtime_dirs() -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        ENV_ROOT.mkdir(parents=True, exist_ok=True)
        ROOT.chmod(0o700)
        ENV_ROOT.chmod(0o700)
    except PermissionError as exc:
        raise HTTPException(500, "Python app storage is not ready. Run the panel update on the VPS.") from exc

def _normalise_paths(app: HostedApp) -> None:
    app.work_dir = str(_app_dir(app.id))
    if app.env_path.startswith("/etc/srv-panel/"):
        app.env_path = str(ENV_ROOT / f"{app.id}.env")

async def _systemctl(*args: str, allow_missing: bool = False) -> bool:
    result = await shell.run(["systemctl", *args], timeout=30)
    message = result.stderr or result.stdout
    if allow_missing and any(text in message.lower() for text in ("not loaded", "not found", "does not exist")):
        return False
    if not result.success:
        raise HTTPException(500, message or "System service action failed.")
    return True

async def _progress(reporter, stage: str, message: str) -> None:
    if reporter: await reporter(stage, message)

def inspect_repository(repository_url: str, branch: str) -> dict[str, object]:
    """Read project files from a temporary shallow clone; never run app code."""
    if not GIT_URL_RE.fullmatch(repository_url):
        raise HTTPException(400, "Enter a valid HTTPS or SSH Git repository URL.")
    if not BRANCH_RE.fullmatch(branch):
        raise HTTPException(400, "Enter a valid branch name.")
    if not dependency_manager.is_healthy("git"):
        raise HTTPException(409, "Git & SSH dependency is required.")
    with tempfile.TemporaryDirectory(prefix="srv-panel-inspect-") as temp_dir:
        source_dir = Path(temp_dir) / "source"
        clone_command = ["git", "clone", "--depth", "1", "--branch", branch, repository_url, str(source_dir)]
        result = subprocess.run(
            clone_command,
            capture_output=True, text=True, timeout=90, check=False,
        )
        if result.returncode:
            fallback_url = _github_https_url(repository_url)
            if fallback_url:
                shutil.rmtree(source_dir, ignore_errors=True)
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", branch, fallback_url, str(source_dir)],
                    capture_output=True, text=True, timeout=90, check=False,
                )
                if not result.returncode:
                    project = suggest_project(source_dir)
                    project["repository_url"] = fallback_url
                    project["transport_note"] = "SSH was unavailable, so this public GitHub repository will use HTTPS."
                    return project
            raise HTTPException(400, f"Repository check failed: {_clone_error(result)}")
        project = suggest_project(source_dir)
        project["repository_url"] = repository_url
        return project

async def next_port(db: AsyncSession) -> int:
    used = set((await db.scalars(select(HostedApp.port))).all())
    return next(port for port in range(config.APP_HOSTING_PORT_START, 65536) if port not in used)

async def next_service_name(db: AsyncSession, domain_id: int) -> str:
    names = set((await db.scalars(select(HostedApp.service_name))).all())
    base = f"srv-python-{domain_id}"
    if base not in names:
        return base
    while True:
        candidate = f"{base}-{secrets.token_hex(3)}"
        if candidate not in names:
            return candidate

async def create_app(db: AsyncSession, domain_id: int, source_type: str, repository_url: str | None, branch: str, build: str, start: str, ssl: bool, postgres_mode: str, external_url: str | None) -> HostedApp:
    if source_type not in {"git", "zip"} or postgres_mode not in {"none", "create", "external"}: raise HTTPException(400, "Invalid app setup.")
    if source_type == "git" and (not repository_url or not dependency_manager.is_healthy("git")): raise HTTPException(409, "Git & SSH dependency is required.")
    if not dependency_manager.is_healthy("python"): raise HTTPException(409, "Python Runtime dependency is required.")
    if await db.scalar(select(HostedApp.id).where(HostedApp.domain_id == domain_id)):
        raise HTTPException(409, "This domain already has Python app setup. Open it from the domain page.")
    _ensure_runtime_dirs()
    port = await next_port(db); service_name = await next_service_name(db, domain_id)
    app = HostedApp(domain_id=domain_id, source_type=source_type, repository_url=repository_url, branch=branch or "main", build_command=build, start_command=start, port=port, service_name=service_name, work_dir=str(_app_dir(domain_id)), env_path=str(ENV_ROOT / f"{domain_id}.env"), ssl_requested=ssl, postgres_mode=postgres_mode)
    if postgres_mode == "external" and not external_url: raise HTTPException(400, "DATABASE_URL is required for an external database.")
    db.add(app); await db.flush()
    if postgres_mode == "external":
        ENV_ROOT.mkdir(parents=True, exist_ok=True)
        Path(app.env_path).write_text(f"DATABASE_URL={external_url}\n", encoding="utf-8")
        os.chmod(app.env_path, 0o600)
    return app

async def extract_zip(upload: UploadFile, app: HostedApp) -> Path:
    if not upload.filename or not upload.filename.lower().endswith(".zip"): raise HTTPException(400, "Upload a ZIP file.")
    data = await upload.read()
    if len(data) > 100 * 1024 * 1024: raise HTTPException(400, "ZIP is larger than 100 MB.")
    _ensure_runtime_dirs()
    archive = _app_dir(app.id) / "upload.zip"; archive.parent.mkdir(parents=True, exist_ok=True); archive.write_bytes(data)
    target = _app_dir(app.id) / "source"; shutil.rmtree(target, ignore_errors=True); target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > 1000: raise HTTPException(400, "ZIP has too many files.")
        for item in z.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts or (item.external_attr >> 16) & 0o170000 == 0o120000: raise HTTPException(400, "ZIP contains an unsafe path.")
        z.extractall(target)
    return target

async def clone_repo(app: HostedApp) -> Path:
    if not GIT_URL_RE.fullmatch(app.repository_url or "") or not BRANCH_RE.fullmatch(app.branch or ""):
        raise HTTPException(400, "The saved repository URL or branch is invalid.")
    target = _app_dir(app.id) / "source"; shutil.rmtree(target, ignore_errors=True); target.parent.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(subprocess.run, ["git", "clone", "--depth", "1", "--branch", app.branch or "main", app.repository_url or "", str(target)], capture_output=True, text=True, timeout=180, shell=False)
    if result.returncode: raise HTTPException(400, (result.stderr or "Git clone failed.")[-500:])
    return target

async def deploy(app: HostedApp, domain_name: str, reporter=None) -> None:
    _ensure_runtime_dirs()
    _normalise_paths(app)
    await _progress(reporter, "source", "Preparing application source.")
    source = await clone_repo(app) if app.source_type == "git" else _app_dir(app.id) / "source"
    if not source.exists(): raise HTTPException(400, "Upload application ZIP before deployment.")
    await _progress(reporter, "venv", "Creating isolated Python environment.")
    venv = _app_dir(app.id) / ".venv"; result = await asyncio.to_thread(subprocess.run, ["python3", "-m", "venv", str(venv)], capture_output=True, text=True, timeout=60)
    if result.returncode: raise HTTPException(500, result.stderr[-500:])
    ENV_ROOT.mkdir(parents=True, exist_ok=True)
    existing = Path(app.env_path).read_text(encoding="utf-8") if Path(app.env_path).exists() else ""
    existing = "".join(f"{line}\n" for line in existing.splitlines() if not line.startswith(("HOST=", "PORT=", "APP_DATA_DIR=")))
    if app.postgres_mode == "create" and not app.database_name:
        from plugins.postgres_manager import queries as pg
        app.database_name, app.database_user = f"app{app.id}", f"app{app.id}"
        password = secrets.token_urlsafe(24)
        pg.create_user(app.database_user, password); pg.create_database(app.database_name, app.database_user)
        existing += f"DATABASE_URL=postgresql://{app.database_user}:{password}@127.0.0.1:5432/{app.database_name}\n"
    data_dir = _app_dir(app.id) / "data"; data_dir.mkdir(parents=True, exist_ok=True)
    env = existing + f"HOST=127.0.0.1\nPORT={app.port}\nAPP_DATA_DIR={data_dir}\n"
    Path(app.env_path).write_text(env, encoding="utf-8"); os.chmod(app.env_path, 0o600)
    await _progress(reporter, "dependencies", "Installing project dependencies.")
    build = await asyncio.to_thread(subprocess.run, ["bash", "-lc", f"cd {source} && PATH={venv}/bin:$PATH {app.build_command}"], capture_output=True, text=True, timeout=600)
    if build.returncode: raise HTTPException(400, (build.stderr or build.stdout)[-1000:])
    await _progress(reporter, "service", "Creating and starting application service.")
    unit = f"[Unit]\nDescription=SRV Panel Python app {app.id}\nAfter=network.target\n\n[Service]\nType=simple\nUser={config.APP_HOSTING_USER}\nGroup={config.APP_HOSTING_USER}\nWorkingDirectory={source}\nEnvironmentFile={app.env_path}\nExecStart=/bin/bash -lc '{venv}/bin/{app.start_command}'\nRestart=on-failure\n\n[Install]\nWantedBy=multi-user.target\n"
    await shell.write_file(_service_unit(app), unit)
    await _systemctl("daemon-reload")
    await _systemctl("enable", "--now", app.service_name)
    await _progress(reporter, "listener", "Checking the private application port.")
    await app_hosting_health_service.wait_for_listener(app.port)
    await _progress(reporter, "nginx", "Enabling the Nginx proxy.")
    await nginx_service.create_proxy(domain_name, "127.0.0.1", app.port, "http")
    await nginx_service.reload()

async def control(app: HostedApp, action: str) -> None:
    if action not in {"start", "stop", "restart"}: raise HTTPException(400, "Invalid app action.")
    await _systemctl(action, app.service_name, allow_missing=action == "stop")
    app.status = "stopped" if action == "stop" else "running"

async def uninstall(app: HostedApp, domain_name: str | None) -> None:
    # Strict cleanup always stops first; pending deployments have no unit to stop.
    await _systemctl("stop", app.service_name, allow_missing=True)
    app.status = "stopped"
    await _systemctl("disable", "--now", app.service_name, allow_missing=True)
    await shell.remove_path(_service_unit(app))
    await _systemctl("daemon-reload")
    if domain_name:
        await nginx_service.remove_site(domain_name)
        await nginx_service.reload()
    shutil.rmtree(_app_dir(app.id), ignore_errors=True); Path(app.env_path).unlink(missing_ok=True)
