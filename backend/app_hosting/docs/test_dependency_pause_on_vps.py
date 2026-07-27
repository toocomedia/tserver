#!/usr/bin/env python3
"""Read-only VPS check for a hosted-app dependency pause."""
import argparse
import socket
import subprocess
import sys


def command_ok(command: list[str]) -> bool:
    return subprocess.run(command, capture_output=True, timeout=15).returncode == 0


def http_status(domain: str) -> int | None:
    result = subprocess.run(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Host: {domain}", "http://127.0.0.1/"],
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
status = http_status(args.domain)
if args.expect == "running":
    checks = {"service": active, "loopback": bound, "public proxy": status not in {None, 502, 503}}
else:
    checks = {"service stopped": not active, "loopback closed": not bound, "offline page": status == 503}

for name, passed in checks.items():
    print(f"{'PASS' if passed else 'FAIL'} {name}")
print(f"HTTP status: {status if status is not None else 'unavailable'}")
sys.exit(0 if all(checks.values()) else 1)
