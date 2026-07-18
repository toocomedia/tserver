# tserver

Lightweight VPS control panel — domains, DNS (PowerDNS), SSL (Certbot), reverse proxy.

## Install (fresh Ubuntu 22.04 / 24.04)

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```

The installer will ask for:

1. **SERVER_IP** — auto-detected (Enter to confirm)  
2. **Panel domain?** — `n` = IP only, or `y` + domain name  
3. **Email** — for Let's Encrypt SSL  

Temp git files under `/tmp` are **deleted automatically** after install.  
Live install path: `/opt/srv-panel` only.

Open: **http://YOUR.SERVER.IP/**

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash
```

## Service

```bash
systemctl status srv-panel
journalctl -u srv-panel -n 50
curl -s http://127.0.0.1:8000/api/health
```
