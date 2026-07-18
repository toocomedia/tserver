"""
models/ssl_cert.py — SSL Certificate ORM model
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class SslCert(Base):
    __tablename__ = "ssl_certs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_id: Mapped[int] = mapped_column(Integer, ForeignKey("domains.id"), nullable=False)
    # full_domain may be a subdomain: sub.example.com
    full_domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    cert_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
