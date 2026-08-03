"""Metadata for a locally stored Railpack database or WordPress backup."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ContainerAppBackup(Base):
    __tablename__ = "container_app_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), index=True)
    database_id: Mapped[int | None] = mapped_column(ForeignKey("container_app_databases.id"))
    database_backup_id: Mapped[int | None] = mapped_column(ForeignKey("container_app_backups.id"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="complete", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
