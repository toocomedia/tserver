"""Panel-side client for the fixed root-owned MariaDB Manager helper."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

import config


class MariaDBManagerService:
    helper_path = Path("/usr/local/lib/srv-panel/mariadb-manager")

    @staticmethod
    def _command() -> list[str]:
        command = [str(MariaDBManagerService.helper_path)]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0 and config.PRIVILEGED_SUDO:
            return ["sudo", "-n", *command]
        return command

    def _call(self, operation: str, **values: str) -> dict[str, Any]:
        if os.name == "nt":
            raise RuntimeError("MariaDB Manager is available only on Linux.")
        if not self.helper_path.is_file():
            raise RuntimeError("MariaDB Manager helper is missing. Reinstall MariaDB from Dependencies.")
        request = json.dumps({"operation": operation, **values})
        try:
            result = subprocess.run(
                self._command(),
                input=request,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("MariaDB Manager request timed out.") from exc
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "MariaDB Manager request failed.").strip()[-1000:])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MariaDB Manager returned an invalid response.") from exc
        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            raise RuntimeError("MariaDB Manager request failed.")
        return payload["result"]

    @staticmethod
    def new_password() -> str:
        return secrets.token_urlsafe(24)

    def list_databases(self) -> list[dict[str, Any]]:
        return list(self._call("list_databases").get("databases") or [])

    def list_users(self) -> list[dict[str, Any]]:
        return list(self._call("list_users").get("users") or [])

    def create_database(self, database: str, user: str) -> dict[str, str]:
        password = self.new_password()
        result = self._call("create_database", database=database, user=user, password=password)
        return {"database": str(result["database"]), "user": str(result["user"]), "password": password}

    def create_local_database(self, database: str, user: str) -> dict[str, str]:
        password = self.new_password()
        try:
            result = self._call("create_local_database", database=database, user=user, password=password)
        except RuntimeError as exc:
            if "Unsupported MariaDB manager operation" in str(exc):
                result = self._call("create_database", database=database, user=user, password=password)
            else:
                raise
        return {"database": str(result["database"]), "user": str(result["user"]), "password": password}

    def drop_database(self, database: str) -> None:
        self._call("drop_database", database=database)

    def drop_user(self, user: str) -> None:
        self._call("drop_user", user=user)

    def reset_password(self, user: str) -> str:
        password = self.new_password()
        self._call("reset_password", user=user, password=password)
        return password

    def set_local_password(self, user: str, password: str) -> None:
        try:
            self._call("reset_local_password", user=user, password=password)
        except RuntimeError as exc:
            if "Unsupported MariaDB manager operation" in str(exc):
                self._call("reset_password", user=user, password=password)
            else:
                raise

    def reset_local_password(self, user: str) -> str:
        password = self.new_password()
        self.set_local_password(user, password)
        return password


mariadb_manager_service = MariaDBManagerService()
