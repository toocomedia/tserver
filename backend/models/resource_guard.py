"""Persistent configuration and priority overrides for Resource Guard."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ResourceGuardSettings(Base):
    __tablename__ = "resource_guard_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    memory_limit_percent: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ResourceGuardPriority(Base):
    __tablename__ = "resource_guard_priorities"
    __table_args__ = (
        UniqueConstraint("component_type", "component_id", name="uq_resource_guard_priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_type: Mapped[str] = mapped_column(String(32), nullable=False)
    component_id: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
