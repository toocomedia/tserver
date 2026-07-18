# tserver

Lightweight VPS control panel — domains, DNS (PowerDNS), SSL (Certbot), reverse proxy.

## Install (Ubuntu 22.04 / 24.04)

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```

The installer **asks you**:

1. **SERVER_IP** — auto-detected, press Enter to confirm  
2. **Panel domain?** — `n` = IP only (`http://IP/`), or `y` then type e.g. `panel.example.com`  
3. **Email** — required for Let's Encrypt SSL later  

Fully automatic (no questions):

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh \
  | sudo NONINTERACTIVE=1 bash
```

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash
```

## Service

```bash
systemctl status srv-panel
journalctl -u srv-panel -n 50
```
