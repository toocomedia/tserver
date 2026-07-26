"""Names of protected hosted-app environment variables; values stay on disk."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AppEnvironmentVariable(Base):
    __tablename__ = "app_environment_variables"
    __table_args__ = (UniqueConstraint("app_id", "key", name="uq_app_environment_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("hosted_apps.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
