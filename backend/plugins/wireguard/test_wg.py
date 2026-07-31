#!/usr/bin/env python3
"""
WireGuard Plugin — Full Service + System Diagnostic Script
Tests every function the router calls, exactly as the panel does.

Run on the VPS as the panel user:
    python3 /opt/srv-panel/app/plugins/wireguard/test_wg.py
"""
import os
import sys
import re
import subprocess
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — locate panel app root
# ---------------------------------------------------------------------------
PANEL_APP = Path(__file__).parent.parent.parent  # plugins/wireguard/../../.. = app/
# Try common install locations
for candidate in [PANEL_APP, Path("/opt/srv-panel/app"), Path("/home/panel/srv-t/backend")]:
    if (candidate / "plugins" / "wireguard" / "service.py").exists():
        PANEL_APP = candidate
        break

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
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
        for line in str(detail).strip().splitlines():
            print(f"       {line}")
    results.append((label, passed))
    return passed

def warn(label, detail=""):
    print(f"{WARN}  {label}")
    if detail:
        for line in str(detail).strip().splitlines():
            print(f"       {line}")

def info(label, detail=""):
    print(f"{INFO}  {label}")
    if detail:
        for line in str(detail).strip().splitlines():
            print(f"       {line}")

def run(cmd, input=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=15)
    except Exception as e:
        class Fake:
            returncode = -1; stdout = ""; stderr = str(e)
        return Fake()

def sudo(*args, input=None):
    return run(["sudo", "-n", *args], input=input)

WG_DIR    = Path("/etc/wireguard")
WG_IFACE  = "wg0"
WG_CONF   = WG_DIR / f"{WG_IFACE}.conf"
SERVER_PUB = WG_DIR / "server.pub"


# ===========================================================================
print(f"\n{TITLE}══════════════════════════════════════════{RESET}")
print(f"{TITLE}  WireGuard Plugin — Full Diagnostics      {RESET}")
print(f"{TITLE}══════════════════════════════════════════{RESET}\n")

# ---------------------------------------------------------------------------
# [1] System prerequisites
# ---------------------------------------------------------------------------
print(f"{TITLE}[1] System Prerequisites{RESET}")
info(f"Running as: {run(['whoami']).stdout.strip()}")
info(f"Panel app path: {PANEL_APP}")

wg_ok  = bool(shutil.which("wg"))
wgq_ok = bool(shutil.which("wg-quick"))
check("wg binary in PATH",       wg_ok,  shutil.which("wg") or "not found")
check("wg-quick binary in PATH", wgq_ok, shutil.which("wg-quick") or "not found")

r = run(["systemctl", "is-active", "wg-quick@wg0"])
check("wg-quick@wg0 service active", r.stdout.strip() == "active", r.stdout.strip())

# ---------------------------------------------------------------------------
# [2] Sudo permissions
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[2] Sudo Permissions (panel user){RESET}")

checks = [
    (["cat", str(WG_CONF)],          "sudo cat wg0.conf"),
    (["cat", str(SERVER_PUB)],       "sudo cat server.pub"),
    (["wg", "show", WG_IFACE],       "sudo wg show wg0"),
    (["wg-quick", "strip", WG_IFACE],"sudo wg-quick strip wg0"),
]
for cmd, label in checks:
    r = sudo(*cmd)
    check(label, r.returncode == 0,
          r.stderr.strip() if r.returncode != 0 else (r.stdout.strip()[:80] or "ok"))

# ---------------------------------------------------------------------------
# [3] Import real service module (via importlib to avoid FastAPI chain)
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[3] Import Service Module{RESET}")
import importlib.util as _ilu

_svc_path = PANEL_APP / "plugins" / "wireguard" / "service.py"
try:
    _spec = _ilu.spec_from_file_location("wireguard_service_mod", _svc_path)
    _mod  = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    wireguard_service = _mod.wireguard_service
    WireguardService  = _mod.WireguardService
    check("Import wireguard service module", True, str(_svc_path))
except Exception as e:
    check("Import wireguard service module", False, str(e))
    print(f"\n{FAIL}  Cannot import service — remaining tests skipped.\n")
    sys.exit(1)

# ---------------------------------------------------------------------------
# [4] is_installed()
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[4] service.is_installed(){RESET}")
try:
    installed = wireguard_service.is_installed()
    check("is_installed() returns True", installed, f"returned: {installed}")
except Exception as e:
    check("is_installed() no exception", False, str(e))

# ---------------------------------------------------------------------------
# [5] get_status()
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[5] service.get_status(){RESET}")
try:
    status = wireguard_service.get_status()
    check("get_status() no exception", True)
    check("status['installed'] is True",  status.get("installed") is True,  str(status.get("installed")))
    check("status['active'] is True",     status.get("active") is True,     str(status.get("active")))
    check("status['listen_port'] == 51820", status.get("listen_port") == 51820, str(status.get("listen_port")))
    check("status['interface'] == 'wg0'", status.get("interface") == "wg0", str(status.get("interface")))
    info(f"Peers reported: {status.get('peers')}")
    info(f"Server pubkey:  {str(status.get('server_pubkey',''))[:32]}…")
except Exception as e:
    check("get_status() no exception", False, str(e))

# ---------------------------------------------------------------------------
# [6] get_server_pubkey()
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[6] service.get_server_pubkey(){RESET}")
try:
    pubkey = wireguard_service.get_server_pubkey()
    check("get_server_pubkey() returns non-empty string", bool(pubkey), pubkey[:32] + "…" if pubkey else "empty")
except Exception as e:
    check("get_server_pubkey() no exception", False, str(e))

# ---------------------------------------------------------------------------
# [7] list_peers()
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[7] service.list_peers(){RESET}")
try:
    peers_before = wireguard_service.list_peers()
    check("list_peers() no exception", True)
    check("list_peers() returns a list", isinstance(peers_before, list), type(peers_before).__name__)
    info(f"Existing peers: {len(peers_before)}")
    for i, p in enumerate(peers_before, 1):
        info(f"  Peer {i}: name={p.get('name','(none)')!r}  ip={p.get('allowed_ips')}  key={p.get('pubkey','')[:20]}…")
except Exception as e:
    check("list_peers() no exception", False, str(e))
    peers_before = []

# ---------------------------------------------------------------------------
# [8] add_peer()  ← the core UI action
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[8] service.add_peer('ui-test-peer'){RESET}")
test_peer = None
try:
    test_peer = wireguard_service.add_peer("ui-test-peer")
    check("add_peer() no exception", True)
    check("add_peer() returns dict",           isinstance(test_peer, dict),    type(test_peer).__name__)
    check("add_peer() has 'pubkey'",           bool(test_peer.get("pubkey")),  test_peer.get("pubkey","")[:32])
    check("add_peer() has 'private_key'",      bool(test_peer.get("private_key")), "present (not shown)")
    check("add_peer() has 'peer_ip'",          bool(test_peer.get("peer_ip")), test_peer.get("peer_ip",""))
    check("add_peer() has 'allowed_ips'",      bool(test_peer.get("allowed_ips")), test_peer.get("allowed_ips",""))

    # Verify peer appears in list_peers()
    peers_after = wireguard_service.list_peers()
    pubkeys_after = [p.get("pubkey") for p in peers_after]
    check("Peer appears in list_peers() after add", test_peer["pubkey"] in pubkeys_after)

    # Verify peer appears in live kernel state
    r = sudo("wg", "show", WG_IFACE)
    check("Peer visible in live wg show", test_peer["pubkey"][:20] in r.stdout,
          r.stdout.strip()[:120] if r.returncode == 0 else r.stderr.strip())

except Exception as e:
    check("add_peer() no exception", False, str(e))

# ---------------------------------------------------------------------------
# [9] get_peer_config()  ← config download action
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[9] service.get_peer_config() — config file generation{RESET}")
if test_peer:
    try:
        conf = wireguard_service.get_peer_config(
            peer_name        = test_peer["name"],
            peer_private_key = test_peer["private_key"],
            peer_ip          = test_peer["peer_ip"],
            server_pubkey    = wireguard_service.get_server_pubkey(),
            server_endpoint  = "1.2.3.4",  # mock server IP
        )
        check("get_peer_config() no exception", True)
        check("Config contains [Interface]",     "[Interface]" in conf)
        check("Config contains [Peer]",          "[Peer]" in conf)
        check("Config contains PrivateKey",       "PrivateKey" in conf)
        check("Config contains peer IP",          test_peer["peer_ip"] in conf)
        check("Config contains server pubkey",    wireguard_service.get_server_pubkey()[:10] in conf)
        check("Config contains AllowedIPs = 0.0.0.0/0", "0.0.0.0/0" in conf)
        check("Config contains DNS",              "DNS" in conf)
        print()
        info("Generated .conf preview:")
        for line in conf.splitlines():
            if "PrivateKey" not in line:  # don't print the key
                info(f"  {line}")
    except Exception as e:
        check("get_peer_config() no exception", False, str(e))
else:
    warn("Skipping — no test peer was created in section 8")

# ---------------------------------------------------------------------------
# [10] remove_peer()  ← delete UI action
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[10] service.remove_peer() — delete peer{RESET}")
if test_peer:
    try:
        removed = wireguard_service.remove_peer(test_peer["pubkey"])
        check("remove_peer() returns True", removed is True, str(removed))

        # Verify gone from list_peers()
        peers_final = wireguard_service.list_peers()
        pubkeys_final = [p.get("pubkey") for p in peers_final]
        check("Peer gone from list_peers() after remove", test_peer["pubkey"] not in pubkeys_final)

        # Verify gone from kernel
        r = sudo("wg", "show", WG_IFACE)
        check("Peer gone from live wg show", test_peer["pubkey"][:20] not in r.stdout)

    except Exception as e:
        check("remove_peer() no exception", False, str(e))
        warn(f"Test peer may still be in wg0.conf — clean up manually: sudo wg set wg0 peer {test_peer.get('pubkey','')} remove")
else:
    warn("Skipping — no test peer was created in section 8")

# ---------------------------------------------------------------------------
# [11] tunnel_restart()  ← restart UI action
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[11] service.tunnel_restart(){RESET}")
try:
    ok = wireguard_service.tunnel_restart()
    check("tunnel_restart() returns True", ok is True, str(ok))

    # Verify still active after restart
    import time; time.sleep(2)
    r = run(["systemctl", "is-active", "wg-quick@wg0"])
    check("Tunnel still active after restart", r.stdout.strip() == "active", r.stdout.strip())
except Exception as e:
    check("tunnel_restart() no exception", False, str(e))

# ---------------------------------------------------------------------------
# [12] Final wg show state
# ---------------------------------------------------------------------------
print(f"\n{TITLE}[12] Final Live State — wg show{RESET}")
r = sudo("wg", "show", WG_IFACE)
if r.returncode == 0:
    for line in r.stdout.strip().splitlines():
        info(line)
else:
    warn(f"wg show failed: {r.stderr.strip()}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{TITLE}══════════════════════════════════════════{RESET}")
print(f"{TITLE}  SUMMARY{RESET}")
total  = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed
print(f"  Passed : {passed}/{total}")
if failed:
    print(f"\n  Failed checks:")
    for label, ok in results:
        if not ok:
            print(f"    {FAIL}  {label}{RESET}")
else:
    print(f"\n  {PASS} All {total} checks passed — plugin is fully functional!")
print(f"{TITLE}══════════════════════════════════════════{RESET}\n")
