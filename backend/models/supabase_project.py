"""Supabase project credentials stored in panel DB."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SupabaseProject(Base):
    __tablename__ = "supabase_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    project_ref: Mapped[str | None] = mapped_column(String(32))
    db_host: Mapped[str] = mapped_column(String(255), nullable=False)
    db_port: Mapped[int] = mapped_column(Integer, default=5432, nullable=False)
    db_name: Mapped[str] = mapped_column(String(63), default="postgres", nullable=False)
    db_user: Mapped[str] = mapped_column(String(63), default="postgres", nullable=False)
    db_password_enc: Mapped[str] = mapped_column(Text, nullable=False)
    pat_enc: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(String(64))
    connection_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
