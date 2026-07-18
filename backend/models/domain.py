"""
models/domain.py — Domain ORM model
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    server_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    nginx_config_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webroot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dns_zone_created: Mapped[bool] = mapped_column(Boolean, default=False)
    nginx_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
