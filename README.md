# tserver

Lightweight VPS control panel — domains, DNS (PowerDNS), SSL (Certbot), reverse proxy.

## Install (Ubuntu 22.04 / 24.04)

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```

IP-only (recommended):

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh \
  | sudo SERVER_IP=YOUR.VPS.IP bash
```

With optional panel domain:

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh \
  | sudo SERVER_IP=YOUR.VPS.IP PANEL_DOMAIN=panel.example.com CERTBOT_EMAIL=you@example.com bash
```

Open: **http://YOUR.VPS.IP/**

## Update

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash
```

## Service

```bash
systemctl status srv-panel
journalctl -u srv-panel -n 50
```
