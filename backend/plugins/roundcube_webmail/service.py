"""Docker lifecycle and launch-token helpers for Roundcube webmail."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class RoundcubeWebmailService:
    plugin_id = "roundcube_webmail"
    container_name = "srv-panel-roundcube-webmail"
    config_version = "3"
    host_port = 8088
    launch_ttl_seconds = 60
    command_timeout = 15

    def __init__(self) -> None:
        self._state_lock = threading.RLock()

    @property
    def data_dir(self) -> Path:
        configured = os.getenv("ROUNDCUBE_WEBMAIL_DATA_DIR")
        if configured:
            return Path(configured)
        if os.name == "nt":
            return Path(os.getenv("TEMP", "C:/tmp")) / "srv-panel-roundcube-webmail"
        return Path("/opt/srv-panel/data/roundcube_webmail")

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "launch.secret"

    @staticmethod
    def _docker_command_prefix() -> list[str]:
        if os.name == "nt":
            return []
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return []
        import config

        return ["sudo", "-n"] if config.PRIVILEGED_SUDO else []

    def _run(
        self, command: list[str], *, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self._docker_command_prefix(), *command],
            capture_output=True,
            text=True,
            timeout=timeout or self.command_timeout,
            check=False,
            shell=False,
        )

    def is_installed(self) -> bool:
        if os.name == "nt":
            return False
        try:
            result = self._run(
                ["docker", "container", "inspect", self.container_name],
                timeout=5,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def needs_reconcile(self) -> bool:
        """Return true when an installed container was built by older plugin code."""
        if os.name == "nt" or not self.is_installed():
            return False
        try:
            result = self._run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "srv-panel.config-version" }}',
                    self.container_name,
                ],
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return (
            result.returncode == 0
            and result.stdout.strip() != self.config_version
        )

    def get_status(self) -> dict[str, Any]:
        status = {
            "installed": False,
            "running": False,
            "healthy": False,
            "state": "missing",
            "error": None,
        }
        if os.name == "nt":
            return status
        try:
            result = self._run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    self.container_name,
                ],
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            status["error"] = str(exc)
            return status
        if result.returncode != 0:
            status["error"] = result.stderr.strip() or None
            return status
        try:
            state = json.loads(result.stdout)
        except json.JSONDecodeError:
            status["error"] = "Docker returned invalid container state."
            return status

        running = bool(state.get("Running"))
        health = (state.get("Health") or {}).get("Status")
        healthy = running and health == "healthy"
        status.update(
            {
                "installed": True,
                "running": running,
                "healthy": healthy,
                "state": health or ("running" if running else "stopped"),
                "error": state.get("Error") or None,
            }
        )
        return status

    def pause(self) -> None:
        if not self.is_installed():
            return
        result = self._run(
            ["docker", "stop", "--time", "10", self.container_name], timeout=20
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Could not stop Roundcube.")
        if self.get_status()["running"]:
            raise RuntimeError("Roundcube is still running after the stop request.")

    def resume(self) -> None:
        if not self.is_installed():
            raise RuntimeError("Roundcube is not installed.")
        result = self._run(["docker", "start", self.container_name], timeout=20)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Could not start Roundcube.")
        deadline = time.monotonic() + 30
        last = self.get_status()
        while time.monotonic() < deadline:
            last = self.get_status()
            if last["healthy"]:
                return
            time.sleep(1)
        raise RuntimeError(last.get("error") or "Roundcube failed its health check.")

    def diagnose_mail_connection(self) -> dict[str, Any]:
        """Probe Maddy from inside the exact Roundcube network namespace."""
        status = self.get_status()
        if not status["healthy"]:
            return {
                "ok": False,
                "error": "Roundcube container is not healthy.",
                "imap": None,
                "smtp": None,
            }
        php = r"""
function srv_host($name) {
    $value = getenv($name) ?: '';
    return preg_replace('#^[a-z]+://#i', '', $value);
}
function srv_probe($host, $port, $tls) {
    $context = stream_context_create(['ssl' => [
        'verify_peer' => false,
        'verify_peer_name' => false,
        'allow_self_signed' => true,
    ]]);
    $target = ($tls ? 'ssl://' : 'tcp://') . $host . ':' . $port;
    $socket = @stream_socket_client(
        $target, $errno, $error, 5, STREAM_CLIENT_CONNECT, $context
    );
    if (!$socket) {
        return ['ok' => false, 'host' => $host, 'port' => $port,
            'error' => trim($errno . ' ' . $error)];
    }
    stream_set_timeout($socket, 2);
    $banner = trim((string) fgets($socket, 512));
    fclose($socket);
    return ['ok' => true, 'host' => $host, 'port' => $port, 'banner' => $banner];
}
$rawImap = getenv('ROUNDCUBEMAIL_DEFAULT_HOST') ?: '';
$mode = getenv('SRV_MADDY_TRANSPORT') ?: (
    preg_match('#^(ssl|tls)://#i', $rawImap) ? 'tls_unverified' : 'local'
);
$usesTls = $mode !== 'local';
$imapPort = $usesTls ? 993 : 143;
$imap = srv_probe(srv_host('ROUNDCUBEMAIL_DEFAULT_HOST'), $imapPort, $usesTls);
$smtp = srv_probe(srv_host('ROUNDCUBEMAIL_SMTP_SERVER'), 587, false);
echo json_encode([
    'ok' => $imap['ok'] && $smtp['ok'],
    'imap' => $imap,
    'smtp' => $smtp,
    'transport' => $mode,
    'smtp_security' => $usesTls ? 'STARTTLS' : 'local',
]);
"""
        try:
            result = self._run(
                ["docker", "exec", self.container_name, "php", "-r", php],
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc), "imap": None, "smtp": None}
        if result.returncode != 0:
            return {
                "ok": False,
                "error": result.stderr.strip() or "Mail connection test failed.",
                "imap": None,
                "smtp": None,
            }
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "Roundcube returned an invalid diagnostic response.",
                "imap": None,
                "smtp": None,
            }
        return data if isinstance(data, dict) else {"ok": False}

    def read_state(self) -> dict[str, Any]:
        with self._state_lock:
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"schema_version": 2, "sites": {}}
            if not isinstance(data, dict):
                return {"schema_version": 2, "sites": {}}
            if data.get("schema_version") == 2 and isinstance(data.get("sites"), dict):
                return data

            migrated: dict[str, Any] = {
                "schema_version": 2,
                "sites": {},
            }
            domain = data.get("mail_domain")
            host = data.get("public_host")
            if isinstance(domain, str) and domain and isinstance(host, str) and host:
                migrated["sites"][domain] = {
                    "public_host": host,
                    "dns_managed": bool(data.get("dns_managed")),
                    "ssl_status": data.get("ssl_status", "not_configured"),
                    "ssl_started_at": data.get("ssl_started_at"),
                    "ssl_error": data.get("ssl_error"),
                    "ssl_error_detail": data.get("ssl_error_detail"),
                    "dns_error": data.get("dns_error"),
                }
            for key in ("rebuild_status", "rebuild_error"):
                if key in data:
                    migrated[key] = data[key]
            self.write_state(migrated)
            return migrated

    def write_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            normalized = dict(state)
            normalized["schema_version"] = 2
            if not isinstance(normalized.get("sites"), dict):
                normalized["sites"] = {}
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
            temp.replace(self.state_path)

    def update_state(self, **changes: Any) -> dict[str, Any]:
        with self._state_lock:
            state = self.read_state()
            state.update(changes)
            self.write_state(state)
            return state

    def get_sites(self) -> dict[str, dict[str, Any]]:
        sites = self.read_state().get("sites", {})
        return {
            str(domain): dict(site)
            for domain, site in sites.items()
            if isinstance(domain, str) and isinstance(site, dict)
        }

    def get_site(self, domain: str) -> dict[str, Any] | None:
        site = self.get_sites().get(domain.strip().lower())
        return dict(site) if site else None

    def save_site(self, domain: str, site: dict[str, Any]) -> dict[str, Any]:
        domain = domain.strip().lower()
        with self._state_lock:
            state = self.read_state()
            sites = dict(state.get("sites", {}))
            sites[domain] = dict(site)
            state["sites"] = sites
            self.write_state(state)
        return dict(site)

    def update_site(self, domain: str, **changes: Any) -> dict[str, Any]:
        current = self.get_site(domain)
        if current is None:
            raise KeyError(domain)
        current.update(changes)
        return self.save_site(domain, current)

    def delete_site(self, domain: str) -> dict[str, Any] | None:
        domain = domain.strip().lower()
        with self._state_lock:
            state = self.read_state()
            sites = dict(state.get("sites", {}))
            removed = sites.pop(domain, None)
            state["sites"] = sites
            self.write_state(state)
        return dict(removed) if isinstance(removed, dict) else None

    def get_public_url(self, domain: str | None = None) -> str | None:
        if domain is not None:
            site = self.get_site(domain)
            if not site or site.get("ssl_status") != "ready":
                return None
            host = site.get("public_host")
            return f"https://{host}/" if isinstance(host, str) and host else None
        for site_domain in self.get_sites():
            url = self.get_public_url(site_domain)
            if url:
                return url
        return None

    def get_configured_url(self, domain: str) -> str | None:
        site = self.get_site(domain)
        if not site:
            return None
        host = site.get("public_host")
        if not isinstance(host, str) or not host:
            return None
        scheme = "https" if site.get("ssl_status") == "ready" else "http"
        return f"{scheme}://{host}/"

    def get_default_domain(self) -> str | None:
        sites = self.get_sites()
        return next(iter(sites), None)

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    @staticmethod
    def _valid_email(email: str) -> bool:
        return bool(
            re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
                email,
            )
        )

    def create_launch_token(self, email: str, *, now: int | None = None) -> str:
        email = email.strip().lower()
        if not self._valid_email(email):
            raise ValueError("Invalid mailbox address.")
        try:
            secret = self.secret_path.read_bytes().strip()
        except OSError as exc:
            raise RuntimeError("Roundcube launch secret is unavailable.") from exc
        if len(secret) < 32:
            raise RuntimeError("Roundcube launch secret is invalid.")
        issued = int(time.time() if now is None else now)
        payload = json.dumps(
            {"email": email, "exp": issued + self.launch_ttl_seconds},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = self._b64encode(payload)
        signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{self._b64encode(signature)}"

    def verify_launch_token(
        self,
        token: str,
        *,
        now: int | None = None,
        expected_email: str | None = None,
    ) -> str:
        """Verify the same compact token contract consumed by the PHP plugin."""
        if not isinstance(token, str) or token.count(".") != 1:
            raise ValueError("Invalid launch token.")
        encoded, provided = token.split(".", 1)
        try:
            secret = self.secret_path.read_bytes().strip()
            signature = self._b64decode(provided)
            payload = json.loads(self._b64decode(encoded))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid launch token.") from exc
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        if len(secret) < 32 or not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid launch token.")
        if not isinstance(payload, dict):
            raise ValueError("Invalid launch token.")
        email = payload.get("email")
        expiration = payload.get("exp")
        current = int(time.time() if now is None else now)
        if (
            not isinstance(email, str)
            or not self._valid_email(email)
            or not isinstance(expiration, int)
            or expiration < current
            or expiration > current + self.launch_ttl_seconds
        ):
            raise ValueError("Expired or invalid launch token.")
        if expected_email is not None and email != expected_email.strip().lower():
            raise ValueError("Launch token does not match this mailbox.")
        return email

    def purge_data(self) -> None:
        """Remove panel-side launch state after Docker volumes were purged."""
        if self.is_installed():
            raise RuntimeError("Uninstall Roundcube before purging its data.")
        for path in (self.secret_path, self.state_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            self.data_dir.rmdir()
        except (FileNotFoundError, OSError):
            pass


roundcube_webmail_service = RoundcubeWebmailService()
