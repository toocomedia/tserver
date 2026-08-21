"""Privileged server power actions with truthful command results."""

from utils.shell import run


async def request_reboot() -> None:
    """Queue a server reboot, or report why systemd rejected it."""
    result = await run(["systemctl", "reboot", "--no-block"], timeout=10)
    if result.success:
        return

    detail = (result.stderr or result.stdout or "System reboot request failed.").strip()
    raise RuntimeError(detail[-500:])
