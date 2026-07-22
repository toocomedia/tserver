"""
models/mail_domain.py — MailDomain ORM model.
Tracks domains configured for mail delivery in the maddy plugin.
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class MailDomain(Base):
    __tablename__ = "mail_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    server_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    dns_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
