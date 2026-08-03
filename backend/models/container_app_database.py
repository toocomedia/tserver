"""One database or cache attachment owned by a Railpack app."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ContainerAppDatabase(Base):
    __tablename__ = "container_app_databases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    environment_key: Mapped[str] = mapped_column(String(128), nullable=False)
    container_name: Mapped[str | None] = mapped_column(String(128), unique=True)
    network_alias: Mapped[str | None] = mapped_column(String(64))
    volume_name: Mapped[str | None] = mapped_column(String(128), unique=True)
    credentials_path: Mapped[str | None] = mapped_column(String(512))
    database_name: Mapped[str | None] = mapped_column(String(63))
    username: Mapped[str | None] = mapped_column(String(63))
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
