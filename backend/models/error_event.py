"""
models/error_event.py — Admin error tracker ORM model
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, Text, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class ErrorEvent(Base):
    __tablename__ = "error_events"
    __table_args__ = (
        Index("ix_error_events_resolved_created", "resolved", "created_at"),
        Index("ix_error_events_source_created", "source", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # error | warning | critical
    level: Mapped[str] = mapped_column(String(16), default="error", nullable=False)
    # domain | dns | ssl | proxy | nginx | powerdns | system | http
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    request_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
