#!/usr/bin/env python3
"""Root-owned, allowlisted PHP-FPM package lifecycle helper for SRV Panel."""
from __future__ import annotations

import json
try:
    import fcntl
except ImportError:
    fcntl = None
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
LOCK_PATH = STATE_PATH.parent / "operation.lock"
# These are the native, version-specific packages required by every PHP
# Website preset currently offered by the panel.  They deliberately remain a
# small, reviewed baseline: Composer packages and arbitrary ext-* suggestions
# are never treated as substitutes for system extensions.
SITE_EXTENSION_NAMES = ("curl", "gd", "intl", "mbstring", "mysql", "xml", "zip", "opcache")
SITE_EXTENSION_SET = frozenset(SITE_EXTENSION_NAMES)

EXTENSION_METADATA = {
    "bcmath": {"category": "Core & Math", "description": "Arbitrary precision mathematics"},
    "bz2": {"category": "Compression", "description": "Bzip2 compression archive support"},
    "curl": {"category": "Networking", "description": "cURL HTTP client library"},
    "gd": {"category": "Graphics", "description": "GD graphics library and image processing"},
    "gmp": {"category": "Core & Math", "description": "GNU Multiple Precision arithmetic"},
    "imagick": {"category": "Graphics", "description": "ImageMagick advanced image processing"},
    "intl": {"category": "Localization", "description": "Internationalization and ICU formatting"},
    "ldap": {"category": "Networking", "description": "Lightweight Directory Access Protocol"},
    "mbstring": {"category": "Strings", "description": "Multibyte string encoding support"},
    "memcached": {"category": "Caching", "description": "Memcached distributed caching client"},
    "mongodb": {"category": "Database", "description": "MongoDB NoSQL driver"},
    "mysql": {"category": "Database", "description": "MySQL, MariaDB, and PDO MySQL driver"},
    "opcache": {"category": "Performance", "description": "Zend OPcache opcode execution caching"},
    "pgsql": {"category": "Database", "description": "PostgreSQL and PDO PostgreSQL driver"},
    "readline": {"category": "Core & Utility", "description": "GNU Readline interactive terminal support"},
    "redis": {"category": "Caching", "description": "Redis memory caching and session driver"},
    "soap": {"category": "Web Services", "description": "SOAP protocol client and server"},
    "sqlite3": {"category": "Database", "description": "SQLite3 database and PDO SQLite driver"},
    "ssh2": {"category": "Networking", "description": "SSH2 secure shell protocol bindings"},
    "xml": {"category": "Parsing", "description": "XML, DOM, and SimpleXML document processing"},
    "zip": {"category": "Compression", "description": "Zip archive compression and extraction"},
}

AVAILABLE_EXTENSION_NAMES = tuple(EXTENSION_METADATA.keys())
AVAILABLE_EXTENSION_SET = frozenset(AVAILABLE_EXTENSION_NAMES)

FPM_MODULES = {
    "bcmath": frozenset({"bcmath"}),
    "bz2": frozenset({"bz2"}),
    "curl": frozenset({"curl"}),
    "gd": frozenset({"gd"}),
    "gmp": frozenset({"gmp"}),
    "imagick": frozenset({"imagick"}),
    "intl": frozenset({"intl"}),
    "ldap": frozenset({"ldap"}),
    "mbstring": frozenset({"mbstring"}),
    "memcached": frozenset({"memcached"}),
    "mongodb": frozenset({"mongodb"}),
    "mysql": frozenset({"mysqli", "pdo_mysql"}),
    "opcache": frozenset({"zend opcache"}),
    "pgsql": frozenset({"pgsql", "pdo_pgsql"}),
    "readline": frozenset({"readline"}),
    "redis": frozenset({"redis"}),
    "soap": frozenset({"soap"}),
    "sqlite3": frozenset({"sqlite3", "pdo_sqlite"}),
    "ssh2": frozenset({"ssh2"}),
    "xml": frozenset({"dom", "xml"}),
    "zip": frozenset({"zip"}),
}
EXTERNAL_REPOSITORY_PPA = "ppa:ondrej/php"
EXTERNAL_REPOSITORY_MARKERS = (
    "ppa.launchpadcontent.net/ondrej/php",
    "ppa.launchpad.net/ondrej/php",
)
PPA_SUPPORTED_UBUNTU_CODENAMES = frozenset({"jammy", "noble"})


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


def os_release() -> dict[str, str]:
    try:
        return {
            key: value.strip().strip('"').strip("'")
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
            if "=" in line
            for key, value in (line.split("=", 1),)
        }
    except OSError:
        fail("Cannot identify this Linux distribution from /etc/os-release.")


def apt_source_files() -> list[Path]:
    source_files = [Path("/etc/apt/sources.list")]
    source_directory = Path("/etc/apt/sources.list.d")
    if source_directory.is_dir():
        source_files.extend(source_directory.glob("*.list"))
        source_files.extend(source_directory.glob("*.sources"))
    return source_files


def external_repository_configured() -> bool:
    for source_file in apt_source_files():
        try:
            contents = source_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if source_file.suffix == ".sources":
            for stanza in re.split(r"\n\s*\n", contents):
                lowered = stanza.lower()
                if not any(marker in lowered for marker in EXTERNAL_REPOSITORY_MARKERS):
                    continue
                if not re.search(r"^enabled:\s*no\s*$", stanza, re.IGNORECASE | re.MULTILINE):
                    return True
            continue
        for line in contents.splitlines():
            if line.lstrip().startswith("#"):
                continue
            lowered = line.lower()
            if any(marker in lowered for marker in EXTERNAL_REPOSITORY_MARKERS):
                return True
    return False


def _disable_list_suite(contents: str, codename: str) -> tuple[str, bool]:
    changed = False
    result: list[str] = []
    suite_pattern = re.compile(rf"(?:^|\s){re.escape(codename)}(?:\s|$)", re.IGNORECASE)
    for line in contents.splitlines(keepends=True):
        lowered = line.lower()
        if (
            not line.lstrip().startswith("#")
            and any(marker in lowered for marker in EXTERNAL_REPOSITORY_MARKERS)
            and suite_pattern.search(line)
        ):
            indentation = line[: len(line) - len(line.lstrip())]
            line = f"{indentation}# {line.lstrip()}"
            changed = True
        result.append(line)
    return "".join(result), changed


def _disable_deb822_suite(contents: str, codename: str) -> tuple[str, bool]:
    changed = False
    parts = re.split(r"(\n[ \t]*\n)", contents)
    for index in range(0, len(parts), 2):
        stanza = parts[index]
        lowered = stanza.lower()
        if not any(marker in lowered for marker in EXTERNAL_REPOSITORY_MARKERS):
            continue
        suites = re.search(r"^suites:\s*(.+)$", stanza, re.IGNORECASE | re.MULTILINE)
        if not suites or codename not in suites.group(1).lower().split():
            continue
        if re.search(r"^enabled:\s*no\s*$", stanza, re.IGNORECASE | re.MULTILINE):
            continue
        if re.search(r"^enabled:", stanza, re.IGNORECASE | re.MULTILINE):
            stanza = re.sub(
                r"^enabled:.*$", "Enabled: no", stanza,
                count=1, flags=re.IGNORECASE | re.MULTILINE,
            )
        else:
            stanza = f"Enabled: no\n{stanza}"
        parts[index] = stanza
        changed = True
    return "".join(parts), changed


def disable_unpublished_ppa_suite() -> list[str]:
    release = os_release()
    if release.get("ID", "").lower() != "ubuntu":
        return []
    codename = (
        release.get("UBUNTU_CODENAME")
        or release.get("VERSION_CODENAME")
        or ""
    ).lower()
    if not codename or codename in PPA_SUPPORTED_UBUNTU_CODENAMES:
        return []
    changed_files: list[str] = []
    for source_file in apt_source_files():
        try:
            contents = source_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if source_file.suffix == ".sources":
            updated, changed = _disable_deb822_suite(contents, codename)
        else:
            updated, changed = _disable_list_suite(contents, codename)
        if not changed:
            continue
        temporary = source_file.with_name(f"{source_file.name}.srv-panel.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, stat.S_IMODE(source_file.stat().st_mode))
        os.replace(temporary, source_file)
        changed_files.append(str(source_file))
    return changed_files


def refresh_apt() -> None:
    disabled = disable_unpublished_ppa_suite()
    if disabled:
        print(
            "==> Disabled an unpublished Ondrej PHP PPA suite: "
            + ", ".join(disabled),
            file=sys.stderr,
        )
    print("==> Refreshing configured APT repositories...", file=sys.stderr)
    run(["apt-get", "update", "-qq"], timeout=300)


def require_supported_ppa_platform() -> None:
    values = os_release()
    if values.get("ID", "").lower() != "ubuntu":
        fail("The external PHP repository action is supported only on Ubuntu. On Debian, use PHP versions available from configured APT sources.")
    codename = (
        values.get("UBUNTU_CODENAME")
        or values.get("VERSION_CODENAME")
        or "unknown"
    ).lower()
    if codename not in PPA_SUPPORTED_UBUNTU_CODENAMES:
        fail(
            f"The external PHP PPA does not publish packages for Ubuntu "
            f"{values.get('VERSION_ID', 'unknown')} ({codename}). Use PHP versions "
            "available from Ubuntu's configured APT sources."
        )


def verify_fpm(item_version: str) -> None:
    active = run(["systemctl", "is-active", f"php{item_version}-fpm"], timeout=30).stdout.strip() == "active"
    socket_path = Path(f"/run/php/php{item_version}-fpm.sock")
    try:
        socket_ok = stat.S_ISSOCK(socket_path.stat().st_mode)
    except OSError:
        socket_ok = False
    if not active or not socket_ok:
        fail(f"PHP {item_version}-FPM did not become healthy; expected socket {socket_path}.")


def verify_fpm_extensions(item_version: str, extensions: tuple[str, ...]) -> None:
    """Confirm the FPM SAPI, rather than only CLI, has each requested module."""
    binary = shutil.which(f"php-fpm{item_version}")
    if not binary:
        fail(f"PHP {item_version}-FPM binary is unavailable after installation.")
    output = run([binary, "-m"], timeout=30).stdout
    loaded = {
        line.strip().lower()
        for line in output.splitlines()
        if line.strip() and not (line.startswith("[") and line.endswith("]"))
    }
    missing = [
        extension
        for extension in extensions
        if not FPM_MODULES[extension].issubset(loaded)
    ]
    if missing:
        fail(
            f"PHP {item_version}-FPM did not load required extensions: {', '.join(missing)}. "
            "Check its FPM conf.d files before retrying."
        )


def install_version(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    state = load_state()
    fpm_package = f"php{item_version}-fpm"
    cli_package = f"php{item_version}-cli"
    extension_packages = [f"php{item_version}-{name}" for name in SITE_EXTENSION_NAMES]
    packages = [fpm_package, cli_package, *extension_packages]
    if package_installed(fpm_package) and item_version not in state:
        fail(f"PHP {item_version} is installed outside SRV Panel and cannot be adopted automatically.")
    refresh_apt()
    for package in packages:
        apt_candidate(package)
    print(f"==> Installing PHP {item_version}-FPM and the panel extension baseline...", file=sys.stderr)
    run(["apt-get", "install", "-y", "--no-install-recommends", *packages], timeout=900)
    run(["systemctl", "enable", "--now", f"php{item_version}-fpm"], timeout=90)
    verify_fpm(item_version)
    verify_fpm_extensions(item_version, SITE_EXTENSION_NAMES)
    state[item_version] = sorted(set(state.get(item_version, []) + packages))
    save_state(state)
    return {
        "version": item_version,
        "installed_packages": packages,
        "message": f"PHP {item_version} installed with the panel extension baseline and a healthy PHP-FPM socket.",
    }


def install_site_extensions(data: dict[str, Any]) -> dict[str, Any]:
    """Install an allowlisted extension and remember only packages the panel added."""
    item_version = version(data.get("version"))
    requested = data.get("extensions")
    if (
        not isinstance(requested, list) or not requested
        or any(str(name) not in SITE_EXTENSION_SET for name in requested)
    ):
        fail("Invalid PHP site extension request.")
    names = sorted({str(name) for name in requested})
    state = load_state()
    if item_version not in state:
        fail(f"PHP {item_version} is not managed by SRV Panel.")
    packages = [f"php{item_version}-{name}" for name in names]
    added = [package for package in packages if not package_installed(package)]
    if added:
        refresh_apt()
        for package in added:
            apt_candidate(package)
        run(["apt-get", "install", "-y", "--no-install-recommends", *added], timeout=900)
        state[item_version] = sorted(set(state[item_version] + added))
        save_state(state)
        run(["systemctl", "reload-or-restart", f"php{item_version}-fpm"], timeout=90)
        verify_fpm(item_version)
    return {"version": item_version, "installed_packages": added, "required_packages": packages}


def check_available(_: dict[str, Any]) -> dict[str, Any]:
    refresh_apt()
    return {"message": "PHP version availability was refreshed from the configured APT sources."}


def enable_external_repository(_: dict[str, Any]) -> dict[str, Any]:
    """Enable the one reviewed PHP PPA; repository URLs are never user input."""
    require_supported_ppa_platform()
    if external_repository_configured():
        refresh_apt()
        return {"message": "The external PHP repository is already enabled; package availability was refreshed."}
    add_repository = shutil.which("add-apt-repository")
    if not add_repository:
        print("==> Installing Ubuntu repository management support...", file=sys.stderr)
        refresh_apt()
        run(["apt-get", "install", "-y", "software-properties-common"], timeout=300)
        add_repository = shutil.which("add-apt-repository")
    if not add_repository:
        fail("Ubuntu repository management support could not be installed.")
    print("==> Enabling the external PHP repository...", file=sys.stderr)
    run([add_repository, "--yes", EXTERNAL_REPOSITORY_PPA], timeout=300)
    if not external_repository_configured():
        fail("The external PHP repository could not be verified after it was added.")
    refresh_apt()
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


def set_all_enabled(data: dict[str, Any]) -> dict[str, Any]:
    """Start or stop all panel-managed PHP-FPM services without removing data."""
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        fail("Invalid PHP runtime state.")
    state = load_state()
    versions = [
        item_version
        for item_version in sorted(state, key=lambda value: tuple(int(part) for part in value.split(".")))
        if package_installed(f"php{item_version}-fpm")
    ]
    if not versions:
        fail("No panel-managed PHP-FPM versions are installed.")
    units = [f"php{item_version}-fpm" for item_version in versions]
    if enabled:
        run(["systemctl", "enable", "--now", *units], timeout=180)
        for item_version in versions:
            verify_fpm(item_version)
        return {
            "versions": versions,
            "message": "All panel-managed PHP-FPM services were enabled.",
        }

    run(["systemctl", "disable", "--now", *units], timeout=180)
    still_running = []
    for unit in units:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.stdout.strip() == "active":
            still_running.append(unit)
    if still_running:
        fail(f"PHP-FPM services are still running: {', '.join(still_running)}.")
    return {
        "versions": versions,
        "message": "All panel-managed PHP-FPM services were disabled. Website files and databases were preserved.",
    }


def list_extensions(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    binary = shutil.which(f"php-fpm{item_version}") or shutil.which(f"php{item_version}")
    loaded_modules = set()
    if binary:
        out = run([binary, "-m"], timeout=15).stdout
        loaded_modules = {
            line.strip().lower()
            for line in out.splitlines()
            if line.strip() and not (line.startswith("[") and line.endswith("]"))
        }

    results = []
    seen_names = set()
    for ext_name, meta in EXTENSION_METADATA.items():
        seen_names.add(ext_name)
        pkg = f"php{item_version}-{ext_name}"
        installed = package_installed(pkg)
        required_fpm = FPM_MODULES.get(ext_name, {ext_name})
        loaded = bool(required_fpm and required_fpm.issubset(loaded_modules))
        results.append({
            "name": ext_name,
            "package": pkg,
            "installed": installed,
            "loaded": loaded,
            "category": meta.get("category", "General"),
            "description": meta.get("description", ""),
        })

    # Also detect any installed or loaded extensions outside the default metadata list
    for mod in sorted(loaded_modules):
        if mod not in seen_names and not mod.startswith("zend"):
            pkg = f"php{item_version}-{mod}"
            results.append({
                "name": mod,
                "package": pkg,
                "installed": package_installed(pkg) or True,
                "loaded": True,
                "category": "Custom",
                "description": f"PHP {item_version} {mod} extension",
            })

    return {"version": item_version, "extensions": results}


def install_extension(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    ext_name = str(data.get("extension") or "").strip().lower()
    if not re.fullmatch(r"^[a-z0-9_]+$", ext_name):
        fail(f"Invalid PHP extension name format: {ext_name}")
    pkg = f"php{item_version}-{ext_name}"
    state = load_state()
    refresh_apt()
    apt_candidate(pkg)
    run(["apt-get", "install", "-y", "--no-install-recommends", pkg], timeout=900)
    if item_version in state:
        state[item_version] = sorted(set(state.get(item_version, []) + [pkg]))
        save_state(state)
    run(["systemctl", "reload-or-restart", f"php{item_version}-fpm"], timeout=90)
    verify_fpm(item_version)
    return {
        "version": item_version,
        "extension": ext_name,
        "package": pkg,
        "message": f"Extension {ext_name} for PHP {item_version} installed successfully.",
    }


def uninstall_extension(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    ext_name = str(data.get("extension") or "").strip().lower()
    if not re.fullmatch(r"^[a-z0-9_]+$", ext_name):
        fail(f"Invalid PHP extension name format: {ext_name}")
    pkg = f"php{item_version}-{ext_name}"
    state = load_state()
    if package_installed(pkg):
        run(["apt-get", "purge", "-y", pkg], timeout=900)
    if item_version in state:
        state[item_version] = [p for p in state.get(item_version, []) if p != pkg]
        save_state(state)
    run(["systemctl", "reload-or-restart", f"php{item_version}-fpm"], timeout=90)
    verify_fpm(item_version)
    return {
        "version": item_version,
        "extension": ext_name,
        "package": pkg,
        "message": f"Extension {ext_name} for PHP {item_version} uninstalled successfully.",
    }


def search_available_extensions(data: dict[str, Any]) -> dict[str, Any]:
    item_version = version(data.get("version"))
    query = str(data.get("query") or "").strip().lower()
    prefix = f"php{item_version}-"
    results = []
    if os.name != "nt":
        try:
            res = subprocess.run(
                ["apt-cache", "search", f"^{prefix}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in res.stdout.splitlines():
                if " - " in line:
                    pkg, desc = line.split(" - ", 1)
                    pkg = pkg.strip()
                    desc = desc.strip()
                    if pkg.startswith(prefix):
                        ext_name = pkg[len(prefix):]
                        if not query or query in ext_name.lower() or query in desc.lower():
                            installed = package_installed(pkg)
                            results.append({
                                "name": ext_name,
                                "package": pkg,
                                "description": desc,
                                "installed": installed,
                            })
        except Exception:
            pass
    return {"version": item_version, "results": results[:30]}


def list_managed(_: dict[str, Any]) -> dict[str, Any]:
    return {"versions": sorted(load_state(), key=lambda value: tuple(int(part) for part in value.split(".")))}


OPERATIONS = {
    "check_available": check_available,
    "enable_external_repository": enable_external_repository,
    "install_version": install_version,
    "install_site_extensions": install_site_extensions,
    "list_extensions": list_extensions,
    "search_available_extensions": search_available_extensions,
    "install_extension": install_extension,
    "uninstall_extension": uninstall_extension,
    "set_all_enabled": set_all_enabled,
    "uninstall_version": uninstall_version,
    "list_managed": list_managed,
}

MUTATING_OPERATIONS = frozenset({
    "check_available",
    "enable_external_repository",
    "install_version",
    "install_site_extensions",
    "install_extension",
    "uninstall_extension",
    "set_all_enabled",
    "uninstall_version",
})


def main() -> None:
    data = request()
    operation = str(data.get("operation") or "")
    handler = OPERATIONS.get(operation)
    if handler is None:
        fail("Unsupported PHP runtime operation.")
    if operation not in MUTATING_OPERATIONS:
        print(json.dumps({"ok": True, "result": handler(data)}))
        return

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        os.chmod(LOCK_PATH, stat.S_IRUSR | stat.S_IWUSR)
        if fcntl is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("Another PHP runtime operation is already running.")
        print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
