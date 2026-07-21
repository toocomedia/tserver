# VPS scripts

## install.sh

First-time bootstrap on Ubuntu 22.04/24.04 (run as root).

```bash
git clone <repo> /root/srv-t
cd /root/srv-t
sudo SERVER_IP=x.x.x.x PANEL_DOMAIN=panel.example.com CERTBOT_EMAIL=you@example.com \
  bash scripts/install.sh
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `SOURCE_DIR` | parent of `scripts/` | Repo root |
| `PANEL_DIR` | `/opt/srv-panel` | Install root |
| `SERVER_IP` | auto-detect | **Required** public IP (panel always works at `http://IP/`) |
| `PANEL_DOMAIN` | same as IP | Optional hostname; leave blank for **IP-only** |
| `CERTBOT_EMAIL` | `admin@localhost` | Let's Encrypt email (for SSL later) |
| `ADMIN_USER` | `admin` | Panel web login username |
| `ADMIN_PASSWORD` | (prompt) | Required when `NONINTERACTIVE=1` (min 8 chars) |
| `SKIP_APT=1` | off | Skip package install |
| `SKIP_UFW=1` | off | Skip UFW rules |
| `DO_UPGRADE=1` | off | Run `apt upgrade` |
| `NONINTERACTIVE=1` | off | No prompts |

Creates: venv, app, `.env` (incl. `SECRET_KEY`), PowerDNS, nginx panel site, sudoers, systemd `srv-panel`, panel admin user.

## update.sh

Deploy new code; **keeps** `.env`, `panel.db`, DNS zones, certs.

```bash
cd /root/srv-t && git pull
sudo bash scripts/update.sh
# or from installed scripts:
sudo SOURCE_DIR=/root/srv-t bash /opt/srv-panel/scripts/update.sh
```

| Flag | Meaning |
|------|---------|
| `--no-pip` | Skip `pip install` |
| `--restart-only` | Only restart service |
| `--refresh-panel-nginx` | Re-run `setup_nginx.sh` (panel site only) |

Backups land in `/opt/srv-panel/backups/`.

If the install has **no panel admin** yet (auth added after first install), update prints:

```bash
sudo bash /opt/srv-panel/scripts/create_admin.sh
```

## create_admin.sh

Root-only helper to create or reset the **web** admin (not the OS `panel` service user).

```bash
sudo bash /opt/srv-panel/scripts/create_admin.sh
sudo bash /opt/srv-panel/scripts/create_admin.sh --user admin --password '...' --force
sudo bash /opt/srv-panel/scripts/create_admin.sh --check   # exit 0 if any user exists
```

| Flag | Meaning |
|------|---------|
| `--user`, `-u` | Username (default `admin`) |
| `--password`, `-p` | Password (prompt if omitted) |
| `--force`, `-f` | Reset password if user exists |
| `--check` | Only report whether users exist |

## setup_powerdns.sh / setup_nginx.sh

Called by `install.sh`. Safe to re-run:

- PowerDNS **reuses** existing API key and zone DB
- Nginx rewrites only `000-default` + `panel` (not domain configs)

## Troubleshooting

```bash
systemctl status srv-panel
journalctl -u srv-panel -n 80 --no-pager
curl -s http://127.0.0.1:8000/api/health
nginx -t
sudo -u panel sudo -n nginx -t   # sudoers check
```
