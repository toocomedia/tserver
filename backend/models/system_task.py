"""Persistent database model for System Task Manager history and audit."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SystemTask(Base):
    __tablename__ = "system_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[float] = mapped_column(Float, nullable=False)
    finished_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    lock_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    logs_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def to_dict(self, include_logs: bool = True) -> Dict[str, Any]:
        logs = []
        if include_logs and self.logs_json:
            try:
                logs = json.loads(self.logs_json)
            except Exception:
                logs = []

        return {
            "id": self.id,
            "category": self.category,
            "action": self.action,
            "target_id": self.target_id,
            "label": self.label,
            "status": self.status,
            "progress": self.progress,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "lock_type": self.lock_type,
            "can_cancel": False,
            "logs": logs[-100:] if include_logs else [],
        }
