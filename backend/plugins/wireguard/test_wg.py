#!/usr/bin/env python3
"""
WireGuard Plugin — VPS Diagnostic & Test Script
Run as the panel user on the VPS:

    python3 /opt/srv-panel/app/plugins/wireguard/test_wg.py

Or as root to also test root-mode paths:

    python3 /opt/srv-panel/app/plugins/wireguard/test_wg.py --as-root
"""
import os
import sys
import subprocess
import shutil
import re
import argparse
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — match service.py constants
# ---------------------------------------------------------------------------
WG_DIR        = Path("/etc/wireguard")
WG_IFACE      = "wg0"
WG_CONF       = WG_DIR / f"{WG_IFACE}.conf"
SERVER_KEY    = WG_DIR / "server.key"
SERVER_PUBKEY = WG_DIR / "server.pub"
WG_PORT       = 51820
TEST_PEER_NAME = "test-diagnostic-peer"

PASS  = "\033[92m  PASS\033[0m"
FAIL  = "\033[91m  FAIL\033[0m"
WARN  = "\033[93m  WARN\033[0m"
INFO  = "\033[94m  INFO\033[0m"
TITLE = "\033[1m\033[96m"
RESET = "\033[0m"

results = []

def check(label, passed, detail=""):
    tag = PASS if passed else FAIL
    print(f"{tag}  {label}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"       {line}")
    results.append((label, passed))
    return passed

def warn(label, detail=""):
    print(f"{WARN}  {label}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"       {line}")

def info(label, detail=""):
    print(f"{INFO}  {label}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"       {line}")

def run(cmd, input=None, check_rc=False):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=15)
        return r
    except Exception as e:
        class FakeResult:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return FakeResult()

def sudo(*args, input=None):
    cmd = ["sudo", "-n", *args]
    return run(cmd, input=input)

# ---------------------------------------------------------------------------
# Section 1 — System prerequisites
# ---------------------------------------------------------------------------
print(f"\n{TITLE}══════════════════════════════════════════{RESET}")
print(f"{TITLE}  WireGuard Plugin — VPS Diagnostics       {RESET}")
print(f"{TITLE}══════════════════════════════════════════{RESET}\n")

print(f"{TITLE}[1] System Prerequisites{RESET}")

# OS
r = run(["cat", "/etc/os-release"])
os_name = ""
for line in r.stdout.splitlines():
    if line.startswith("PRETTY_NAME="):
        os_name = line.split("=", 1)[1].strip('"')
info(f"OS: {os_name or 'unknown'}")

# Whoami
whoami = run(["whoami"]).stdout.strip()
info(f"Running as: {whoami}")

# wg binary
wg_path = shutil.which("wg") or ""
check("wg binary found", bool(wg_path), wg_path or "wg not in PATH")

# wg-quick binary
wgq_path = shutil.which("wg-quick") or ""
check("wg-quick binary found", bool(wgq_path), wgq_path or "wg-quick not in PATH")

# wg version
r = run(["wg", "--version"])
info(f"wg version: {r.stdout.strip() or r.stderr.strip()}")

# ---------------------------------------------------------------------------
# Section 2 — /etc/wireguard directory & files
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[2] /etc/wireguard Files & Permissions{RESET}")

# Directory exists
dir_exists = WG_DIR.exists()
check("/etc/wireguard/ exists", dir_exists)

if dir_exists:
    # Directory permissions
    r = run(["stat", "-c", "%a %U %G", str(WG_DIR)])
    stat_out = r.stdout.strip()
    info(f"/etc/wireguard permissions: {stat_out}")

    # wg0.conf via sudo
    r = sudo("cat", str(WG_CONF))
    can_read_conf = r.returncode == 0
    check("sudo -n cat wg0.conf works", can_read_conf,
          r.stderr.strip() if not can_read_conf else f"{len(r.stdout.splitlines())} lines read")

    if can_read_conf:
        conf_text = r.stdout
        has_interface = "[Interface]" in conf_text
        has_privatekey = "PrivateKey" in conf_text
        has_address = "Address" in conf_text
        check("wg0.conf has [Interface] block", has_interface)
        check("wg0.conf has PrivateKey", has_privatekey)
        check("wg0.conf has Address", has_address)

        # Count peers
        peer_count = conf_text.count("[Peer]")
        info(f"Peer blocks in wg0.conf: {peer_count}")

    # server.pub via sudo
    r = sudo("cat", str(SERVER_PUBKEY))
    can_read_pub = r.returncode == 0
    check("sudo -n cat server.pub works", can_read_pub,
          r.stderr.strip() if not can_read_pub else f"pubkey: {r.stdout.strip()[:32]}…")

# ---------------------------------------------------------------------------
# Section 3 — Sudo permissions
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[3] Sudo Permissions{RESET}")

# tee (needed for writing conf)
r = run(["bash", "-c", "echo test | sudo -n tee /tmp/srv-wg-test.txt > /dev/null && echo ok"])
check("sudo -n tee works", "ok" in r.stdout,
      r.stderr.strip() if "ok" not in r.stdout else "")

# cat /etc/wireguard/*
r = sudo("cat", str(WG_CONF))
check("sudo -n cat /etc/wireguard/* works", r.returncode == 0,
      r.stderr.strip() if r.returncode != 0 else "")

# wg (needed for syncconf, peer remove)
r = sudo("wg", "show", WG_IFACE)
check("sudo -n wg show wg0 works", r.returncode == 0,
      (r.stderr or r.stdout).strip() if r.returncode != 0 else r.stdout.strip()[:120])

# wg-quick strip (needed for syncconf)
r = sudo("wg-quick", "strip", WG_IFACE)
check("sudo -n wg-quick strip wg0 works", r.returncode == 0,
      (r.stderr or r.stdout).strip() if r.returncode != 0 else "ok")

# systemctl status
r = sudo("systemctl", "is-active", f"wg-quick@{WG_IFACE}")
svc_active = r.stdout.strip() == "active"
check(f"wg-quick@{WG_IFACE} systemd service is active", svc_active,
      f"status: {r.stdout.strip()}")

# ---------------------------------------------------------------------------
# Section 4 — Key generation (no sudo needed)
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[4] Key Generation (no sudo){RESET}")

r = run(["wg", "genkey"])
has_privkey = r.returncode == 0 and len(r.stdout.strip()) > 20
check("wg genkey works without sudo", has_privkey,
      r.stderr.strip() if not has_privkey else f"key length: {len(r.stdout.strip())} chars")

if has_privkey:
    privkey = r.stdout.strip()
    r2 = run(["wg", "pubkey"], input=privkey)
    has_pubkey = r2.returncode == 0 and len(r2.stdout.strip()) > 20
    check("wg pubkey (derive public key) works", has_pubkey,
          r2.stderr.strip() if not has_pubkey else f"pubkey: {r2.stdout.strip()[:32]}…")

# ---------------------------------------------------------------------------
# Section 5 — Peer add simulation
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[5] Peer Add Simulation{RESET}")

# Generate test key pair
r = run(["wg", "genkey"])
test_privkey = r.stdout.strip()
r2 = run(["wg", "pubkey"], input=test_privkey)
test_pubkey = r2.stdout.strip()
check("Generated test key pair", bool(test_privkey and test_pubkey))

# Find next free IP
next_ip = "10.8.0.2"
r = sudo("cat", str(WG_CONF))
if r.returncode == 0:
    used = set()
    for m in re.finditer(r"10\.8\.0\.(\d+)", r.stdout):
        used.add(int(m.group(1)))
    used.add(1)
    for i in range(2, 255):
        if i not in used:
            next_ip = f"10.8.0.{i}"
            break
info(f"Next available peer IP: {next_ip}")

# Write peer block via sudo tee -a
peer_block = (
    f"\n# Name: {TEST_PEER_NAME}\n"
    f"[Peer]\n"
    f"PublicKey  = {test_pubkey}\n"
    f"AllowedIPs = {next_ip}/32\n"
)
r = sudo("tee", "-a", str(WG_CONF), input=peer_block)
wrote_peer = r.returncode == 0
check("Write peer block to wg0.conf via sudo tee", wrote_peer,
      r.stderr.strip() if not wrote_peer else f"wrote {len(peer_block)} bytes")

# Hot-reload via wg syncconf
if wrote_peer:
    r_strip = sudo("wg-quick", "strip", WG_IFACE)
    if r_strip.returncode == 0:
        r_sync = sudo("wg", "syncconf", WG_IFACE, "/dev/stdin", input=r_strip.stdout)
        check("wg syncconf (live reload) works", r_sync.returncode == 0,
              r_sync.stderr.strip() if r_sync.returncode != 0 else "peers updated in kernel")
    else:
        warn("wg-quick strip failed — syncconf skipped", r_strip.stderr.strip())

# Read back conf and verify peer is there
r = sudo("cat", str(WG_CONF))
peer_visible = test_pubkey in r.stdout if r.returncode == 0 else False
check("Peer visible in wg0.conf after write", peer_visible)

# ---------------------------------------------------------------------------
# Section 6 — Peer remove simulation
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[6] Peer Remove Simulation{RESET}")

if r.returncode == 0 and peer_visible:
    conf_text = r.stdout
    # Two-pass remove
    blocks = re.split(r"(?=^\[)", conf_text, flags=re.MULTILINE)
    filtered = [b for b in blocks if not (b.startswith("[Peer]") and test_pubkey in b)]
    removed = len(filtered) < len(blocks)
    check("Identified peer block for removal", removed)

    if removed:
        new_conf = "".join(filtered)
        r2 = sudo("tee", str(WG_CONF), input=new_conf)
        check("Rewrote wg0.conf without test peer", r2.returncode == 0,
              r2.stderr.strip() if r2.returncode != 0 else "")

        # Remove from kernel
        r3 = sudo("wg", "set", WG_IFACE, "peer", test_pubkey, "remove")
        check("wg set peer remove (kernel)", r3.returncode == 0,
              r3.stderr.strip() if r3.returncode != 0 else "peer removed from kernel")

        # Verify gone
        r4 = sudo("cat", str(WG_CONF))
        check("Peer no longer in wg0.conf", test_pubkey not in r4.stdout)
else:
    warn("Skipping peer remove — peer was not written successfully")

# ---------------------------------------------------------------------------
# Section 7 — Uninstall script check
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[7] Uninstall Script Check{RESET}")

script_dir = Path(__file__).parent / "scripts"
uninstall_sh = script_dir / "uninstall_wireguard.sh"
check("uninstall_wireguard.sh exists", uninstall_sh.exists(), str(uninstall_sh))
if uninstall_sh.exists():
    r = run(["bash", "-n", str(uninstall_sh)])  # syntax check only, don't run
    check("uninstall script syntax valid", r.returncode == 0,
          r.stderr.strip() if r.returncode != 0 else "")
    warn("NOT running uninstall — this would remove WireGuard. Run manually if needed.")

# ---------------------------------------------------------------------------
# Section 8 — Current wg show output
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[8] Live wg show{RESET}")
r = sudo("wg", "show", WG_IFACE)
if r.returncode == 0:
    for line in r.stdout.strip().splitlines():
        info(line)
else:
    warn(f"wg show failed: {(r.stderr or r.stdout).strip()}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{TITLE}══════════════════════════════════════════{RESET}")
print(f"{TITLE}  SUMMARY{RESET}")
total   = len(results)
passed  = sum(1 for _, ok in results if ok)
failed  = total - passed
print(f"  Passed : {passed}/{total}")
if failed:
    print(f"\n  {FAIL} Failed checks:")
    for label, ok in results:
        if not ok:
            print(f"    • {label}")
else:
    print(f"\n  {PASS} All checks passed!")
print(f"{TITLE}══════════════════════════════════════════{RESET}\n")
