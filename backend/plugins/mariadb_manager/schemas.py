"""Request validation for MariaDB Manager."""
from __future__ import annotations

import re

from pydantic import BaseModel, field_validator


_DATABASE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_USER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class DatabaseCreate(BaseModel):
    database: str
    user: str

    @field_validator("database")
    @classmethod
    def valid_database(cls, value: str) -> str:
        value = value.strip().lower()
        if not _DATABASE.fullmatch(value):
            raise ValueError("Database names use lowercase letters, numbers, and underscores only.")
        return value

    @field_validator("user")
    @classmethod
    def valid_user(cls, value: str) -> str:
        value = value.strip().lower()
        if not _USER.fullmatch(value):
            raise ValueError("Usernames use lowercase letters, numbers, and underscores only.")
        return value


class Confirmation(BaseModel):
    confirmation: str
