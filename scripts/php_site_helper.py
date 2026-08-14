#!/usr/bin/env python3
"""Root-owned stdin-only helper for isolated native PHP websites."""
from __future__ import annotations

import grp
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import quote


DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
USER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
ROOT_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STATE_ROOT = Path("/var/lib/srv-panel/php-sites")
LOG_ROOT = Path("/var/log/srv-panel/php-sites")
WP_CLI = Path("/usr/local/bin/wp")
WORDPRESS_EXTENSIONS = ("curl", "gd", "intl", "mbstring", "mysql", "xml", "zip", "opcache")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        fail("Invalid PHP site helper request.")
    if not isinstance(value, dict):
        fail("Invalid PHP site helper request.")
    return value


def run(
    command: list[str], *, timeout: int = 120, check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, input=input_text, capture_output=True, text=True, timeout=timeout,
            check=False, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"Command failed: {exc}")
    if check and result.returncode != 0:
        fail((result.stderr or result.stdout or "PHP site helper command failed.").strip()[-2000:])
    return result


def atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


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
    return result


def document_root(value: Any) -> str:
    result = str(value or "").strip().replace("\\", "/").strip("/")
    path = PurePosixPath(result)
    if (
        not result or path.is_absolute() or len(result) > 255 or ".." in path.parts
        or any(not ROOT_PART_RE.fullmatch(part) for part in path.parts)
    ):
        fail("Invalid PHP document root.")
    return result


def panel_user(value: Any) -> str:
    result = str(value or "").strip()
    try:
        pwd.getpwnam(result)
    except KeyError:
        fail("Panel user does not exist.")
    return result


def identity(item_id: int) -> tuple[str, str]:
    username = f"srvphp{item_id}"
    return username[:31], username[:31]


def paths(data: dict[str, Any]) -> dict[str, Any]:
    item_id = site_id(data.get("site_id"))
    site_domain = domain(data.get("domain"))
    php_version = version(data.get("version"))
    relative_root = document_root(data.get("document_root"))
    root = Path("/var/www") / site_domain
    public = root.joinpath(*PurePosixPath(relative_root).parts)
    username, groupname = identity(item_id)
    item = {
        "id": item_id, "domain": site_domain, "version": php_version,
        "document_root": relative_root, "root": root, "public": public,
        "user": username, "group": groupname,
        "pool": f"srv-panel-site-{item_id}",
        "socket": Path(f"/run/php/srv-site-{item_id}-{php_version}.sock"),
        "pool_path": Path(f"/etc/php/{php_version}/fpm/pool.d/srv-panel-site-{item_id}.conf"),
        "state": STATE_ROOT / str(item_id), "logs": LOG_ROOT / str(item_id),
    }
    reject_unsafe_path_chain(Path("/var/www"), item["public"])
    reject_unsafe_path_chain(Path("/var/lib/srv-panel"), item["state"])
    reject_unsafe_path_chain(Path("/var/log/srv-panel"), item["logs"])
    return item


def reject_unsafe_path_chain(base: Path, target: Path) -> None:
    try:
        relative = target.relative_to(base)
    except ValueError:
        fail("PHP website path escaped its managed root.")
    current = base
    for part in ("", *relative.parts):
        if part:
            current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            fail(f"Unsafe managed PHP website path component: {current}.")


def require_runtime(item: dict[str, Any]) -> None:
    if not Path(f"/etc/php/{item['version']}/fpm/pool.d").is_dir():
        fail(f"PHP {item['version']}-FPM configuration is unavailable.")
    if not Path(f"/usr/bin/php{item['version']}").is_file():
        fail(f"PHP {item['version']} CLI is unavailable.")


def ensure_user(item: dict[str, Any], panel: str) -> None:
    try:
        pwd.getpwnam(item["user"])
    except KeyError:
        run([
            "useradd", "--system", "--no-create-home", "--home-dir", str(item["root"]),
            "--shell", "/usr/sbin/nologin", "--user-group", item["user"],
        ])
    group = grp.getgrgid(pwd.getpwnam(panel).pw_gid).gr_name
    for directory in (item["root"], item["public"], item["state"], item["logs"], item["state"] / "sessions", item["state"] / "tmp"):
        directory.mkdir(parents=True, exist_ok=True)
    run(["chown", "-R", f"{item['user']}:{group}", str(item["root"])])
    run(["chown", f"{panel}:{item['group']}", str(item["state"])])
    run(["chown", "-R", f"{item['user']}:{group}", str(item["state"] / "sessions"), str(item["state"] / "tmp")])
    run(["chown", f"root:{group}", str(item["logs"])])
    run(["chmod", "0750", str(item["root"]), str(item["state"]), str(item["logs"])])
    run(["chmod", "0770", str(item["public"]), str(item["state"] / "sessions"), str(item["state"] / "tmp")])
    if shutil.which("setfacl") is None:
        fail("POSIX ACL support is missing. Install the acl package, then retry.")
    acl = f"u:{item['user']}:rwx,u:{panel}:rwx,u:www-data:r-x,m::rwx,o::---"
    default_acl = f"u::{'rwx'},u:{item['user']}:rwx,u:{panel}:rwx,u:www-data:r-x,m::rwx,o::---"
    run(["setfacl", "-R", "-m", acl, str(item["root"])])
    directories = [Path(root) for root, _, _ in os.walk(item["root"])]
    for offset in range(0, len(directories), 200):
        run(["setfacl", "-d", "-m", default_acl, *map(str, directories[offset:offset + 200])])
    for name in ("access.log", "nginx-error.log", "php-error.log"):
        log_path = item["logs"] / name
        log_path.touch(exist_ok=True)
        owner = pwd.getpwnam(item["user"]).pw_uid if name == "php-error.log" else 0
        os.chown(log_path, owner, pwd.getpwnam(panel).pw_gid)
        os.chmod(log_path, 0o640)


def database_values(value: Any) -> dict[str, str]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        fail("Invalid PHP website database credentials.")
    name = str(value.get("database") or "")
    username = str(value.get("username") or "")
    secret = str(value.get("password") or "")
    if not IDENTIFIER_RE.fullmatch(name) or not USER_RE.fullmatch(username) or not PASSWORD_RE.fullmatch(secret):
        fail("Invalid PHP website database credentials.")
    return {"database": name, "username": username, "password": secret}


def pool_content(item: dict[str, Any], database: dict[str, str]) -> str:
    environment = ""
    if database:
        url = f"mysql://{quote(database['username'], safe='')}:{quote(database['password'], safe='')}@127.0.0.1:3306/{database['database']}"
        values = {
            "DB_HOST": "127.0.0.1", "DB_PORT": "3306", "DB_DATABASE": database["database"],
            "DB_USERNAME": database["username"], "DB_PASSWORD": database["password"], "DATABASE_URL": url,
        }
        environment = "".join(f"env[{key}] = {value}\n" for key, value in values.items())
    return f"""; Generated by SRV Panel. Do not edit.
[{item['pool']}]
user = {item['user']}
group = {item['group']}
listen = {item['socket']}
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = ondemand
pm.max_children = 3
pm.process_idle_timeout = 10s
pm.max_requests = 500
catch_workers_output = yes
clear_env = yes
security.limit_extensions = .php
php_admin_value[open_basedir] = {item['root']}:{item['state']}:/tmp
php_admin_value[session.save_path] = {item['state'] / 'sessions'}
php_admin_value[upload_tmp_dir] = {item['state'] / 'tmp'}
php_admin_value[error_log] = {item['logs'] / 'php-error.log'}
php_admin_flag[log_errors] = on
php_admin_value[memory_limit] = 128M
php_admin_value[upload_max_filesize] = 64M
php_admin_value[post_max_size] = 64M
php_admin_value[max_execution_time] = 60
{environment}"""


def reload_fpm(php_version: str) -> None:
    binary = f"/usr/sbin/php-fpm{php_version}"
    result = run([binary, "-t"], timeout=30, check=False)
    if result.returncode != 0:
        fail((result.stderr or result.stdout or "PHP-FPM configuration test failed.").strip()[-2000:])
    run(["systemctl", "reload-or-restart", f"php{php_version}-fpm"], timeout=90)


def wait_for_socket(path: Path) -> None:
    for _ in range(50):
        try:
            if stat.S_ISSOCK(path.stat().st_mode):
                return
        except OSError:
            pass
        time.sleep(0.1)
    fail(f"PHP-FPM site socket is unavailable: {path}.")


def write_pool(item: dict[str, Any], database: dict[str, str]) -> None:
    require_runtime(item)
    atomic_write(item["pool_path"], pool_content(item, database), 0o600)
    reload_fpm(item["version"])
    wait_for_socket(item["socket"])


def remove_pool(item_id: int, php_version: str) -> None:
    path = Path(f"/etc/php/{php_version}/fpm/pool.d/srv-panel-site-{item_id}.conf")
    socket_path = Path(f"/run/php/srv-site-{item_id}-{php_version}.sock")
    if path.exists():
        path.unlink()
        reload_fpm(php_version)
    socket_path.unlink(missing_ok=True)


def starter(item: dict[str, Any]) -> None:
    existing = list(item["public"].iterdir())
    if len(existing) == 1 and existing[0].name == "index.html" and existing[0].is_file():
        content = existing[0].read_text(encoding="utf-8", errors="ignore")
        if "Site Coming Soon" in content and "configured and ready" in content:
            existing[0].unlink()
            existing = []
    if existing:
        return
    index = item["public"] / "index.php"
    atomic_write(index, "<?php\nhttp_response_code(200);\necho 'PHP site ready';\n", 0o640)
    user = pwd.getpwnam(item["user"])
    os.chown(index, user.pw_uid, user.pw_gid)


def provision(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    panel = panel_user(data.get("panel_user"))
    require_runtime(item)
    ensure_user(item, panel)
    starter(item)
    write_pool(item, database_values(data.get("database")))
    return {"user": item["user"], "socket_path": str(item["socket"]), "root_path": str(item["root"])}


def prepare_version(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    item["version"] = version(data.get("new_version"))
    item["socket"] = Path(f"/run/php/srv-site-{item['id']}-{item['version']}.sock")
    item["pool_path"] = Path(f"/etc/php/{item['version']}/fpm/pool.d/srv-panel-site-{item['id']}.conf")
    write_pool(item, database_values(data.get("database")))
    return {"socket_path": str(item["socket"]), "version": item["version"]}


def finalize_version(data: dict[str, Any]) -> dict[str, Any]:
    item_id = site_id(data.get("site_id"))
    old_version = version(data.get("old_version"))
    remove_pool(item_id, old_version)
    return {"removed_version": old_version}


def enable(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    write_pool(item, database_values(data.get("database")))
    return {"socket_path": str(item["socket"])}


def disable(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    remove_pool(item["id"], item["version"])
    return {"disabled": True}


def purge(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    versions = set()
    php_root = Path("/etc/php")
    if php_root.is_dir():
        for path in php_root.glob(f"*/fpm/pool.d/srv-panel-site-{item['id']}.conf"):
            versions.add(path.parts[-4])
            path.unlink(missing_ok=True)
    for php_version in versions:
        reload_fpm(php_version)
    run(["pkill", "-u", item["user"]], timeout=15, check=False)
    for target in (item["root"], item["state"], item["logs"]):
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
    run(["userdel", item["user"]], timeout=30, check=False)
    return {"purged": True}


def install_wordpress_extensions(data: dict[str, Any]) -> dict[str, Any]:
    php_version = version(data.get("version"))
    return install_managed_extensions(php_version, WORDPRESS_EXTENSIONS)


def check_wordpress_extensions(data: dict[str, Any]) -> dict[str, Any]:
    php_version = version(data.get("version"))
    packages = [f"php{php_version}-{name}" for name in WORDPRESS_EXTENSIONS]
    missing = []
    for package in packages:
        result = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package], timeout=10, check=False)
        if result.returncode != 0 or not result.stdout.startswith("ii"):
            missing.append(package)
    return {"ready": not missing, "required_packages": packages, "missing_packages": missing}


def check_database_extension(data: dict[str, Any]) -> dict[str, Any]:
    php_version = version(data.get("version"))
    package = f"php{php_version}-mysql"
    result = run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", package], timeout=10, check=False)
    installed = result.returncode == 0 and result.stdout.startswith("ii")
    return {"ready": installed, "required_packages": [package], "missing_packages": [] if installed else [package]}


def install_database_extension(data: dict[str, Any]) -> dict[str, Any]:
    php_version = version(data.get("version"))
    return install_managed_extensions(php_version, ("mysql",))


def install_managed_extensions(php_version: str, extensions: tuple[str, ...]) -> dict[str, Any]:
    helper = Path("/usr/local/lib/srv-panel/php-runtime-manager")
    if not helper.is_file():
        fail("PHP runtime helper is missing. Run the SRV Panel updater first.")
    result = run(
        [str(helper)], timeout=900,
        input_text=json.dumps({
            "operation": "install_site_extensions",
            "version": php_version,
            "extensions": list(extensions),
        }),
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("PHP runtime helper returned an invalid extension response.")
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        fail("PHP runtime helper failed to install site extensions.")
    return dict(payload["result"])


def run_as_site(
    item: dict[str, Any], arguments: list[str], *, timeout: int = 300, check: bool = True,
    cache_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["runuser", "-u", item["user"], "--"]
    if cache_dir is not None:
        command.extend(["/usr/bin/env", f"WP_CLI_CACHE_DIR={cache_dir}"])
    command.extend([f"/usr/bin/php{item['version']}",
        str(WP_CLI), f"--path={item['public']}", *arguments,
    ])
    return run(command, timeout=timeout, check=check)


def wordpress_values(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        fail("Invalid WordPress setup.")
    result = {key: str(value.get(key) or "").strip() for key in ("site_title", "admin_user", "admin_email")}
    result["admin_password"] = str(value.get("admin_password") or "")
    if (
        not result["site_title"] or len(result["site_title"]) > 255
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,60}", result["admin_user"])
        or result["admin_email"].count("@") != 1 or len(result["admin_password"]) < 12
    ):
        fail("Invalid WordPress administrator details.")
    return result


def install_wordpress(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    database = database_values(data.get("database"))
    wordpress = wordpress_values(data.get("wordpress"))
    scheme = str(data.get("scheme") or "http")
    if scheme not in {"http", "https"}:
        fail("Invalid WordPress URL scheme.")
    if not WP_CLI.is_file():
        fail("Panel-managed WP-CLI is unavailable. Run the SRV Panel updater first.")
    allowed = {"index.php"}
    existing = {path.name for path in item["public"].iterdir()}
    if existing - allowed and not (item["public"] / "wp-load.php").is_file():
        fail("WordPress requires an empty document root.")
    placeholder = item["public"] / "index.php"
    if placeholder.is_file() and "PHP site ready" in placeholder.read_text(encoding="utf-8", errors="ignore"):
        placeholder.unlink()
    if not (item["public"] / "wp-load.php").is_file():
        with tempfile.TemporaryDirectory(prefix=f"srv-panel-wpcli-{item['id']}-") as temporary:
            cache_dir = Path(temporary)
            user = pwd.getpwnam(item["user"])
            os.chown(cache_dir, user.pw_uid, user.pw_gid)
            run_as_site(item, ["core", "download", "--no-color"], timeout=600, cache_dir=cache_dir)
    run_as_site(item, [
        "config", "create", f"--dbname={database['database']}", f"--dbuser={database['username']}",
        f"--dbpass={database['password']}", "--dbhost=127.0.0.1", "--skip-check", "--force", "--no-color",
    ])
    installed = run_as_site(item, ["core", "is-installed", "--no-color"], timeout=60, check=False)
    if installed.returncode != 0:
        run_as_site(item, [
            "core", "install", f"--url={scheme}://{item['domain']}", f"--title={wordpress['site_title']}",
            f"--admin_user={wordpress['admin_user']}", f"--admin_password={wordpress['admin_password']}",
            f"--admin_email={wordpress['admin_email']}", "--skip-email", "--no-color",
        ], timeout=300)
    return {"installed": True, "url": f"{scheme}://{item['domain']}"}


def wordpress_url(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    scheme = str(data.get("scheme") or "http")
    if scheme not in {"http", "https"}:
        fail("Invalid WordPress URL scheme.")
    url = f"{scheme}://{item['domain']}"
    run_as_site(item, ["option", "update", "home", url, "--no-color"], timeout=120)
    run_as_site(item, ["option", "update", "siteurl", url, "--no-color"], timeout=120)
    return {"url": url}


def wordpress_database_password(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    database = database_values(data.get("database"))
    run_as_site(item, [
        "config", "set", "DB_PASSWORD", database["password"], "--type=constant", "--no-color",
    ], timeout=120)
    return {"updated": True}


def clear_wordpress_cache(data: dict[str, Any]) -> dict[str, Any]:
    item = paths(data)
    core_cache = item["root"] / ".wp-cli" / "cache" / "core"
    reject_unsafe_path_chain(item["root"], core_cache)
    if not core_cache.exists():
        return {"cleared": False}
    if core_cache.is_symlink() or not core_cache.is_dir():
        fail("WordPress core cache path is unsafe.")
    shutil.rmtree(core_cache)
    for directory in (core_cache.parent, core_cache.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break
    return {"cleared": True}


def read_logs(data: dict[str, Any]) -> dict[str, Any]:
    item_id = site_id(data.get("site_id"))
    stream = str(data.get("stream") or "")
    names = {"access": "access.log", "nginx_error": "nginx-error.log", "php": "php-error.log"}
    if stream not in names:
        fail("Invalid PHP website log stream.")
    try:
        count = int(data.get("lines", 200))
    except (TypeError, ValueError):
        fail("Invalid log line count.")
    count = max(1, min(count, 500))
    path = LOG_ROOT / str(item_id) / names[stream]
    if not path.is_file() or path.is_symlink():
        return {"stream": stream, "lines": []}
    with path.open("r", encoding="utf-8", errors="replace") as source:
        values = source.readlines()[-count:]
    return {"stream": stream, "lines": [line.rstrip("\n") for line in values]}


OPERATIONS = {
    "provision": provision,
    "prepare_version": prepare_version,
    "finalize_version": finalize_version,
    "enable": enable,
    "disable": disable,
    "purge": purge,
    "install_wordpress_extensions": install_wordpress_extensions,
    "check_wordpress_extensions": check_wordpress_extensions,
    "check_database_extension": check_database_extension,
    "install_database_extension": install_database_extension,
    "install_wordpress": install_wordpress,
    "wordpress_url": wordpress_url,
    "wordpress_database_password": wordpress_database_password,
    "clear_wordpress_cache": clear_wordpress_cache,
    "read_logs": read_logs,
}


def main() -> None:
    if os.geteuid() != 0:
        fail("PHP site helper must run as root.")
    data = request()
    handler = OPERATIONS.get(str(data.get("operation") or ""))
    if handler is None:
        fail("Unsupported PHP site helper operation.")
    print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
