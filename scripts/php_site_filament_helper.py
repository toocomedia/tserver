#!/usr/bin/env python3
"""Root-owned installer for Filament on fresh panel-managed Laravel sites."""
from __future__ import annotations
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
VERSION_RE = re.compile(r"^\d+\.\d+$")
STATE_ROOT = Path("/var/lib/srv-panel/php-sites")
COMPOSER = Path("/usr/local/bin/composer")
FILAMENT_PACKAGE = "filament/filament:^5.0"
def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)
def request() -> dict:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        fail("Invalid Filament installer request.")
    if not isinstance(value, dict):
        fail("Invalid Filament installer request.")
    return value
def run(command: list[str], *, cwd: Path | None = None, timeout: int = 600) -> None:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"Filament installer command failed: {exc}")
    if result.returncode:
        fail((result.stderr or result.stdout or "Filament installer command failed.").strip()[-2000:])
def site_id(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        fail("Invalid PHP site ID.")
    if result < 1 or result > 2_147_483_647:
        fail("Invalid PHP site ID.")
    return result


def clean_text(value: object, label: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or any(ord(char) < 32 for char in result):
        fail(f"Invalid Filament administrator {label}.")
    return result


def site(data: dict) -> dict:
    domain = str(data.get("domain") or "").strip().lower()
    version = str(data.get("version") or "").strip()
    if not DOMAIN_RE.fullmatch(domain) or not VERSION_RE.fullmatch(version):
        fail("Invalid Filament website request.")
    if tuple(map(int, version.split("."))) < (8, 3):
        fail("Filament requires PHP 8.3 or newer.")
    if str(data.get("document_root") or "").strip("/") != "public":
        fail("Filament document root must be public.")
    item_id = site_id(data.get("site_id"))
    root = Path("/var/www") / domain
    for path in (root, root / "artisan", root / "composer.json", root / "public" / "index.php"):
        if not path.exists() or path.is_symlink():
            fail("Filament requires a complete panel-managed Laravel project.")
    user = f"srvphp{item_id}"[:31]
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        fail("PHP website user does not exist. Repair the site runtime first.")
    state = STATE_ROOT / str(item_id) / "tmp"
    if not state.is_dir() or state.is_symlink():
        fail("PHP website temporary directory is unavailable. Repair the site runtime first.")
    return {"domain": domain, "root": root, "version": version, "user": user, "account": account, "state": state}


def admin(data: dict) -> dict:
    value = data.get("filament")
    if not isinstance(value, dict):
        fail("Filament administrator details are required.")
    name = clean_text(value.get("admin_name"), "name", 120)
    email = clean_text(value.get("admin_email"), "email", 255).lower()
    password = str(value.get("admin_password") or "")
    if not EMAIL_RE.fullmatch(email) or len(password) < 12 or len(password) > 512 or "\n" in password or "\r" in password:
        fail("Invalid Filament administrator credentials.")
    return {"name": name, "email": email, "password": password}


def environment(item: dict, values: dict) -> tuple[dict, Path]:
    temporary = Path(tempfile.mkdtemp(prefix="filament-", dir=item["state"]))
    os.chown(temporary, item["account"].pw_uid, item["account"].pw_gid)
    home, cache = temporary / "home", temporary / "cache"
    home.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    for path in (home, cache):
        os.chown(path, item["account"].pw_uid, item["account"].pw_gid)
    return {
        "HOME": str(home), "COMPOSER_HOME": str(home), "COMPOSER_CACHE_DIR": str(cache),
        "COMPOSER_NO_INTERACTION": "1", "PATH": "/usr/local/bin:/usr/bin:/bin", **values,
    }, temporary


def run_as_site(item: dict, arguments: list[str], env: dict, *, timeout: int = 900) -> None:
    run([
        "runuser", "-u", item["user"], "--", "env", "-i",
        *(f"{key}={value}" for key, value in env.items()), f"/usr/bin/php{item['version']}", *arguments,
    ], cwd=item["root"], timeout=timeout)


def write(path: Path, content: str, item: dict) -> None:
    if path.exists() and path.is_symlink():
        fail("Filament project file is unsafe.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chown(temporary, item["account"].pw_uid, item["account"].pw_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def replace_env(path: Path, key: str, value: str, item: dict) -> None:
    if not path.is_file() or path.is_symlink():
        fail("Laravel environment file is unavailable.")
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(?m)^#?\s*{re.escape(key)}=.*$")
    line = f"{key}={value}"
    write(path, pattern.sub(line, content, count=1) if pattern.search(content) else f"{content.rstrip()}\n{line}\n", item)


def enable_access(item: dict, values: dict) -> None:
    model = item["root"] / "app" / "Models" / "User.php"
    content = model.read_text(encoding="utf-8") if model.is_file() and not model.is_symlink() else ""
    if "FilamentUser" not in content:
        marker = "use Illuminate\\Foundation\\Auth\\User as Authenticatable;"
        if marker not in content or "class User extends Authenticatable" not in content:
            fail("Unsupported Laravel User model. Add FilamentUser access manually.")
        content = content.replace(marker, "use Filament\\Models\\Contracts\\FilamentUser;\nuse Filament\\Panel;\n" + marker)
        content = content.replace("class User extends Authenticatable", "class User extends Authenticatable implements FilamentUser", 1)
        content = content.rstrip()[:-1] + "\n    public function canAccessPanel(Panel $panel): bool\n    {\n        return $this->email === config('srv_panel.filament_admin_email');\n    }\n}\n"
        write(model, content, item)
    config = item["root"] / "config" / "srv_panel.php"
    expected = "<?php\n\nreturn [\n    'filament_admin_email' => env('FILAMENT_ADMIN_EMAIL'),\n];\n"
    if config.exists() and (not config.is_file() or config.is_symlink() or config.read_text(encoding="utf-8") != expected):
        fail("Filament panel configuration already exists and is not panel-managed.")
    write(config, expected, item)
    replace_env(item["root"] / ".env", "FILAMENT_ADMIN_EMAIL", values["email"], item)


def install(data: dict) -> dict:
    item, values = site(data), admin(data)
    if not COMPOSER.is_file() or COMPOSER.is_symlink():
        fail("Panel-managed Composer is unavailable. Run the SRV Panel updater first.")
    env, temporary = environment(item, {
        "FILAMENT_ADMIN_NAME": values["name"], "FILAMENT_ADMIN_EMAIL": values["email"], "FILAMENT_ADMIN_PASSWORD": values["password"],
    })
    try:
        run_as_site(item, [str(COMPOSER), "require", FILAMENT_PACKAGE, "--no-progress", "--no-interaction"], env)
        run_as_site(item, ["artisan", "filament:install", "--panels", "--no-interaction"], env)
        enable_access(item, values)
        run_as_site(item, ["artisan", "config:clear", "--no-interaction"], env)
        code = """require 'vendor/autoload.php';$app=require 'bootstrap/app.php';$app->make(Illuminate\\Contracts\\Console\\Kernel::class)->bootstrap();$c=App\\Models\\User::class;$u=$c::query()->firstOrNew(['email'=>getenv('FILAMENT_ADMIN_EMAIL')]);$u->name=getenv('FILAMENT_ADMIN_NAME');$u->password=Illuminate\\Support\\Facades\\Hash::make(getenv('FILAMENT_ADMIN_PASSWORD'));$u->save();"""
        run_as_site(item, ["-r", code], env)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return {"installed": True, "admin_url": f"https://{item['domain']}/admin", "package": FILAMENT_PACKAGE}


def status(data: dict) -> dict:
    return {"composer_available": COMPOSER.is_file() and not COMPOSER.is_symlink(), "package": FILAMENT_PACKAGE}


def main() -> None:
    if os.geteuid() != 0:
        fail("Filament installer must run as root.")
    data = request()
    handler = {"install": install, "status": status}.get(str(data.get("operation") or ""))
    if handler is None:
        fail("Unsupported Filament installer operation.")
    print(json.dumps({"ok": True, "result": handler(data)}))


if __name__ == "__main__":
    main()
