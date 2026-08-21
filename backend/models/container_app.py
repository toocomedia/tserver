"""Metadata for a Docker/Railpack web application."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ContainerApp(Base):
    __tablename__ = "container_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)  # git | image
    build_mode: Mapped[str] = mapped_column(String(16), nullable=False)  # railpack | dockerfile | image
    repository_url: Mapped[str | None] = mapped_column(String(512))
    branch: Mapped[str | None] = mapped_column(String(128))
    image_reference: Mapped[str | None] = mapped_column(String(512))
    image_digest: Mapped[str | None] = mapped_column(String(512))
    previous_image: Mapped[str | None] = mapped_column(String(512))
    container_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    internal_port: Mapped[int] = mapped_column(Integer, nullable=False)
    host_port: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    env_path: Mapped[str] = mapped_column(String(512), nullable=False)
    data_volume: Mapped[str | None] = mapped_column(String(128))
    data_mount_path: Mapped[str | None] = mapped_column(String(512))
    storage_mounts: Mapped[str | None] = mapped_column(Text)
    git_ref: Mapped[str | None] = mapped_column(String(128))
    git_ref_type: Mapped[str] = mapped_column(String(16), default="branch", nullable=False)
    deploy_key_path: Mapped[str | None] = mapped_column(String(512))
    deploy_key_public: Mapped[str | None] = mapped_column(Text)
    root_directory: Mapped[str | None] = mapped_column(String(255), default="")
    dockerfile_path: Mapped[str | None] = mapped_column(String(255), default="Dockerfile")
    build_args: Mapped[str | None] = mapped_column(Text)
    build_secret_keys: Mapped[str | None] = mapped_column(Text)
    custom_start_command: Mapped[str | None] = mapped_column(Text)
    health_path: Mapped[str] = mapped_column(String(255), default="/", nullable=False)
    startup_timeout_seconds: Mapped[int] = mapped_column(Integer, default=45, nullable=False)
    database_mode: Mapped[str] = mapped_column(String(24), default="none", nullable=False)
    database_provider: Mapped[str | None] = mapped_column(String(64))
    database_name: Mapped[str | None] = mapped_column(String(63))
    database_user: Mapped[str | None] = mapped_column(String(63))
    preset: Mapped[str | None] = mapped_column(String(24))
    wordpress_content_volume: Mapped[str | None] = mapped_column(String(128))
    wordpress_site_title: Mapped[str | None] = mapped_column(String(255))
    wordpress_admin_user: Mapped[str | None] = mapped_column(String(64))
    wordpress_admin_email: Mapped[str | None] = mapped_column(String(255))
    wordpress_pending_secret_path: Mapped[str | None] = mapped_column(String(512))
    pending_database_specs: Mapped[str | None] = mapped_column(Text)
    cpu_limit: Mapped[str] = mapped_column(String(16), default="1.0", nullable=False)
    memory_limit_mb: Mapped[int] = mapped_column(Integer, default=512, nullable=False)
    pid_limit: Mapped[int] = mapped_column(Integer, default=256, nullable=False)
    ssl_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    deployed_revision: Mapped[str | None] = mapped_column(String(64))
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
