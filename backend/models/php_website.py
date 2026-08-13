"""Managed native PHP website metadata; secrets stay outside the database."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PhpWebsite(Base):
    __tablename__ = "php_websites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"), unique=True, nullable=False)
    preset: Mapped[str] = mapped_column(String(24), default="php", nullable=False)
    previous_project_type: Mapped[str] = mapped_column(String(24), default="static", nullable=False)
    php_version: Mapped[str] = mapped_column(String(16), nullable=False)
    document_root: Mapped[str] = mapped_column(String(255), default="public", nullable=False)
    root_path: Mapped[str] = mapped_column(String(512), nullable=False)
    linux_user: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="provisioning", nullable=False)
    ssl_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ssl_include_www: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wordpress_site_title: Mapped[str | None] = mapped_column(String(255))
    wordpress_admin_user: Mapped[str | None] = mapped_column(String(64))
    wordpress_admin_email: Mapped[str | None] = mapped_column(String(255))
    wordpress_installed_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_warning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )
