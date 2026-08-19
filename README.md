# Barq Panel

A fast, lightweight, and modern control panel for your VPS.

## 🚀 One-Click Install

Run these commands on a fresh supported amd64 VPS: Ubuntu 22.04/24.04/26.04 or Debian 12/13.

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh -o /tmp/tserver-get.sh
sudo bash /tmp/tserver-get.sh
rm -f /tmp/tserver-get.sh
```
*(This installs dependencies, PowerDNS, Nginx, and the panel service. It prompts for an admin password.)*

The piped form remains supported for interactive terminals:

```bash
curl -fsSL https://raw.githubusercontent.com/toocomedia/tserver/main/scripts/get.sh | sudo bash
```

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

**Password & 2FA Recovery (Secure CLI):**
For security, the panel does not expose a public "Forgot Password" form. If you get locked out, use the secure SSH commands locally on your server:

```bash
# Forcefully reset the admin password
sudo bash /opt/srv-panel/scripts/create_admin.sh --user admin --password "YourNewPassword" --force

# Disable 2FA if you lost access to your authenticator app
sudo bash /opt/srv-panel/scripts/create_admin.sh --user admin --disable-2fa

# Reset password AND disable 2FA at the same time
sudo bash /opt/srv-panel/scripts/create_admin.sh --user admin --password "YourNewPassword" --force --disable-2fa
```

---

## ⚡ Low-RAM Optimization & Worker Control

For 512 MB – 1 GB RAM servers, you can manage **Low-RAM Optimization Mode** and **Single Nginx Worker Mode** from the Web UI (Server Usage page) or CLI:

```bash
# Enable Low-RAM Optimization Mode
sudo bash /opt/srv-panel/scripts/optimize.sh enable

# Set Single Nginx Worker Mode (worker_processes 1)
sudo bash /opt/srv-panel/scripts/optimize.sh nginx-worker-1
```
*(For detailed technical architecture, see [docs/low_ram_optimization_mode.md](file:///c:/Users/riadh/Desktop/srv-t/docs/low_ram_optimization_mode.md).)*

---

## Security (lightweight)

Built into the app (works with or without nginx, IP or domain):

- **Login rate limit** — `slowapi` (default `5/minute` per IP; `LOGIN_RATE_LIMIT`)
- **Login lockout** — after 5 failures, 15 minutes (`LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS`)
- **CSRF** — required on POST forms and `fetch` (`X-CSRF-Token`)
- **Session cookie** — `SameSite=lax`; set `SESSION_HTTPS_ONLY=true` only when the panel is always HTTPS (leave false for plain `http://IP` login)

Limits are in-memory (per process) and reset on restart.

---

## Plugin & Dependency Development

- [Plugin development guide](docs/plugin_development_guide.md)
- [System dependency development guide](docs/dependency_development_guide.md)
- [Docker dependency operations](docs/docker_dependency_operations.md)
