#!/usr/bin/env python3
"""Read-only VPS diagnostic for one deployed Python app.

Usage: python3 test_on_vps.py --service srv-python-42 --port 9101 --domain api.example.com
"""
import argparse, os, socket, subprocess, sys
from pathlib import Path

def check(label, command=None, ok=None):
    try:
        passed = ok() if ok else subprocess.run(command, capture_output=True, timeout=15).returncode == 0
    except Exception: passed = False
    print(("PASS" if passed else "FAIL") + " " + label)
    return passed

p = argparse.ArgumentParser(); p.add_argument("--service", required=True); p.add_argument("--port", type=int, required=True); p.add_argument("--domain"); p.add_argument("--env")
a = p.parse_args(); results = []
results += [check("Git", ["git", "--version"]), check("SSH", ["ssh", "-V"]), check("Python", ["python3", "-c", "import venv,pip"])]
results += [check("systemd service", ["systemctl", "is-active", "--quiet", a.service]), check("nginx config", ["nginx", "-t"])]
results += [check("loopback listener", ok=lambda: socket.create_connection(("127.0.0.1", a.port), 3).close() is None)]
if a.domain: results += [check("Nginx proxy", ["curl", "-fsS", "-H", f"Host: {a.domain}", "http://127.0.0.1/"])]
if a.env:
    results += [check("protected environment", ok=lambda: Path(a.env).is_file() and (Path(a.env).stat().st_mode & 0o077) == 0)]
sys.exit(0 if all(results) else 1)
