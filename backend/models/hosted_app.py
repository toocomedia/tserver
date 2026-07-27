"""Hosted Python application metadata; secrets stay in the environment file."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base

class HostedApp(Base):
    __tablename__ = "hosted_apps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    repository_url: Mapped[str | None] = mapped_column(String(512))
    branch: Mapped[str | None] = mapped_column(String(128))
    runtime: Mapped[str] = mapped_column(String(32), default="python", nullable=False)
    build_command: Mapped[str] = mapped_column(Text, nullable=False)
    start_command: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    service_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    work_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    env_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    paused_by_dependency: Mapped[str | None] = mapped_column(String(64))
    postgres_mode: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    database_name: Mapped[str | None] = mapped_column(String(63))
    database_user: Mapped[str | None] = mapped_column(String(63))
    ssl_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    deployed_revision: Mapped[str | None] = mapped_column(String(64))
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime)
    available_revision: Mapped[str | None] = mapped_column(String(64))
    available_revision_message: Mapped[str | None] = mapped_column(String(512))
    available_revision_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    active_release: Mapped[str | None] = mapped_column(String(128))
    previous_release: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
