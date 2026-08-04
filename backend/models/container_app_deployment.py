"""Persisted progress for one container-app deployment."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ContainerAppDeployment(Base):
    __tablename__ = "container_app_deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    stage: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    action: Mapped[str] = mapped_column(String(16), default="deploy", nullable=False)
    profile: Mapped[str | None] = mapped_column(String(32))
    peak_ram_mb: Mapped[int | None] = mapped_column(Integer)
    guard_blocked_reason: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
