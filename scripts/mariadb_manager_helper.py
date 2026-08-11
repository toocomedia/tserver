#!/usr/bin/env python3
"""Root-owned, stdin-only MariaDB Manager helper for panel-managed databases."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
USER_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PASSWORD = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}
SYSTEM_USERS = {"root", "mariadb.sys", "mysql", "mysql.session"}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        fail("Invalid MariaDB manager request.")
    if not isinstance(value, dict):
        fail("Invalid MariaDB manager request.")
    return value


def identifier(value: Any, *, user: bool = False) -> str:
    text = str(value or "").strip().lower()
    matcher = USER_IDENTIFIER if user else IDENTIFIER
    if not matcher.fullmatch(text):
        fail("Invalid MariaDB identifier.")
    return text


def password(value: Any) -> str:
    text = str(value or "")
    if not PASSWORD.fullmatch(text):
        fail("Invalid MariaDB password.")
    return text


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(value: str) -> str:
    return "`" + value + "`"


def run_sql(sql: str, *, rows: bool = False) -> list[list[str]]:
    result = subprocess.run(
        ["mariadb", "--protocol=socket", "--batch", "--skip-column-names", "--raw"],
        input=sql,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        fail((result.stderr or result.stdout or "MariaDB command failed.").strip())
    if not rows:
        return []
    return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]


def list_databases() -> dict[str, Any]:
    rows = run_sql(
        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
        "WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys') "
        "ORDER BY SCHEMA_NAME;",
        rows=True,
    )
    return {"databases": [{"name": row[0]} for row in rows if row]}


def list_users() -> dict[str, Any]:
    rows = run_sql("SELECT DISTINCT User FROM mysql.user ORDER BY User;", rows=True)
    return {"users": [{"name": row[0]} for row in rows if row and row[0] not in SYSTEM_USERS]}


def create_database(request_data: dict[str, Any]) -> dict[str, Any]:
    database = identifier(request_data.get("database"))
    user = identifier(request_data.get("user"), user=True)
    secret = password(request_data.get("password"))
    run_sql(
        f"CREATE DATABASE IF NOT EXISTS {sql_identifier(database)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        f"CREATE USER IF NOT EXISTS {literal(user)}@'localhost' IDENTIFIED BY {literal(secret)};"
        f"ALTER USER {literal(user)}@'localhost' IDENTIFIED BY {literal(secret)};"
        f"CREATE USER IF NOT EXISTS {literal(user)}@'127.0.0.1' IDENTIFIED BY {literal(secret)};"
        f"ALTER USER {literal(user)}@'127.0.0.1' IDENTIFIED BY {literal(secret)};"
        f"CREATE USER IF NOT EXISTS {literal(user)}@'%' IDENTIFIED BY {literal(secret)};"
        f"ALTER USER {literal(user)}@'%' IDENTIFIED BY {literal(secret)};"
        f"GRANT ALL PRIVILEGES ON {sql_identifier(database)}.* TO {literal(user)}@'localhost';"
        f"GRANT ALL PRIVILEGES ON {sql_identifier(database)}.* TO {literal(user)}@'127.0.0.1';"
        f"GRANT ALL PRIVILEGES ON {sql_identifier(database)}.* TO {literal(user)}@'%';"
        "FLUSH PRIVILEGES;"
    )
    return {"database": database, "user": user}


def drop_database(request_data: dict[str, Any]) -> dict[str, Any]:
    database = identifier(request_data.get("database"))
    if database in SYSTEM_DATABASES:
        fail("System databases cannot be removed.")
    run_sql(f"DROP DATABASE IF EXISTS {sql_identifier(database)};")
    return {"database": database}


def drop_user(request_data: dict[str, Any]) -> dict[str, Any]:
    user = identifier(request_data.get("user"), user=True)
    if user in SYSTEM_USERS:
        fail("System users cannot be removed.")
    run_sql(
        f"DROP USER IF EXISTS {literal(user)}@'localhost';"
        f"DROP USER IF EXISTS {literal(user)}@'127.0.0.1';"
        f"DROP USER IF EXISTS {literal(user)}@'%';"
        "FLUSH PRIVILEGES;"
    )
    return {"user": user}


def reset_password(request_data: dict[str, Any]) -> dict[str, Any]:
    user = identifier(request_data.get("user"), user=True)
    secret = password(request_data.get("password"))
    if user in SYSTEM_USERS:
        fail("System user passwords cannot be changed here.")
    run_sql(
        f"ALTER USER {literal(user)}@'localhost' IDENTIFIED BY {literal(secret)};"
        f"ALTER USER {literal(user)}@'127.0.0.1' IDENTIFIED BY {literal(secret)};"
        f"ALTER USER {literal(user)}@'%' IDENTIFIED BY {literal(secret)};"
        "FLUSH PRIVILEGES;"
    )
    return {"user": user}


OPERATIONS = {
    "list_databases": lambda data: list_databases(),
    "list_users": lambda data: list_users(),
    "create_database": create_database,
    "drop_database": drop_database,
    "drop_user": drop_user,
    "reset_password": reset_password,
}


def main() -> None:
    data = request()
    operation = str(data.get("operation") or "")
    handler = OPERATIONS.get(operation)
    if handler is None:
        fail("Unsupported MariaDB manager operation.")
    print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
