"""Append-only audit records for managed app file operations."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class FileManagerEvent(Base):
    __tablename__ = "file_manager_events"
    __table_args__ = (
        Index("ix_file_manager_events_app_created", "app_id", "created_at"),
        Index("ix_file_manager_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    app_id: Mapped[int] = mapped_column(Integer, nullable=False)
    root_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    item_count: Mapped[int | None] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
