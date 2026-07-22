"""
utils/shell.py — Safe subprocess runner
All shell commands go through here. Never use os.system() elsewhere.
When PRIVILEGED_SUDO is true and process is not root, privileged
commands run via `sudo -n` (install.sh installs sudoers for panel user).
"""
import asyncio
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# First-arg binaries that may need root on a real VPS
_PRIVILEGED_BINS = frozenset({
    "nginx", "certbot", "openssl", "tee", "ln", "rm", "mkdir", "chmod", "chown", "ufw", "bash", "systemctl", "sysctl",
})


@dataclass
class ShellResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


def _use_sudo() -> bool:
    # Windows / non-Unix have no geteuid — never use sudo there
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() == 0:
        return False
    try:
        import config
        return bool(getattr(config, "PRIVILEGED_SUDO", False))
    except Exception:
        return False


def _maybe_sudo(args: list[str]) -> list[str]:
    if not args or not _use_sudo():
        return args
    bin_name = Path(args[0]).name
    if bin_name in _PRIVILEGED_BINS or args[0].startswith("/usr/sbin/nginx"):
        return ["sudo", "-n", *args]
    return args


async def run(command: str | list[str], timeout: int = 30) -> ShellResult:
    """
    Run a shell command asynchronously.
    Accepts a string (will be split via shlex) or a list of args.
    Returns ShellResult with stdout, stderr, returncode, and success flag.
    """
    if isinstance(command, str):
        args = shlex.split(command)
    else:
        args = list(command)

    args = _maybe_sudo(args)
    logger.info("Shell: %s", " ".join(args))

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.error("Shell timeout: %s", " ".join(args))
        return ShellResult(success=False, stdout="", stderr="Timeout", returncode=-1)
    except FileNotFoundError as exc:
        logger.error("Shell command not found: %s — %s", args[0], exc)
        return ShellResult(success=False, stdout="", stderr=str(exc), returncode=-1)

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    success = proc.returncode == 0

    if not success:
        logger.warning(
            "Shell failed (rc=%d): %s\nstderr: %s",
            proc.returncode, " ".join(args), stderr,
        )
    else:
        logger.debug("Shell ok: %s", " ".join(args))

    return ShellResult(
        success=success,
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode,
    )


async def write_file(path: str | Path, content: str) -> None:
    """Write a file; uses sudo tee when PRIVILEGED_SUDO and not root."""
    path = str(path)
    if not _use_sudo():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        return

    # Ensure parent exists
    parent = str(Path(path).parent)
    await run(["mkdir", "-p", parent], timeout=10)

    args = _maybe_sudo(["tee", path])
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(
        proc.communicate(input=content.encode("utf-8")),
        timeout=15,
    )
    if proc.returncode != 0:
        err = stderr_b.decode("utf-8", errors="replace")
        raise OSError(f"Failed to write {path}: {err}")


async def remove_path(path: str | Path) -> None:
    """Remove a file or symlink; uses sudo when needed."""
    path = str(path)
    p = Path(path)
    if not p.exists() and not p.is_symlink():
        return
    if not _use_sudo():
        p.unlink()
        return
    result = await run(["rm", "-f", path], timeout=10)
    if not result.success:
        raise OSError(f"Failed to remove {path}: {result.stderr}")


async def symlink(target: str | Path, link_path: str | Path) -> None:
    """Create symlink link_path -> target; uses sudo when needed."""
    target, link_path = str(target), str(link_path)
    await remove_path(link_path)
    if not _use_sudo():
        os.symlink(target, link_path)
        return
    result = await run(["ln", "-sfn", target, link_path], timeout=10)
    if not result.success:
        raise OSError(f"Failed to symlink {link_path}: {result.stderr}")


async def nginx_test() -> ShellResult:
    """Run nginx -t and return result."""
    return await run(["nginx", "-t"])


async def nginx_reload() -> ShellResult:
    """Reload nginx gracefully (nginx -s reload)."""
    return await run(["nginx", "-s", "reload"])
