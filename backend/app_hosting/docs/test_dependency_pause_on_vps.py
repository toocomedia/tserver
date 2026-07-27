#!/usr/bin/env python3
"""Read-only VPS check for a hosted-app dependency pause."""
import argparse
import socket
import subprocess
import sys


def command_ok(command: list[str]) -> bool:
    return subprocess.run(command, capture_output=True, timeout=15).returncode == 0


def http_status(domain: str, *, https: bool = False) -> int | None:
    target = f"https://{domain}/" if https else "http://127.0.0.1/"
    command = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}"]
    if https:
        command += ["-k", "--resolve", f"{domain}:443:127.0.0.1"]
    else:
        command += ["-H", f"Host: {domain}"]
    result = subprocess.run(
        [*command, target],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def listener(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return True
    except OSError:
        return False


parser = argparse.ArgumentParser()
parser.add_argument("--service", required=True)
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--domain", required=True)
parser.add_argument("--expect", choices=("running", "paused"), required=True)
args = parser.parse_args()

active = command_ok(["systemctl", "is-active", "--quiet", args.service])
bound = listener(args.port)
http = http_status(args.domain)
https = http_status(args.domain, https=True)
if args.expect == "running":
    checks = {"service": active, "loopback": bound, "public proxy": any(code not in {None, 502, 503} for code in (http, https))}
else:
    checks = {"service stopped": not active, "loopback closed": not bound, "offline page": 503 in (http, https)}

for name, passed in checks.items():
    print(f"{'PASS' if passed else 'FAIL'} {name}")
print(f"HTTP status: {http if http is not None else 'unavailable'}")
print(f"HTTPS status: {https if https is not None else 'unavailable'}")
sys.exit(0 if all(checks.values()) else 1)
