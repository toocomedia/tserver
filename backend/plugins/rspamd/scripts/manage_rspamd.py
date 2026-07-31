#!/usr/bin/env python3
"""
backend/plugins/rspamd/scripts/manage_rspamd.py — Privileged Rspamd Helper.

Run as root via sudoers (NOPASSWD).
Manages systemctl for rspamd, patches /etc/maddy/maddy.conf, updates action score thresholds,
and executes installer/uninstaller lifecycle scripts safely as root.

Usage:
    python3 manage_rspamd.py install
    python3 manage_rspamd.py uninstall
    python3 manage_rspamd.py service-control <start|stop|restart>
    python3 manage_rspamd.py update-thresholds <reject_score> <add_header_score>
    python3 manage_rspamd.py sync-maddy <enable|disable>
"""
import sys
import os
import re
import subprocess
from pathlib import Path

MADDY_CONF = Path("/etc/maddy/maddy.conf")
RSPAMD_ACTIONS_CONF = Path("/etc/rspamd/local.d/actions.conf")
SCRIPT_DIR = Path(__file__).resolve().parent


def run(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )


def install_plugin():
    install_script = SCRIPT_DIR / "install_rspamd.sh"
    if not install_script.exists():
        print(f"ERROR: Installer script {install_script} missing.", file=sys.stderr)
        sys.exit(1)

    res = run(["bash", str(install_script)], check=False)
    if res.returncode != 0:
        print(f"ERROR: Installation failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        sys.exit(1)
    print("Rspamd plugin installed successfully.")


def uninstall_plugin():
    uninstall_script = SCRIPT_DIR / "uninstall_rspamd.sh"
    if not uninstall_script.exists():
        print(f"ERROR: Uninstaller script {uninstall_script} missing.", file=sys.stderr)
        sys.exit(1)

    res = run(["bash", str(uninstall_script)], check=False)
    if res.returncode != 0:
        print(f"ERROR: Uninstallation failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        sys.exit(1)
    print("Rspamd plugin uninstalled successfully.")


def service_control(action: str):
    if action not in ("start", "stop", "restart"):
        print(f"ERROR: Invalid action '{action}'", file=sys.stderr)
        sys.exit(1)

    res = run(["systemctl", action, "rspamd"], check=False)
    if res.returncode != 0:
        print(f"ERROR: systemctl {action} rspamd failed: {res.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    print(f"Rspamd service {action}ed successfully.")


def update_thresholds(reject_str: str, add_header_str: str):
    try:
        reject = float(reject_str)
        add_header = float(add_header_str)
    except ValueError:
        print("ERROR: Thresholds must be numeric values.", file=sys.stderr)
        sys.exit(1)

    if add_header >= reject:
        print("ERROR: add_header threshold must be smaller than reject threshold.", file=sys.stderr)
        sys.exit(1)

    RSPAMD_ACTIONS_CONF.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Managed by srv-panel Rspamd plugin\n"
        f"reject = {reject};\n"
        f"add_header = {add_header};\n"
    )

    try:
        RSPAMD_ACTIONS_CONF.write_text(content, encoding="utf-8")
    except Exception as exc:
        print(f"ERROR: Writing actions.conf failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # Reload rspamd configuration
    run(["systemctl", "reload", "rspamd"], check=False)
    print("Rspamd thresholds updated and service reloaded.")


def sync_maddy(mode: str):
    if mode not in ("enable", "disable"):
        print("ERROR: mode must be 'enable' or 'disable'", file=sys.stderr)
        sys.exit(1)

    if not MADDY_CONF.exists():
        print(f"WARNING: Maddy configuration file {MADDY_CONF} does not exist.", file=sys.stderr)
        sys.exit(0)

    try:
        content = MADDY_CONF.read_text(encoding="utf-8")
        has_rspamd = "rspamd http://127.0.0.1:11333" in content or "check.rspamd" in content

        if mode == "enable":
            if not has_rspamd:
                lines = content.splitlines()
                new_lines = []
                inserted = False
                for line in lines:
                    if "fail_open" in line:
                        continue
                    new_lines.append(line)
                    if "check {" in line and not inserted:
                        new_lines.append("        rspamd http://127.0.0.1:11333")
                        inserted = True
                content = "\n".join(new_lines) + "\n"
                MADDY_CONF.write_text(content, encoding="utf-8")
                print("Injected Rspamd check into Maddy configuration.")
        else: # disable
            lines = [
                line for line in content.splitlines()
                if "rspamd http://127.0.0.1:11333" not in line
                and "check.rspamd" not in line
                and "fail_open" not in line
            ]
            content = "\n".join(lines) + "\n"
            MADDY_CONF.write_text(content, encoding="utf-8")
            print("Removed Rspamd check from Maddy configuration.")

        # Validate maddy config before restart
        val = run(["maddy", "--config", str(MADDY_CONF), "check"], check=False)
        if val.returncode != 0:
            print(f"WARNING: Maddy config validation warning: {val.stderr.strip()}", file=sys.stderr)
            # Revert patch if invalid
            lines = [
                line for line in MADDY_CONF.read_text(encoding="utf-8").splitlines()
                if "rspamd http://127.0.0.1:11333" not in line
                and "check.rspamd" not in line
                and "fail_open" not in line
            ]
            MADDY_CONF.write_text("\n".join(lines) + "\n", encoding="utf-8")

        run(["systemctl", "restart", "maddy"], check=False)
        print("Maddy service restarted cleanly.")

    except Exception as exc:
        print(f"ERROR: Syncing Maddy configuration failed: {exc}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: manage_rspamd.py <install|uninstall|service-control|update-thresholds|sync-maddy> [args...]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "install":
        install_plugin()
    elif cmd == "uninstall":
        uninstall_plugin()
    elif cmd == "service-control" and len(sys.argv) >= 3:
        service_control(sys.argv[2])
    elif cmd == "update-thresholds" and len(sys.argv) >= 4:
        update_thresholds(sys.argv[2], sys.argv[3])
    elif cmd == "sync-maddy" and len(sys.argv) >= 3:
        sync_maddy(sys.argv[2])
    else:
        print(f"ERROR: Unknown or incomplete command '{cmd}'", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
