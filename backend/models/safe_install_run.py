"""Persistent record for one Safe Install Mode run."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SafeInstallRun(Base):
    __tablename__ = "safe_install_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # FK to guard_operations — the install operation this run is helping
    operation_id: Mapped[int] = mapped_column(Integer, ForeignKey("guard_operations.id"), nullable=False, index=True)
    # JSON list of all candidate services proposed to the user
    candidate_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON list of service IDs the user approved to stop
    approved_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # JSON list of service IDs that were actually stopped (subset of approved)
    services_stopped: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    before_ram_mb: Mapped[int | None] = mapped_column(Integer)
    after_ram_mb: Mapped[int | None] = mapped_column(Integer)
    # pending | running | succeeded | failed | aborted
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    # pending | restored | paused_new_app | failed
    restore_state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
