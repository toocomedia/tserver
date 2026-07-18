# tserver

Lightweight VPS control panel — domains, DNS (PowerDNS), SSL (Certbot), reverse proxy.

## Install (fresh Ubuntu 22.04 / 24.04)

**Recommended** (download then run — shows full logs):

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh -o /tmp/tserver-get.sh
sudo bash /tmp/tserver-get.sh
rm -f /tmp/tserver-get.sh
```

One-liner also works:

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```

You should immediately see: `==> tserver installer starting...`

The installer asks for:

1. **SERVER_IP** — auto-detected (Enter to confirm)  
2. **Panel domain?** — `n` = IP only, or `y` + domain  
3. **Email** — for Let's Encrypt  

Temp files under `/tmp` are removed after install. Panel lives in `/opt/srv-panel`.

Open: **http://YOUR.SERVER.IP/**

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh -o /tmp/tserver-upd.sh
sudo bash /tmp/tserver-upd.sh
rm -f /tmp/tserver-upd.sh
```

## Service

```bash
systemctl status srv-panel
journalctl -u srv-panel -n 50
curl -s http://127.0.0.1:8000/api/health
```
