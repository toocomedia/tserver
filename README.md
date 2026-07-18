# tserver

Lightweight VPS control panel — domains, DNS (PowerDNS), SSL (Certbot), reverse proxy.

## Install (Ubuntu 22.04 / 24.04)

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```

IP and panel URL are set automatically. When it finishes, open:

```text
http://<your-server-public-ip>/
```

(Optional) force values if auto-detect is wrong:

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh \
  | sudo SERVER_IP=1.2.3.4 PANEL_DOMAIN=panel.example.com bash
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
