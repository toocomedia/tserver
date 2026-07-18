"""
models/proxy.py — Reverse Proxy ORM model
"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class ReverseProxy(Base):
    __tablename__ = "reverse_proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Null when proxy is external (DNS managed outside the panel)
    domain_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("domains.id"), nullable=True
    )
    # subdomain prefix only for managed: "app"; empty/full for external
    subdomain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # public hostname: "app.example.com" or external FQDN
    full_domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    target_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    target_port: Mapped[int] = mapped_column(Integer, nullable=False)
    # http or https (protocol to backend)
    protocol: Mapped[str] = mapped_column(String(8), default="http", nullable=False)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ssl_cert_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ssl_certs.id"), nullable=True
    )
    nginx_config_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # True = panel wrote DNS A on parent zone; False = external DNS only
    dns_managed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
