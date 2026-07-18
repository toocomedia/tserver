"""
models/dns_record.py — DNS Record ORM model (panel-side tracking)
Source of truth is PowerDNS; this table tracks panel-managed records.
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class DnsRecord(Base):
    __tablename__ = "dns_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_id: Mapped[int] = mapped_column(Integer, ForeignKey("domains.id"), nullable=False)
    # full record name e.g. "www.example.com." or "@" stored as "example.com."
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False)   # A, CNAME, MX, TXT...
    content: Mapped[str] = mapped_column(String(65535), nullable=False)
    ttl: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    # managed=True means panel created it; False means manually added via DNS page
    managed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
