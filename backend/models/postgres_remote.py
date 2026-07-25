"""Persistent PostgreSQL remote-access endpoints."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class PostgresRemoteDomain(Base):
    __tablename__ = "postgres_remote_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("domains.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    subdomain: Mapped[str | None] = mapped_column(String(63), nullable=True)
    full_domain: Mapped[str] = mapped_column(String(253), unique=True, nullable=False)
    # Kept for compatibility with v1 endpoint rows. Native PostgreSQL now
    # owns TLS, but older SQLite tables still require this non-null column.
    nginx_stream: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encryption_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ssl_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    certificate_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    certificate_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    allowed_cidrs: Mapped[str] = mapped_column(Text, nullable=False)
    dns_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    tls_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    postgres_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
