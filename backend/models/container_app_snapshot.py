"""Immutable deployment input captured for a Railpack container app."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ContainerAppSnapshot(Base):
    __tablename__ = "container_app_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    configuration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(128))
    image_digest: Mapped[str | None] = mapped_column(String(512))
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    environment_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    secret_versions_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), index=True)
    failure_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

