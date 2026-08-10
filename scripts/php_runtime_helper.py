#!/usr/bin/env python3
"""Root-owned, allowlisted PHP-FPM package lifecycle helper for SRV Panel."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^\d+\.\d+$")
STATE_PATH = Path("/var/lib/srv-panel/php-runtime/managed-versions.json")
EXTERNAL_REPOSITORY_PPA = "ppa:ondrej/php"
EXTERNAL_REPOSITORY_MARKERS = (
    "ppa.launchpadcontent.net/ondrej/php",
    "ppa.launchpad.net/ondrej/php",
)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(command[:2])} timed out.")
    if result.returncode != 0:
        fail((result.stderr or result.stdout or f"{' '.join(command)} failed.").strip()[-2000:])
    return result


def request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        fail("Invalid PHP runtime request.")
    if not isinstance(value, dict):
        fail("Invalid PHP runtime request.")
    return value


def version(value: Any) -> str:
    text = str(value or "").strip()
    if not VERSION_RE.fullmatch(text):
        fail("Invalid PHP version.")
    return text


def load_state() -> dict[str, list[str]]:
    if not STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("PHP runtime ownership state is unreadable. Reinstall the runtime helper before changing PHP versions.")
    if not isinstance(data, dict):
        fail("PHP runtime ownership state is invalid.")
    return {
        str(item_version): [str(package) for package in packages if isinstance(package, str)]
        for item_version, packages in data.items()
        if VERSION_RE.fullmatch(str(item_version)) and isinstance(packages, list)
    }


def save_state(state: dict[str, list[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(STATE_PATH)
    os.chmod(STATE_PATH, stat.S_IRUSR | stat.S_IWUSR)


def package_installed(package: str) -> bool:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package],
        capture_output=True, text=True, timeout=20, check=False,
    )
    return result.returncode == 0 and result.stdout.startswith("ii")


def apt_candidate(package: str) -> str:
    result = run(["apt-cache", "policy", package], timeout=30)
    for line in result.stdout.splitlines():
        if line.strip().startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
            if candidate and candidate != "(none)":
                return candidate
    fail(f"{package} is unavailable from this server's configured APT repositories.")


def external_repository_configured() -> bool:
    source_files = [Path("/etc/apt/sources.list")]
    source_directory = Path("/etc/apt/sources.list.d")
    if source_directory.is_dir():
        source_files.extend(source_directory.glob("*.list"))
        source_files.extend(source_directory.glob("*.sources"))
    for source_file in source_files:
        try:
            contents = source_file.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(marker in contents for marker in EXTERNAL_REPOSITORY_MARKERS):
            return True
    return False


def require_ubuntu() -> None:
    try:
        values = dict(
            line.split("=", 1) for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
    except OSError:
        fail("Cannot identify this Linux distribution from /etc/os-release.")
    if values.get("ID", "").strip().strip('"') != "ubuntu":
        fail("The external PHP repository action is supported only on Ubuntu.")


def verify_fpm(item_version: str) -> None:
    active = run(["systemctl", "is-active", f"php{item_version}-fpm"], timeout=30).stdout.strip() == "active"
    socket_path = Path(f"/run/php/php{item_version}-fpm.sock")
    try:
        socket_ok = stat.S_ISSOCK(socket_path.stat().st_mode)
    except OSError:
        socket_ok = False
    if not active or not socket_ok:
        fail(f"PHP {item_version}-FPM did not become healthy; expected socket {socket_path}.")


def install_version(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    state = load_state()
    fpm_package = f"php{item_version}-fpm"
    cli_package = f"php{item_version}-cli"
    if package_installed(fpm_package) and item_version not in state:
        fail(f"PHP {item_version} is installed outside SRV Panel and cannot be adopted automatically.")
    print("==> Refreshing configured APT repositories...", file=sys.stderr)
    run(["apt-get", "update", "-qq"], timeout=300)
    apt_candidate(fpm_package)
    print(f"==> Installing PHP {item_version}-FPM...", file=sys.stderr)
    run(["apt-get", "install", "-y", fpm_package, cli_package], timeout=900)
    run(["systemctl", "enable", "--now", f"php{item_version}-fpm"], timeout=90)
    verify_fpm(item_version)
    state[item_version] = sorted(set(state.get(item_version, []) + [fpm_package, cli_package]))
    save_state(state)
    return {"version": item_version, "message": f"PHP {item_version} installed and PHP-FPM socket is healthy."}


def check_available(_: dict[str, Any]) -> dict[str, Any]:
    print("==> Refreshing configured APT repositories...", file=sys.stderr)
    run(["apt-get", "update", "-qq"], timeout=300)
    return {"message": "PHP version availability was refreshed from the configured APT sources."}


def enable_external_repository(_: dict[str, Any]) -> dict[str, Any]:
    """Enable the one reviewed PHP PPA; repository URLs are never user input."""
    require_ubuntu()
    if external_repository_configured():
        print("==> Refreshing configured APT repositories...", file=sys.stderr)
        run(["apt-get", "update", "-qq"], timeout=300)
        return {"message": "The external PHP repository is already enabled; package availability was refreshed."}
    add_repository = shutil.which("add-apt-repository")
    if not add_repository:
        print("==> Installing Ubuntu repository management support...", file=sys.stderr)
        run(["apt-get", "update", "-qq"], timeout=300)
        run(["apt-get", "install", "-y", "software-properties-common"], timeout=300)
        add_repository = shutil.which("add-apt-repository")
    if not add_repository:
        fail("Ubuntu repository management support could not be installed.")
    print("==> Enabling the external PHP repository...", file=sys.stderr)
    run([add_repository, "--yes", EXTERNAL_REPOSITORY_PPA], timeout=300)
    if not external_repository_configured():
        fail("The external PHP repository could not be verified after it was added.")
    print("==> Refreshing configured APT repositories...", file=sys.stderr)
    run(["apt-get", "update", "-qq"], timeout=300)
    return {"message": "External PHP repository enabled. Choose individual PHP versions to install."}


def uninstall_version(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    state = load_state()
    packages = state.get(item_version)
    if not packages:
        fail(f"PHP {item_version} is not managed by SRV Panel and cannot be removed here.")
    run(["systemctl", "disable", "--now", f"php{item_version}-fpm"], timeout=90)
    installed = [package for package in packages if package_installed(package)]
    if installed:
        run(["apt-get", "purge", "-y", *installed], timeout=900)
    state.pop(item_version, None)
    save_state(state)
    return {
        "version": item_version,
        "message": f"PHP {item_version} packages were removed. Website files and databases were preserved.",
    }


def list_managed(_: dict[str, Any]) -> dict[str, Any]:
    return {"versions": sorted(load_state(), key=lambda value: tuple(int(part) for part in value.split(".")))}


OPERATIONS = {
    "check_available": check_available,
    "enable_external_repository": enable_external_repository,
    "install_version": install_version,
    "uninstall_version": uninstall_version,
    "list_managed": list_managed,
}


def main() -> None:
    data = request()
    operation = str(data.get("operation") or "")
    handler = OPERATIONS.get(operation)
    if handler is None:
        fail("Unsupported PHP runtime operation.")
    print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
