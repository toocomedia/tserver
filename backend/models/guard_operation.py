"""Persistent Resource Guard operation records."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class GuardOperation(Base):
    __tablename__ = "guard_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component_type: Mapped[str] = mapped_column(String(32), nullable=False)
    component_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    reserved_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="preflight", nullable=False, index=True)
    queue_position: Mapped[int | None] = mapped_column(Integer)
    preflight_result: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    current_ram_mb: Mapped[int | None] = mapped_column(Integer)
    peak_ram_mb: Mapped[int | None] = mapped_column(Integer)
    current_cpu: Mapped[float | None] = mapped_column()
    deployment_id: Mapped[int | None] = mapped_column(Integer, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
