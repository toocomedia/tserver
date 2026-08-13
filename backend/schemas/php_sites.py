"""Validated JSON request bodies for the panel's native PHP website APIs."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator


VERSION_RE = re.compile(r"^\d+\.\d+$")
ROOT_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WP_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")


def validate_document_root(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    if not parts or len(normalized) > 255 or any(
        part in {".", ".."} or not ROOT_PART_RE.fullmatch(part) for part in parts
    ):
        raise ValueError("Document root must be a relative folder inside the managed website root.")
    return "/".join(parts)


def validate_php_version(value: str) -> str:
    normalized = str(value or "").strip()
    if not VERSION_RE.fullmatch(normalized):
        raise ValueError("Invalid PHP version.")
    return normalized


class WordPressSetup(BaseModel):
    site_title: str = Field(min_length=1, max_length=255)
    admin_user: str
    admin_email: str = Field(max_length=255)
    admin_password: str = Field(min_length=12, max_length=512)

    @field_validator("site_title", "admin_email")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("admin_user")
    @classmethod
    def valid_user(cls, value: str) -> str:
        value = value.strip()
        if not WP_USER_RE.fullmatch(value):
            raise ValueError("Invalid WordPress administrator username.")
        return value

    @field_validator("admin_email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if value.count("@") != 1 or value.startswith("@") or value.endswith("@"):
            raise ValueError("Invalid WordPress administrator email.")
        return value


class SiteCreate(BaseModel):
    domain_id: int = Field(gt=0)
    preset: str = "php"
    php_version: str
    document_root: str = "public"
    create_database: bool = False
    ssl: bool = False
    include_www: bool = False
    install_missing_extensions: bool = False
    wordpress: WordPressSetup | None = None

    @field_validator("php_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return validate_php_version(value)

    @field_validator("document_root")
    @classmethod
    def valid_root(cls, value: str) -> str:
        return validate_document_root(value)

    @model_validator(mode="after")
    def valid_preset(self):
        if self.preset not in {"php", "wordpress"}:
            raise ValueError("Preset must be php or wordpress.")
        if self.preset == "wordpress" and self.wordpress is None:
            raise ValueError("WordPress administrator details are required.")
        if self.preset == "php" and self.wordpress is not None:
            raise ValueError("WordPress details are valid only for the WordPress preset.")
        return self


class RuntimeChange(BaseModel):
    php_version: str

    @field_validator("php_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        return validate_php_version(value)


class DocumentRootChange(BaseModel):
    document_root: str

    @field_validator("document_root")
    @classmethod
    def valid_root(cls, value: str) -> str:
        return validate_document_root(value)


class ControlRequest(BaseModel):
    action: str

    @field_validator("action")
    @classmethod
    def valid_action(cls, value: str) -> str:
        if value not in {"enable", "disable"}:
            raise ValueError("Action must be enable or disable.")
        return value


class SslRequest(BaseModel):
    include_www: bool = False


class Confirmation(BaseModel):
    confirmation: str


class DeleteSite(BaseModel):
    confirmation: str
    delete_database: bool = False


class DatabaseDelete(BaseModel):
    confirmation: str


class DatabaseCreate(BaseModel):
    install_missing_extension: bool = False


class WordPressRetry(BaseModel):
    admin_password: str = Field(min_length=12, max_length=512)
    install_missing_extensions: bool = False
