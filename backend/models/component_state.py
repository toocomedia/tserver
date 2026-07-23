"""Persistent desired state and lifecycle audit data for plugins/dependencies."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ComponentState(Base):
    __tablename__ = "component_states"
    __table_args__ = (
        UniqueConstraint("component_type", "component_id", name="uq_component_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_type: Mapped[str] = mapped_column(String(32), nullable=False)
    component_id: Mapped[str] = mapped_column(String(64), nullable=False)
    desired_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    operation: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    install_origin: Mapped[str] = mapped_column(String(32), default="bundled", nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
