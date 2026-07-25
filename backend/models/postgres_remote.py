"""
models/postgres_remote.py — ORM model for PostgreSQL Remote Domain Endpoints
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class PostgresRemoteDomain(Base):
    __tablename__ = "postgres_remote_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("domains.id"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(32), default="managed", nullable=False)  # "managed" vs "external"
    subdomain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    full_domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=5432, nullable=False)
    allowed_cidrs: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dns_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    tls_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    postgres_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    certificate_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    certificate_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ssl_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nginx_stream: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
