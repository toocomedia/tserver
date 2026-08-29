"""Template and Compose YAML export endpoints for Railpack applications."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.container_app import ContainerApp
from models.container_app_snapshot import ContainerAppSnapshot
from services.apps_engine import template_export
from services.apps_engine.app_spec import AppSpec, ServiceSpec

router = APIRouter()


async def _app(db: AsyncSession, app_id: int) -> ContainerApp:
    app = await db.get(ContainerApp, app_id)
    if not app:
        raise HTTPException(404, "Application not found.")
    return app


async def _resolve_compose_yaml(db: AsyncSession, app: ContainerApp) -> tuple[str, str]:
    """Return (yaml_content, filename) for an app."""
    import json
    from pathlib import Path
    from services import container_app_database_service
    from services.apps_engine.app_spec import AppSpec, ServiceSpec, VolumeSpec, HealthCheckSpec

    snapshot = None
    if app.active_snapshot_id:
        snapshot = await db.get(ContainerAppSnapshot, app.active_snapshot_id)
    elif app.pending_snapshot_id:
        snapshot = await db.get(ContainerAppSnapshot, app.pending_snapshot_id)

    app_name = app.container_name or f"app-{app.id}"
    filename = f"{app_name}-compose.yml"

    # 1. Try resolving AppSpec from candidate/active snapshot
    if snapshot:
        from services.apps_engine import app_spec_snapshots
        try:
            spec = app_spec_snapshots.app_spec_for(snapshot)
            yaml_str = template_export.app_spec_to_compose_yaml(spec)
            header_comments = [f"# Application: {spec.display_name or app_name.title()}"]
            if getattr(spec, "docs_url", ""):
                header_comments.append(f"# Documentation: {spec.docs_url}")
            if getattr(spec, "post_install_message", ""):
                header_comments.append(f"# Initial Setup: {spec.post_install_message}")
            if header_comments:
                yaml_str = "\n".join(header_comments) + "\n\n" + yaml_str
            return yaml_str, filename
        except Exception:
            pass

    # 2. Build complete AppSpec for single-container app including attached databases and volumes
    image = app.image_reference or "app:latest"
    ports = [app.internal_port] if app.internal_port else [80]

    # Real environment variables
    env_map: dict[str, str] = {}
    if app.env_path and Path(app.env_path).is_file():
        try:
            for line in Path(app.env_path).read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, _, v = line.partition("=")
                    if k.strip():
                        env_map[k.strip()] = v.strip()
        except Exception:
            pass

    # Storage volumes
    volumes: list[VolumeSpec] = []
    if app.storage_mounts:
        try:
            parsed_mounts = json.loads(app.storage_mounts)
            for idx, m in enumerate(parsed_mounts):
                if isinstance(m, dict) and m.get("mount_path"):
                    lbl = str(m.get("label") or f"data{idx+1}").strip()
                    volumes.append(VolumeSpec(name_suffix=lbl, container_mount_path=str(m["mount_path"]).strip()))
        except Exception:
            pass
    if app.data_volume and app.data_mount_path:
        volumes.append(VolumeSpec(name_suffix="data", container_mount_path=str(app.data_mount_path).strip()))

    # Health check
    health = None
    if app.health_path and app.health_path != "disabled":
        health = HealthCheckSpec(probe_type="http", http_path=app.health_path, http_port=ports[0])

    # Attached databases
    databases = await container_app_database_service.attachments_for(db, app.id)
    depends: list[str] = []
    db_services: dict[str, ServiceSpec] = {}

    for idx, db_item in enumerate(databases):
        kind = db_item.kind
        svc_name = "db" if idx == 0 and len(databases) == 1 else f"db_{kind}"
        depends.append(svc_name)
        db_img = container_app_database_service.IMAGES.get(kind, f"{kind}:latest")
        db_ports = [5432] if kind == "postgresql" else [3306] if kind == "mariadb" else [6379] if kind == "redis" else [27017]
        db_env: dict[str, str] = {}
        db_vol: list[VolumeSpec] = []

        clean_db_name = (getattr(db_item, "database_name", None) or app_name).replace("-", "_")
        clean_user = (getattr(db_item, "username", None) or clean_db_name).replace("-", "_")
        if kind == "postgresql":
            db_env = {"POSTGRES_DB": clean_db_name, "POSTGRES_USER": clean_user, "POSTGRES_PASSWORD": "${DB_PASSWORD:-changeme}"}
            db_vol = [VolumeSpec(name_suffix=f"{svc_name}_data", container_mount_path="/var/lib/postgresql/data")]
        elif kind == "mariadb":
            db_env = {"MYSQL_DATABASE": clean_db_name, "MYSQL_USER": clean_user, "MYSQL_PASSWORD": "${DB_PASSWORD:-changeme}", "MYSQL_ROOT_PASSWORD": "${DB_ROOT_PASSWORD:-rootpass}"}
            db_vol = [VolumeSpec(name_suffix=f"{svc_name}_data", container_mount_path="/var/lib/mysql")]
        elif kind == "redis":
            db_vol = [VolumeSpec(name_suffix=f"{svc_name}_data", container_mount_path="/data")]
        elif kind == "mongodb":
            db_vol = [VolumeSpec(name_suffix=f"{svc_name}_data", container_mount_path="/data/db")]

        db_services[svc_name] = ServiceSpec(
            name=svc_name,
            image_reference=db_img,
            internal_ports=tuple(db_ports),
            environment_defaults=db_env,
            volumes=db_vol,
        )

        # Adapt web environment to use the local Compose db service instead of host.docker.internal
        for k in ("DATABASE_URL", "MYSQL_URL", "REDIS_URL", "MONGODB_URI"):
            if k in env_map and "@host.docker.internal:" in env_map[k]:
                env_map[k] = env_map[k].replace("@host.docker.internal:", f"@{svc_name}:")
        if "DB_HOST" in env_map and env_map["DB_HOST"] == "host.docker.internal":
            env_map["DB_HOST"] = svc_name

    web_service = ServiceSpec(
        name=app_name,
        image_reference=image,
        internal_ports=tuple(ports),
        environment_defaults=env_map,
        volumes=volumes,
        health_check=health,
        depends_on=tuple(depends),
    )

    all_services = {app_name: web_service, **db_services}

    fallback_spec = AppSpec(
        name=app_name,
        display_name=app_name.title(),
        web_service_name=app_name,
        web_port=ports[0],
        services=all_services,
    )
    yaml_str = template_export.app_spec_to_compose_yaml(fallback_spec)

    # Prepend header comments from snapshot if available
    header_comments = [f"# Application: {app_name.title()}"]
    if snapshot and snapshot.config_json:
        try:
            cfg = json.loads(snapshot.config_json)
            if isinstance(cfg, dict):
                post_msg = cfg.get("post_install_message") or (cfg.get("app_spec") or {}).get("post_install_message")
                if post_msg:
                    header_comments.append(f"# Initial Setup: {post_msg}")
                for note in cfg.get("setup_notes") or []:
                    header_comments.append(f"# Note: {note}")
                for cmd in cfg.get("admin_commands") or []:
                    c_text = cmd.get("command") if isinstance(cmd, dict) else str(cmd)
                    if c_text:
                        header_comments.append(f"# Admin Command: {c_text}")
        except Exception:
            pass

    if header_comments:
        yaml_str = "\n".join(header_comments) + "\n\n" + yaml_str

    return yaml_str, filename


@router.get("/{app_id}/compose-template")
async def download_compose_template(app_id: int, db: AsyncSession = Depends(get_db)):
    """Direct file download of standard docker-compose.yml."""
    app = await _app(db, app_id)
    yaml_str, filename = await _resolve_compose_yaml(db, app)
    return Response(
        content=yaml_str,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{app_id}/compose-template/raw")
async def raw_compose_template(app_id: int, db: AsyncSession = Depends(get_db)):
    """JSON response containing the raw YAML string for in-browser copying."""
    app = await _app(db, app_id)
    yaml_str, filename = await _resolve_compose_yaml(db, app)
    return JSONResponse({
        "status": "ok",
        "filename": filename,
        "yaml": yaml_str,
    })
