#!/usr/bin/env bash
# Shared Python dependency selection and validation for install/update scripts.

SRV_PYTHON_PREFLIGHT_DIR=""

srv_python_select_constraints() {
  local python_bin="$1"
  local backend_dir="$2"
  local version_key

  version_key="$($python_bin -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")')" || return 1
  case "$version_key" in
    310|311|312|313|314) ;;
    *)
      printf 'ERROR: Unsupported Python version from %s. SRV Panel requires Python 3.10 through 3.14.\n' "$python_bin" >&2
      return 1
      ;;
  esac

  SRV_PYTHON_VERSION_KEY="$version_key"
  SRV_PYTHON_CONSTRAINT_FILE="$backend_dir/constraints/python${version_key}.txt"
  if [[ ! -r "$SRV_PYTHON_CONSTRAINT_FILE" ]]; then
    printf 'ERROR: Missing tested Python %s constraint file: %s\n' "$version_key" "$SRV_PYTHON_CONSTRAINT_FILE" >&2
    return 1
  fi
  export SRV_PYTHON_VERSION_KEY SRV_PYTHON_CONSTRAINT_FILE
}

srv_python_verify_venv() {
  local venv_dir="$1"
  "$venv_dir/bin/python" -m pip check
  "$venv_dir/bin/python" - <<'PY'
import aiosqlite
import alembic
import asyncpg
import cryptography
import fastapi
import httpx
import psutil
import sqlalchemy
import uvicorn

print(f"Python dependency verification passed (SQLAlchemy {sqlalchemy.__version__}).")
PY
}

srv_python_install_requirements() {
  local venv_dir="$1"
  local requirements_file="$2"
  local constraint_file="$3"

  [[ -x "$venv_dir/bin/python" ]] || {
    printf 'ERROR: Python virtualenv is missing: %s\n' "$venv_dir" >&2
    return 1
  }
  [[ -r "$requirements_file" ]] || {
    printf 'ERROR: Python requirements file is missing: %s\n' "$requirements_file" >&2
    return 1
  }
  [[ -r "$constraint_file" ]] || {
    printf 'ERROR: Python constraint file is missing: %s\n' "$constraint_file" >&2
    return 1
  }

  "$venv_dir/bin/python" -m pip install --upgrade pip
  "$venv_dir/bin/python" -m pip install \
    --requirement "$requirements_file" \
    --constraint "$constraint_file"
  srv_python_verify_venv "$venv_dir"
}

srv_python_cleanup_preflight() {
  case "${SRV_PYTHON_PREFLIGHT_DIR:-}" in
    /tmp/srv-panel-python-preflight.*)
      rm -rf -- "$SRV_PYTHON_PREFLIGHT_DIR"
      ;;
  esac
  SRV_PYTHON_PREFLIGHT_DIR=""
}

srv_python_preflight_requirements() {
  local python_bin="$1"
  local requirements_file="$2"
  local constraint_file="$3"
  local status

  SRV_PYTHON_PREFLIGHT_DIR="$(mktemp -d /tmp/srv-panel-python-preflight.XXXXXX)"
  printf '==> Testing Python requirements in an isolated temporary environment...\n'
  if (
    set -euo pipefail
    "$python_bin" -m venv "$SRV_PYTHON_PREFLIGHT_DIR"
    srv_python_install_requirements \
      "$SRV_PYTHON_PREFLIGHT_DIR" \
      "$requirements_file" \
      "$constraint_file"
  ); then
    srv_python_cleanup_preflight
    return 0
  else
    status=$?
    srv_python_cleanup_preflight
    printf 'ERROR: Python requirements failed compatibility checks. The installed panel environment was not changed.\n' >&2
    return "$status"
  fi
}
