"""
models/external_dns.py — External DNS provider binding (one per domain).

Source of truth for records stays with the external provider (Wix, Hetzner,
...); this table only stores which provider manages a domain plus its
encrypted, provider-specific credentials. The `provider` column is a free
registry id, so adding a new provider needs no schema migration.
"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class ExternalDnsBinding(Base):
    __tablename__ = "external_dns_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One binding per domain — switching provider replaces the row.
    domain_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("domains.id"), unique=True, nullable=False
    )
    # Registry provider id (e.g. "hetzner", "wix"); not an enum on purpose.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Provider-side zone locator (Hetzner zone id / Wix domain name / ...).
    zone_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fernet-encrypted JSON of provider-specific credentials. Never plaintext.
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
