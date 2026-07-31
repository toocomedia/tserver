"""
backend/plugins/wireguard/service.py — WireGuard VPN Management Service.

Handles installation checks, tunnel status, and peer CRUD by directly
reading/writing /etc/wireguard/wg0.conf and calling the wg CLI.
No daemon required — all state lives in the kernel and the config file.
"""
import os
import re
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WG_DIR        = Path("/etc/wireguard")
WG_IFACE      = "wg0"
WG_CONF       = WG_DIR / f"{WG_IFACE}.conf"
SERVER_KEY    = WG_DIR / "server.key"
SERVER_PUBKEY = WG_DIR / "server.pub"
WG_NETWORK    = "10.8.0"       # first three octets of the tunnel subnet
WG_PORT       = 51820
DNS_SERVER    = "1.1.1.1"


class WireguardService:

    # ------------------------------------------------------------------
    # Installation / Status
    # ------------------------------------------------------------------

    def is_installed(self) -> bool:
        """True when wg binary exists and wg0.conf is present."""
        has_wg  = shutil.which("wg") is not None or os.path.exists("/usr/bin/wg")
        has_conf = WG_CONF.exists()
        return has_wg and has_conf

    def get_status(self) -> Dict[str, Any]:
        """Return tunnel active state, peer count, server pubkey, and port."""
        installed = self.is_installed()
        active    = False
        peers     = 0

        if installed and os.name != "nt":
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", f"wg-quick@{WG_IFACE}"],
                    capture_output=True, text=True,
                )
                active = res.stdout.strip() == "active"
            except Exception as exc:
                logger.warning("Could not check wg-quick status: %s", exc)

        peers = len(self.list_peers())

        return {
            "installed":   installed,
            "active":      active,
            "peers":       peers,
            "server_pubkey": self.get_server_pubkey(),
            "listen_port": WG_PORT,
            "interface":   WG_IFACE,
        }

    def get_server_pubkey(self) -> str:
        """Read the server public key from disk."""
        try:
            if SERVER_PUBKEY.exists():
                return SERVER_PUBKEY.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Peer listing — parse wg0.conf
    # ------------------------------------------------------------------

    def list_peers(self) -> List[Dict[str, Any]]:
        """Parse wg0.conf and return all [Peer] blocks."""
        if not WG_CONF.exists():
            return []
        try:
            raw = WG_CONF.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Could not read wg0.conf: %s", exc)
            return []

        peers: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        for line in raw.splitlines():
            stripped = line.strip()
            if stripped == "[Peer]":
                if current is not None:
                    peers.append(current)
                current = {"name": "", "pubkey": "", "allowed_ips": "", "preshared_key": ""}
                continue
            if stripped == "[Interface]":
                if current is not None:
                    peers.append(current)
                current = None
                continue
            if current is None:
                continue

            # Parse key-value pairs inside a [Peer] block
            if "=" in stripped and not stripped.startswith("#"):
                key, _, val = stripped.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "PublicKey":
                    current["pubkey"] = val
                elif key == "AllowedIPs":
                    current["allowed_ips"] = val
                elif key == "PresharedKey":
                    current["preshared_key"] = val
            # Peer name is stored as a comment line: # Name: laptop
            elif stripped.startswith("# Name:"):
                if current is not None:
                    current["name"] = stripped[len("# Name:"):].strip()

        if current is not None:
            peers.append(current)

        return [p for p in peers if p.get("pubkey")]

    # ------------------------------------------------------------------
    # Peer management
    # ------------------------------------------------------------------

    def _next_peer_ip(self) -> str:
        """Find the next free .X address in the 10.8.0.0/24 subnet."""
        used_ips: set[int] = {1}  # .1 is the server
        for peer in self.list_peers():
            m = re.search(r"10\.8\.0\.(\d+)", peer.get("allowed_ips", ""))
            if m:
                used_ips.add(int(m.group(1)))
        for i in range(2, 255):
            if i not in used_ips:
                return f"{WG_NETWORK}.{i}"
        raise RuntimeError("No free IPs left in the 10.8.0.0/24 subnet (254 peers max).")

    def _run_wg(self, *args: str) -> subprocess.CompletedProcess:
        cmd = ["wg", *args]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
            cmd = ["sudo", "-n", *cmd]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def add_peer(self, name: str) -> Dict[str, Any]:
        """
        Generate a key pair for a new peer, assign the next free tunnel IP,
        append the [Peer] block to wg0.conf, and hot-reload via wg syncconf.

        Returns the full peer info dict (including private key for config download).
        """
        if os.name == "nt":
            # Dev/Windows mock
            return {
                "name": name,
                "pubkey": "MOCK+PUBLIC+KEY==",
                "private_key": "MOCK+PRIVATE+KEY==",
                "allowed_ips": "10.8.0.2/32",
                "preshared_key": "",
            }

        # Generate key pair
        keygen = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True)
        private_key = keygen.stdout.strip()
        pubgen = subprocess.run(
            ["wg", "pubkey"], input=private_key, capture_output=True, text=True, check=True
        )
        public_key = pubgen.stdout.strip()

        peer_ip = self._next_peer_ip()
        allowed_ips = f"{peer_ip}/32"

        # Append [Peer] block to wg0.conf
        peer_block = (
            f"\n# Name: {name}\n"
            f"[Peer]\n"
            f"PublicKey  = {public_key}\n"
            f"AllowedIPs = {allowed_ips}\n"
        )
        try:
            with WG_CONF.open("a", encoding="utf-8") as f:
                f.write(peer_block)
        except PermissionError:
            # Try writing via sudo tee
            block_bytes = peer_block.encode()
            subprocess.run(
                ["sudo", "-n", "tee", "-a", str(WG_CONF)],
                input=peer_block, text=True, check=True, capture_output=True,
            )

        # Hot-reload the running interface (no downtime)
        self._syncconf()

        return {
            "name":        name,
            "pubkey":      public_key,
            "private_key": private_key,
            "allowed_ips": allowed_ips,
            "peer_ip":     peer_ip,
        }

    def remove_peer(self, pubkey: str) -> bool:
        """Remove the [Peer] block matching pubkey from wg0.conf, then syncconf."""
        if not WG_CONF.exists():
            return False

        raw = WG_CONF.read_text(encoding="utf-8")
        new_lines: List[str] = []
        in_target = False
        removed = False

        for line in raw.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == "[Peer]":
                # Peek: look ahead is not possible line-by-line, so we buffer
                # We handle this with a two-pass approach
                in_target = False
            new_lines.append(line)

        # Two-pass: split into blocks, filter the matching one
        blocks = re.split(r"(?=^\[)", raw, flags=re.MULTILINE)
        filtered = []
        for block in blocks:
            if block.startswith("[Peer]") and pubkey in block:
                removed = True
                continue
            filtered.append(block)

        if not removed:
            return False

        new_conf = "".join(filtered)
        try:
            WG_CONF.write_text(new_conf, encoding="utf-8")
        except PermissionError:
            subprocess.run(
                ["sudo", "-n", "tee", str(WG_CONF)],
                input=new_conf, text=True, check=True, capture_output=True,
            )

        # Remove from running kernel state immediately
        self._run_wg("set", WG_IFACE, "peer", pubkey, "remove")
        return True

    def _syncconf(self) -> None:
        """Hot-reload wg0 config without bouncing the interface."""
        try:
            strip = subprocess.run(
                ["wg-quick", "strip", WG_IFACE],
                capture_output=True, text=True, check=True,
            )
            sync_cmd = ["wg", "syncconf", WG_IFACE, "/dev/stdin"]
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                sync_cmd = ["sudo", "-n"] + sync_cmd
            subprocess.run(sync_cmd, input=strip.stdout, text=True, check=True, capture_output=True)
        except Exception as exc:
            logger.warning("wg syncconf failed (interface may be down): %s", exc)

    # ------------------------------------------------------------------
    # Client config generation
    # ------------------------------------------------------------------

    def get_peer_config(
        self,
        peer_name: str,
        peer_private_key: str,
        peer_ip: str,
        server_pubkey: str,
        server_endpoint: str,
    ) -> str:
        """
        Render a WireGuard client .conf file string.
        This is written at add-time to a temp location so the user can
        download it immediately — the private key is never stored on disk.
        """
        return (
            f"# WireGuard client config — {peer_name}\n"
            f"# Generated by the panel. Keep this file private.\n\n"
            f"[Interface]\n"
            f"PrivateKey = {peer_private_key}\n"
            f"Address    = {peer_ip}/32\n"
            f"DNS        = {DNS_SERVER}\n\n"
            f"[Peer]\n"
            f"PublicKey  = {server_pubkey}\n"
            f"Endpoint   = {server_endpoint}:{WG_PORT}\n"
            f"AllowedIPs = 0.0.0.0/0\n"
            f"PersistentKeepalive = 25\n"
        )

    def tunnel_restart(self) -> bool:
        """Restart the wg-quick systemd service."""
        if os.name == "nt":
            return True
        try:
            cmd = ["systemctl", "restart", f"wg-quick@{WG_IFACE}"]
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                cmd = ["sudo", "-n"] + cmd
            res = subprocess.run(cmd, capture_output=True, text=True)
            return res.returncode == 0
        except Exception as exc:
            logger.error("Tunnel restart failed: %s", exc)
            return False


wireguard_service = WireguardService()
