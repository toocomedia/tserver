#!/usr/bin/env python3
"""
test_on_vps.py — Safe, read-only VPS health check for the postgres_manager plugin.

Run from the panel install root on the VPS:
    python3 backend/plugins/postgres_manager/docs/test_on_vps.py

Nothing is created, modified, or deleted.
Requires: the panel user must have 'sudo -u postgres psql' in sudoers.
"""
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# Resolve plugin root dynamically — works whether app lives at
# /opt/srv-panel/app/, /opt/srv-panel/backend/, or any other path.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # docs/ -> postgres_manager/

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"

results: list[tuple[str, bool, str]] = []


def check(label: str, fn) -> bool:
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, str(exc)
    results.append((label, ok, detail))
    icon = PASS if ok else FAIL
    print(f"  {icon}  {label}", end="")
    if detail:
        print(f"  →  {detail}", end="")
    print()
    return ok


# ── Checks ────────────────────────────────────────────────────────────────

def check_psql_binary():
    path = shutil.which("psql")
    return bool(path), path or "psql not found in PATH"


def check_service_active():
    res = subprocess.run(
        ["systemctl", "is-active", "postgresql"],
        capture_output=True, text=True, timeout=5,
    )
    active = res.stdout.strip() == "active"
    return active, res.stdout.strip()


def check_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        ok = s.connect_ex(("127.0.0.1", 5432)) == 0
    return ok, "5432 open" if ok else "5432 not responding"


def check_list_databases():
    res = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-t", "-A", "-c",
         "SELECT datname FROM pg_catalog.pg_database WHERE datistemplate=false;"],
        capture_output=True, text=True, timeout=10, shell=False,
    )
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()
    dbs = [l for l in res.stdout.strip().splitlines() if l]
    return True, f"{len(dbs)} database(s) found: {', '.join(dbs[:5])}"


def check_list_users():
    res = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-t", "-A", "-c",
         "SELECT rolname FROM pg_catalog.pg_roles ORDER BY rolname;"],
        capture_output=True, text=True, timeout=10, shell=False,
    )
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip()
    users = [l for l in res.stdout.strip().splitlines() if l]
    return True, f"{len(users)} role(s): {', '.join(users[:5])}"


def check_psutil():
    try:
        import psutil
        return True, f"psutil {psutil.__version__}"
    except ImportError:
        return False, "psutil not installed (pip install psutil)"


def check_ram():
    import psutil
    res = subprocess.run(
        ["pgrep", "-x", "postgres"], capture_output=True, text=True, timeout=5,
    )
    pids = res.stdout.strip().split()
    if not pids:
        return False, "postgres process not found"
    pid = int(pids[0])
    mb = round(psutil.Process(pid).memory_info().rss / 1_048_576, 1)
    return True, f"{mb} MB (PID {pid})"


def check_plugin_json():
    path = PLUGIN_ROOT / "plugin.json"
    if not path.exists():
        return False, f"Not found: {path}"
    data = json.loads(path.read_text())
    return data.get("id") == "postgres_manager", f"id={data.get('id')}"


def check_template_files():
    expected = [
        "templates/postgres.html",
        "templates/partials/_pg_databases.html",
        "templates/partials/_pg_users.html",
        "templates/partials/_pg_query.html",
        "templates/partials/_pg_scripts.html",
    ]
    missing = [f for f in expected if not (PLUGIN_ROOT / f).exists()]
    return (not missing), ("all present" if not missing else f"missing: {missing}")


# ── Run ───────────────────────────────────────────────────────────────────

print("\nPostgreSQL Manager — VPS Health Check")
print("=" * 42)

check("psql binary found",          check_psql_binary)
check("postgresql service active",   check_service_active)
check("port 5432 listening",         check_port)
check("databases list readable",     check_list_databases)
check("users list readable",         check_list_users)
check("psutil available",            check_psutil)
check("RAM readable from process",   check_ram)
check("plugin.json valid",           check_plugin_json)
check("all template files present",  check_template_files)

print("=" * 42)
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
if passed == total:
    print(f"\n\033[92m==> {passed}/{total} checks passed. Plugin ready.\033[0m\n")
    sys.exit(0)
else:
    print(f"\n\033[91m==> {passed}/{total} checks passed. Fix the failures above.\033[0m\n")
    sys.exit(1)
