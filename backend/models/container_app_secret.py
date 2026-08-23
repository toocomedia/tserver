"""Private generated values and optional bootstrap credentials for container apps."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ContainerAppSecret(Base):
    __tablename__ = "container_app_secrets"
    __table_args__ = (UniqueConstraint("app_id", "key", "version", name="uq_container_app_secret_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ContainerAppCredential(Base):
    __tablename__ = "container_app_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_secret_id: Mapped[int] = mapped_column(ForeignKey("container_app_secrets.id"), nullable=False)
    reveal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_revealed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ContainerAppCredentialAccess(Base):
    __tablename__ = "container_app_credential_accesses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("container_apps.id"), nullable=False, index=True)
    credential_id: Mapped[int] = mapped_column(ForeignKey("container_app_credentials.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
