"""
queries.py — PostgreSQL CRUD via psql subprocess calls.

All commands use: sudo -u postgres psql -t -A -c "..."
Rules: shell=False, timeout=10, validated identifiers, no raw user input in SQL.
"""
import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Safe PostgreSQL identifier: letters, digits, underscore, hyphen, 1–63 chars
_IDENT_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,63}$")
# Only SELECT statements are allowed in the query runner
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


# ------------------------------------------------------------------
# Input guards
# ------------------------------------------------------------------

def _validate_ident(name: str, label: str = "name") -> None:
    """Raise ValueError if name contains characters unsafe for a pg identifier."""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid {label} '{name}'. "
            "Use only letters, digits, underscores, or hyphens (max 63 chars)."
        )


def _escape_literal(value: str) -> str:
    """Minimal SQL-literal escaping: double up single quotes."""
    return value.replace("'", "''")


# ------------------------------------------------------------------
# Shared runner
# ------------------------------------------------------------------

def _run_psql(args: list[str], timeout: int = 10) -> str:
    """
    Execute psql as the postgres OS user. Returns stdout on success.
    Raises RuntimeError on non-zero exit or timeout.
    """
    if os.name == "nt":
        # Windows dev environment — no real psql available
        logger.debug("[DEV] Mock psql call: %s", args)
        return ""

    cmd = ["sudo", "-n", "-u", "postgres", "psql", "-t", "-A"] + args
    logger.debug("psql cmd: %s", " ".join(cmd))
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("psql command timed out after %ds." % timeout)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "psql returned non-zero.")
    return res.stdout


# ------------------------------------------------------------------
# Databases
# ------------------------------------------------------------------

def list_databases() -> list[dict[str, Any]]:
    """Return all user databases with owner, encoding and human-readable size."""
    sql = (
        "SELECT datname, pg_catalog.pg_get_userbyid(datdba), "
        "pg_catalog.pg_encoding_to_char(encoding), "
        "pg_size_pretty(pg_catalog.pg_database_size(datname)) "
        "FROM pg_catalog.pg_database WHERE datistemplate = false ORDER BY datname;"
    )
    out = _run_psql(["-c", sql])
    results: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 4:
            results.append({
                "name": parts[0], "owner": parts[1],
                "encoding": parts[2], "size": parts[3],
            })
    return results


def create_database(name: str, owner: str = "postgres") -> bool:
    """Create a new database owned by an existing role."""
    _validate_ident(name, "database name")
    _validate_ident(owner, "owner")
    _run_psql(["-c", f'CREATE DATABASE "{name}" OWNER "{owner}";'])
    logger.info("Database created: %s (owner: %s)", name, owner)
    return True


def drop_database(name: str) -> bool:
    """Drop a database. Fails if active connections exist."""
    _validate_ident(name, "database name")
    _run_psql(["-c", f'DROP DATABASE "{name}";'])
    logger.info("Database dropped: %s", name)
    return True


# ------------------------------------------------------------------
# Tables
# ------------------------------------------------------------------

def list_tables(db_name: str) -> list[dict[str, Any]]:
    """Return user tables in a database with size and estimated row counts."""
    _validate_ident(db_name, "database name")
    sql = (
        "SELECT tablename, "
        "pg_size_pretty(pg_total_relation_size(quote_ident(tablename))), "
        "n_live_tup "
        "FROM pg_stat_user_tables ORDER BY tablename;"
    )
    out = _run_psql(["-d", db_name, "-c", sql])
    results: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            results.append({
                "name": parts[0], "size": parts[1], "row_count": parts[2],
            })
    return results


# ------------------------------------------------------------------
# Users / Roles
# ------------------------------------------------------------------

def list_users() -> list[dict[str, Any]]:
    """Return all PostgreSQL roles with login and superuser flags."""
    sql = (
        "SELECT rolname, rolsuper::text, rolcanlogin::text "
        "FROM pg_catalog.pg_roles ORDER BY rolname;"
    )
    out = _run_psql(["-c", sql])
    results: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            results.append({
                "name": parts[0],
                "superuser": parts[1] == "t",
                "can_login": parts[2] == "t",
            })
    return results


def create_user(name: str, password: str) -> bool:
    """Create a new login role."""
    _validate_ident(name, "username")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    escaped_pw = _escape_literal(password)
    _run_psql(["-c", f"CREATE ROLE \"{name}\" WITH LOGIN PASSWORD '{escaped_pw}';"])
    logger.info("User created: %s", name)
    return True


def drop_user(name: str) -> bool:
    """Drop a PostgreSQL role."""
    _validate_ident(name, "username")
    _run_psql(["-c", f'DROP ROLE "{name}";'])
    logger.info("User dropped: %s", name)
    return True


def change_password(name: str, new_password: str) -> bool:
    """Update a role's password."""
    _validate_ident(name, "username")
    if not new_password or len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    escaped_pw = _escape_literal(new_password)
    _run_psql(["-c", f"ALTER ROLE \"{name}\" WITH PASSWORD '{escaped_pw}';"])
    logger.info("Password changed for user: %s", name)
    return True


# ------------------------------------------------------------------
# Query runner (SELECT only)
# ------------------------------------------------------------------

def run_query(db_name: str, sql: str) -> list[dict[str, Any]]:
    """
    Execute a read-only SELECT query. Non-SELECT statements are rejected
    both by this function and by the READ ONLY transaction wrapper in psql.
    Returns a list of row dicts (column "row" contains the pipe-delimited line).
    """
    _validate_ident(db_name, "database name")
    sql = sql.strip()
    if not _SELECT_RE.match(sql):
        raise ValueError("Only SELECT statements are permitted in the query runner.")
    if len(sql) > 4000:
        raise ValueError("Query too long (max 4 000 characters).")

    # Wrap in a read-only transaction so even if the check is bypassed, writes fail
    wrapped = f"BEGIN; SET TRANSACTION READ ONLY; {sql}; ROLLBACK;"
    out = _run_psql(["-d", db_name, "-c", wrapped], timeout=10)

    rows: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        if line.strip() and not line.startswith("BEGIN") and not line.startswith("ROLLBACK"):
            rows.append({"row": line})
    return rows
