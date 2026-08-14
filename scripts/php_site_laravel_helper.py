#!/usr/bin/env python3
"""Root-owned Laravel installer for isolated native PHP websites."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any


DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
USER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
STATE_ROOT = Path("/var/lib/srv-panel/php-sites")
COMPOSER = Path("/usr/local/bin/composer")
PHP_RUNTIME_HELPER = Path("/usr/local/lib/srv-panel/php-runtime-manager")
LARAVEL_PACKAGE = "laravel/laravel:^13.0"
LARAVEL_EXTENSIONS = ("curl", "mbstring", "mysql", "xml", "zip")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        fail("Invalid Laravel installer request.")
    if not isinstance(value, dict):
        fail("Invalid Laravel installer request.")
    return value


def run(
    command: list[str], *, timeout: int = 180, check: bool = True, cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, input=input_text, capture_output=True, text=True, timeout=timeout,
            check=False, shell=False, cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"Command failed: {exc}")
    if check and result.returncode != 0:
        fail((result.stderr or result.stdout or "Laravel installer command failed.").strip()[-2000:])
    return result


def site_id(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        fail("Invalid PHP site ID.")
    if result < 1 or result > 2_147_483_647:
        fail("Invalid PHP site ID.")
    return result


def domain(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not DOMAIN_RE.fullmatch(result):
        fail("Invalid PHP site domain.")
    return result


def version(value: Any) -> str:
    result = str(value or "").strip()
    if not VERSION_RE.fullmatch(result):
        fail("Invalid PHP version.")
    if tuple(map(int, result.split("."))) < (8, 3):
        fail("Laravel 13 requires PHP 8.3 or newer.")
    return result


def document_root(value: Any) -> str:
    result = str(value or "").strip().replace("\\", "/").strip("/")
    if result != "public" or PurePosixPath(result).is_absolute():
        fail("Laravel document root must be public.")
    return result


def database_values(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        fail("Invalid Laravel database credentials.")
    name = str(value.get("database") or "")
    username = str(value.get("username") or "")
    secret = str(value.get("password") or "")
    if not IDENTIFIER_RE.fullmatch(name) or not USER_RE.fullmatch(username) or not PASSWORD_RE.fullmatch(secret):
        fail("Invalid Laravel database credentials.")
    return {"database": name, "username": username, "password": secret}


def site(data: dict[str, Any]) -> dict[str, Any]:
    item_id = site_id(data.get("site_id"))
    site_domain = domain(data.get("domain"))
    php_version = version(data.get("version"))
    document_root(data.get("document_root"))
    root = Path("/var/www") / site_domain
    item = {
        "id": item_id,
        "domain": site_domain,
        "version": php_version,
        "root": root,
        "public": root / "public",
        "user": f"srvphp{item_id}"[:31],
        "state": STATE_ROOT / str(item_id),
    }
    reject_unsafe_path_chain(Path("/var/www"), root)
    reject_unsafe_path_chain(STATE_ROOT, item["state"])
    try:
        pwd.getpwnam(item["user"])
    except KeyError:
        fail("PHP website user does not exist. Repair the site runtime first.")
    if not root.is_dir() or root.is_symlink() or not item["public"].is_dir() or item["public"].is_symlink():
        fail("Laravel requires a provisioned PHP website root.")
    return item


def reject_unsafe_path_chain(base: Path, target: Path) -> None:
    try:
        relative = target.relative_to(base)
    except ValueError:
        fail("Laravel path escaped its managed root.")
    current = base
    for part in ("", *relative.parts):
        if part:
            current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            fail(f"Unsafe Laravel path component: {current}.")


def require_composer() -> None:
    if not COMPOSER.is_file() or COMPOSER.is_symlink():
        fail("Panel-managed Composer is unavailable. Run the SRV Panel updater first.")


def extension_status(php_version: str) -> dict[str, Any]:
    packages = [f"php{php_version}-{name}" for name in LARAVEL_EXTENSIONS]
    missing = []
    for package in packages:
        result = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package], timeout=10, check=False)
        if result.returncode != 0 or not result.stdout.startswith("ii"):
            missing.append(package)
    return {"ready": not missing, "required_packages": packages, "missing_packages": missing}


def install_extensions(php_version: str) -> dict[str, Any]:
    if not PHP_RUNTIME_HELPER.is_file() or PHP_RUNTIME_HELPER.is_symlink():
        fail("PHP runtime helper is missing. Run the SRV Panel updater first.")
    result = run(
        [str(PHP_RUNTIME_HELPER)], timeout=900,
        input_text=json.dumps({
            "operation": "install_site_extensions",
            "version": php_version,
            "extensions": list(LARAVEL_EXTENSIONS),
        }),
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("PHP runtime helper returned an invalid extension response.")
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        fail("PHP runtime helper failed to install Laravel extensions.")
    return dict(payload["result"])


def site_environment(item: dict[str, Any]) -> tuple[dict[str, str], Path]:
    base = item["state"] / "tmp"
    if not base.is_dir() or base.is_symlink():
        fail("PHP website temporary directory is unavailable. Repair the site runtime first.")
    work = Path(tempfile.mkdtemp(prefix="laravel-", dir=base))
    account = pwd.getpwnam(item["user"])
    os.chown(work, account.pw_uid, account.pw_gid)
    os.chmod(work, 0o700)
    home, cache = work / "home", work / "cache"
    home.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    os.chown(home, account.pw_uid, account.pw_gid)
    os.chown(cache, account.pw_uid, account.pw_gid)
    return {
        "HOME": str(home),
        "COMPOSER_HOME": str(home),
        "COMPOSER_CACHE_DIR": str(cache),
        "COMPOSER_NO_INTERACTION": "1",
    }, work


def run_as_site(
    item: dict[str, Any], arguments: list[str], *, cwd: Path, environment: dict[str, str],
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    command = [
        "runuser", "-u", item["user"], "--", "env",
        *(f"{key}={value}" for key, value in environment.items()),
        f"/usr/bin/php{item['version']}", *arguments,
    ]
    return run(command, timeout=timeout, cwd=cwd)


def starter_placeholder(item: dict[str, Any]) -> bool:
    entries = list(item["root"].iterdir())
    if len(entries) != 1 or entries[0] != item["public"]:
        return False
    public_entries = list(item["public"].iterdir())
    if len(public_entries) != 1 or public_entries[0].name != "index.php" or not public_entries[0].is_file():
        return False
    return "PHP site ready" in public_entries[0].read_text(encoding="utf-8", errors="ignore")


def is_laravel_project(root: Path) -> bool:
    return all((root / value).is_file() for value in ("artisan", "composer.json", "public/index.php", "vendor/autoload.php"))


def remove_starter(item: dict[str, Any]) -> None:
    placeholder = item["public"] / "index.php"
    placeholder.unlink()
    item["public"].rmdir()


def replace_env_values(path: Path, values: dict[str, str], item: dict[str, Any]) -> None:
    if path.is_symlink():
        fail("Laravel environment file is unsafe.")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"Could not read Laravel environment file: {exc}")
    for key, value in values.items():
        pattern = re.compile(rf"(?m)^#?\s*{re.escape(key)}=.*$")
        line = f"{key}={value}"
        if pattern.search(content):
            content = pattern.sub(line, content, count=1)
        else:
            content = f"{content.rstrip()}\n{line}\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        account = pwd.getpwnam(item["user"])
        os.chown(temporary, account.pw_uid, account.pw_gid)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def env_values(item: dict[str, Any], database: dict[str, str], scheme: str) -> dict[str, str]:
    return {
        "APP_NAME": f'"{item["domain"]}"',
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "APP_URL": f"{scheme}://{item['domain']}",
        "DB_CONNECTION": "mysql",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3306",
        "DB_DATABASE": database["database"],
        "DB_USERNAME": database["username"],
        "DB_PASSWORD": database["password"],
    }


def configure_laravel(
    item: dict[str, Any], root: Path, database: dict[str, str], scheme: str,
    environment: dict[str, str],
) -> None:
    env_path = root / ".env"
    if not env_path.is_file():
        example = root / ".env.example"
        if not example.is_file() or example.is_symlink():
            fail("Laravel environment template is missing.")
        shutil.copyfile(example, env_path)
    replace_env_values(env_path, env_values(item, database, scheme), item)
    run_as_site(item, ["artisan", "key:generate", "--force", "--no-interaction"], cwd=root, environment=environment)
    run_as_site(item, ["artisan", "migrate", "--force", "--no-interaction"], cwd=root, environment=environment, timeout=600)
    run_as_site(item, ["artisan", "config:clear", "--no-interaction"], cwd=root, environment=environment)


def install(data: dict[str, Any]) -> dict[str, Any]:
    item = site(data)
    database = database_values(data.get("database"))
    scheme = str(data.get("scheme") or "http")
    if scheme not in {"http", "https"}:
        fail("Invalid Laravel URL scheme.")
    require_composer()
    status = extension_status(item["version"])
    if not status["ready"]:
        fail(f"Missing Laravel PHP extensions: {', '.join(status['missing_packages'])}.")
    environment, temporary = site_environment(item)
    try:
        if is_laravel_project(item["root"]):
            configure_laravel(item, item["root"], database, scheme, environment)
        else:
            staging = item["root"] / ".srv-panel-laravel-installing"
            if staging.exists():
                if staging.is_symlink() or not staging.is_dir():
                    fail("Laravel staging directory is unsafe.")
                shutil.rmtree(staging)
            if not starter_placeholder(item):
                fail("Laravel requires a new empty website root.")
            run_as_site(
                item,
                [str(COMPOSER), "create-project", "--prefer-dist", "--no-dev", "--no-progress", LARAVEL_PACKAGE, str(staging)],
                cwd=item["root"], environment=environment, timeout=900,
            )
            if not is_laravel_project(staging):
                fail("Composer did not produce a complete Laravel project.")
            configure_laravel(item, staging, database, scheme, environment)
            remove_starter(item)
            for entry in staging.iterdir():
                shutil.move(str(entry), item["root"] / entry.name)
            staging.rmdir()
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return {"installed": True, "url": f"{scheme}://{item['domain']}", "package": LARAVEL_PACKAGE}


def update_url(data: dict[str, Any]) -> dict[str, Any]:
    item = site(data)
    scheme = str(data.get("scheme") or "http")
    if scheme not in {"http", "https"}:
        fail("Invalid Laravel URL scheme.")
    if not is_laravel_project(item["root"]):
        fail("Laravel project files are unavailable. Retry Laravel setup first.")
    path = item["root"] / ".env"
    replace_env_values(path, {"APP_URL": f"{scheme}://{item['domain']}"}, item)
    environment, temporary = site_environment(item)
    try:
        run_as_site(item, ["artisan", "config:clear", "--no-interaction"], cwd=item["root"], environment=environment)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return {"url": f"{scheme}://{item['domain']}"}


def update_database_password(data: dict[str, Any]) -> dict[str, Any]:
    item = site(data)
    database = database_values(data.get("database"))
    if not is_laravel_project(item["root"]):
        fail("Laravel project files are unavailable. Retry Laravel setup first.")
    replace_env_values(item["root"] / ".env", {
        "DB_CONNECTION": "mysql",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3306",
        "DB_DATABASE": database["database"],
        "DB_USERNAME": database["username"],
        "DB_PASSWORD": database["password"],
    }, item)
    environment, temporary = site_environment(item)
    try:
        run_as_site(item, ["artisan", "config:clear", "--no-interaction"], cwd=item["root"], environment=environment)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return {"updated": True}


def status(data: dict[str, Any]) -> dict[str, Any]:
    php_version = version(data.get("version"))
    return {
        "composer_available": COMPOSER.is_file() and not COMPOSER.is_symlink(),
        "laravel_package": LARAVEL_PACKAGE,
        **extension_status(php_version),
    }


OPERATIONS = {
    "status": status,
    "install_extensions": lambda data: install_extensions(version(data.get("version"))),
    "install": install,
    "update_url": update_url,
    "update_database_password": update_database_password,
}


def main() -> None:
    if os.geteuid() != 0:
        fail("Laravel installer must run as root.")
    data = request()
    handler = OPERATIONS.get(str(data.get("operation") or ""))
    if handler is None:
        fail("Unsupported Laravel installer operation.")
    print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
