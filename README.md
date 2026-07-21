# SRV-Panel

A simple, lightweight control panel for your VPS.

## 🚀 One-Click Install

Run this command as `root` on a fresh Ubuntu 22.04 or 24.04 server to automatically install the panel:

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```
*(This will install dependencies, setup PowerDNS, Nginx, and the Panel service. It will prompt you for an admin password during installation.)*

---

## 🔄 One-Click Update

To update your panel to the latest version from GitHub (this will safely keep your database, SSL, and configurations intact and restart the service):

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get-update.sh | sudo bash
```

---

## ⚙️ Common Actions

If you ever need to manually restart the panel or check its status, you can use standard systemctl commands:

**Restart the Panel:**
```bash
sudo systemctl restart srv-panel
```

**Check Panel Status:**
```bash
sudo systemctl status srv-panel
```

**View Live Logs:**
```bash
sudo journalctl -u srv-panel -f
```

**Reset Admin Password:**
If you get locked out, you can run the admin creation script locally on your server:
```bash
sudo bash /opt/srv-panel/scripts/create_admin.sh --user admin --force
```

---

## Security (lightweight)

Built into the app (works with or without nginx, IP or domain):

- **Login rate limit** — `slowapi` (default `5/minute` per IP; `LOGIN_RATE_LIMIT`)
- **Login lockout** — after 5 failures, 15 minutes (`LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS`)
- **CSRF** — required on POST forms and `fetch` (`X-CSRF-Token`)
- **Session cookie** — `SameSite=lax`; set `SESSION_HTTPS_ONLY=true` only when the panel is always HTTPS (leave false for plain `http://IP` login)

Limits are in-memory (per process) and reset on restart.
