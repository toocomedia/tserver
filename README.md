# SRV Panel — VPS Control Panel

Lightweight self-hosted VPS control panel. Manages domains, DNS, SSL certificates, and reverse proxies through a clean web UI.

---

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Web server | **Nginx** | Fastest, lightest, battle-tested |
| DNS | **PowerDNS** | Built-in REST API, programmatic control |
| SSL | **Certbot + Let's Encrypt** | Free, auto-renewal, nginx plugin |
| Backend | **FastAPI (Python)** | Async, clean API, easy shell integration |
| Templates | **Jinja2 + Vanilla JS** | No build step, no Node.js required |
| Database | **SQLite (SQLAlchemy async)** | Zero setup, perfect for v1 |

---

## Features (v1)

| Module | What it does |
|--------|-------------|
| **Domain Manager** | Add/delete domains. Auto-creates DNS zone, A record, webroot, nginx config, default HTML page |
| **DNS Manager** | View/add/delete DNS records per zone. Apply pre-built templates (Web, Email, Full) |
| **SSL Manager** | Issue, renew, revoke Let's Encrypt certs. Auto-updates nginx to HTTPS |
| **Reverse Proxy** | Point `sub.domain.com` → `ip:port`. Auto-creates DNS record + nginx config. Optional SSL |

---

## Smart Cascade Logic

Every action in the panel is connected. No isolated buttons.

### Create Domain
```
User submits "example.com"
    │
    ├─ 1. Validate domain name format
    ├─ 2. Check: not already in DB or nginx
    ├─ 3. PowerDNS: create zone for example.com
    ├─ 4. PowerDNS: add A record → server IP
    ├─ 5. Create /var/www/example.com/public/
    ├─ 6. Write default index.html to webroot
    ├─ 7. Generate nginx HTTP config (with acme-challenge block)
    ├─ 8. Run nginx -t → ROLLBACK all above if fails
    ├─ 9. nginx reload
    └─ 10. Save to DB
```

### Issue SSL on Domain
```
User clicks "Issue SSL" on example.com
    │
    ├─ 1. Check: nginx config exists (HTTP must be active)
    ├─ 2. Check: no cert already issued
    ├─ 3. Run certbot --nginx -d example.com
    │       certbot writes to /var/www/acme-challenge/
    │       Let's Encrypt fetches http://example.com/.well-known/acme-challenge/...
    ├─ 4. Certbot updates nginx config to HTTPS + redirect
    ├─ 5. Save cert path + expiry to DB
    └─ 6. "Issue SSL" button replaced with "SSL Active" badge
```

### Create Reverse Proxy
```
User fills: subdomain=app, domain=example.com, target=1.2.3.4:9000, SSL=yes
    │
    ├─ 1. Validate subdomain label, IP, port
    ├─ 2. Check: app.example.com not already in DB or nginx
    ├─ 3. PowerDNS: add A record app.example.com → THIS server IP
    │       (NOT the target IP — traffic comes here, nginx forwards it)
    ├─ 4. Generate nginx reverse proxy config for app.example.com
    ├─ 5. Run nginx -t → ROLLBACK DNS record if fails
    ├─ 6. nginx reload
    ├─ 7. If SSL enabled: run certbot for app.example.com
    ├─ 8. If SSL: update nginx config to HTTPS
    └─ 9. Save to DB
```

### Delete Domain
```
User clicks "Delete" on example.com
    │
    ├─ Guard: active reverse proxies? → BLOCK (remove them first)
    ├─ Guard: active SSL cert? → BLOCK (revoke first, or force)
    ├─ Remove nginx config + reload
    ├─ Remove /var/www/example.com/ webroot
    ├─ PowerDNS: delete zone (all records go with it)
    └─ Remove from DB
```

---

## Nginx Safety Architecture

The panel strictly prevents domain bleed-through:

```
000-default.conf   ← loads first (alphabetically)
                     catches ALL unmatched domains
                     returns 444 (Nginx drops connection silently)

panel.conf         ← only responds to PANEL_DOMAIN
example.com.conf   ← only responds to example.com, www.example.com
app.example.com.conf ← only responds to app.example.com
```

**Before every nginx reload:** `nginx -t` runs. If config is invalid, the bad config is removed and the previous state is restored. The panel never applies a config that breaks nginx.

**Before every domain/proxy creation:** The panel scans all existing nginx configs for duplicate `server_name` entries. If a conflict is found, the action is blocked.

---

## Folder Structure

```
srv-t/
│
├── rules/                        ← Project rules (read before coding)
│   ├── DESIGN.md                 ← UI design system (colors, spacing, components)
│   ├── BACKEND.md                ← Backend rules (file limits, structure, patterns)
│   └── FRONTEND.md               ← Frontend rules (CSS variables, JS modules, templates)
│
├── backend/
│   ├── main.py                   ← App entry point. Mounts routers. No routes here.
│   ├── config.py                 ← All settings from .env
│   ├── database.py               ← Async SQLAlchemy engine + session factory
│   │
│   ├── models/                   ← SQLAlchemy ORM models (DB schema)
│   │   ├── domain.py             ← Domain table
│   │   ├── dns_record.py         ← DNS record tracking table
│   │   ├── ssl_cert.py           ← SSL cert table
│   │   └── proxy.py              ← Reverse proxy table
│   │
│   ├── schemas/                  ← Pydantic validation (request/response shapes)
│   │   └── domain.py
│   │
│   ├── routers/                  ← HTTP routes (thin — calls services only)
│   │   ├── system.py             ← Dashboard + health check
│   │   └── domains.py            ← Domain CRUD
│   │
│   ├── services/                 ← Business logic (one file per feature)
│   │   ├── domain_service.py     ← Domain cascade: create, delete, page edit
│   │   ├── dns_service.py        ← DNS zone + record management
│   │   ├── nginx_service.py      ← Config write/remove, webroot, nginx -t
│   │   ├── ssl_service.py        ← Certbot: issue, renew, revoke       [Phase 4]
│   │   ├── proxy_service.py      ← Reverse proxy cascade               [Phase 5]
│   │   └── cascade_service.py    ← Multi-step orchestration            [Phase 5]
│   │
│   ├── utils/                    ← Shared low-level helpers
│   │   ├── shell.py              ← Safe async subprocess runner
│   │   ├── validators.py         ← Domain, IP, port input validators
│   │   ├── powerdns.py           ← PowerDNS REST API client
│   │   └── nginx_templates.py    ← Nginx config string generators
│   │
│   ├── templates/
│   │   ├── layout.html           ← Base HTML shell (sidebar + topbar + content)
│   │   ├── partials/
│   │   │   ├── sidebar.html
│   │   │   └── topbar.html
│   │   └── pages/
│   │       ├── dashboard.html
│   │       ├── domains/
│   │       │   ├── index.html    ← Domain list table
│   │       │   ├── create.html   ← Add domain form
│   │       │   └── detail.html   ← Domain detail + page editor
│   │       ├── dns/              [Phase 3]
│   │       ├── ssl/              [Phase 4]
│   │       └── proxy/            [Phase 5]
│   │
│   ├── static/
│   │   ├── css/
│   │   │   ├── main.css          ← Design tokens + reset + typography
│   │   │   ├── layout.css        ← Sidebar, topbar, content area
│   │   │   ├── components.css    ← Buttons, tables, badges, forms, modals
│   │   │   └── utils.css         ← Spacing/flex/display helpers
│   │   └── js/
│   │       ├── main.js           ← Global: fetch wrapper, toast, modal, confirm
│   │       └── modules/          ← Page-specific JS (one file per page)
│   │
│   └── requirements.txt
│
├── nginx-configs/                ← Reference configs (deployed by scripts)
│   ├── 000-default.conf          ← Drop-all default_server (return 444)
│   └── panel.conf                ← Panel UI nginx proxy config
│
├── scripts/                      ← Server setup scripts (run once on VPS)
│   ├── install.sh                ← Full install: Python, nginx, PowerDNS, certbot
│   ├── setup_powerdns.sh         ← PowerDNS SQLite + REST API + key generation
│   └── setup_nginx.sh            ← Nginx: remove default, add drop-all + panel config
│
├── .env.example                  ← Environment variable template
└── README.md                     ← This file
```

---

## Database Schema

### `domains`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT UNIQUE | e.g. `example.com` |
| server_ip | TEXT | IP at creation time |
| nginx_config_path | TEXT | `/etc/nginx/sites-available/example.com.conf` |
| webroot_path | TEXT | `/var/www/example.com/public` |
| dns_zone_created | BOOLEAN | |
| nginx_active | BOOLEAN | |
| created_at | DATETIME | |

### `dns_records`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| domain_id | FK → domains | |
| name | TEXT | record name |
| type | TEXT | A, CNAME, MX, TXT... |
| content | TEXT | |
| ttl | INTEGER | default 3600 |
| managed | BOOLEAN | `true` = panel-created |
| created_at | DATETIME | |

### `ssl_certs`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| domain_id | FK → domains | |
| full_domain | TEXT UNIQUE | e.g. `sub.example.com` |
| cert_path | TEXT | `/etc/letsencrypt/live/...` |
| expiry_date | DATETIME | |
| auto_renew | BOOLEAN | default `true` |
| issued_at | DATETIME | |

### `reverse_proxies`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| domain_id | FK → domains | parent domain |
| subdomain | TEXT | prefix only, e.g. `app` |
| full_domain | TEXT UNIQUE | e.g. `app.example.com` |
| target_ip | TEXT | |
| target_port | INTEGER | |
| protocol | TEXT | `http` or `https` |
| ssl_enabled | BOOLEAN | |
| ssl_cert_id | FK → ssl_certs | nullable |
| nginx_config_path | TEXT | |
| created_at | DATETIME | |

---

## DNS Templates

Available in `config.py → DNS_TEMPLATES`:

| Template | Records created |
|----------|----------------|
| `basic_web` | A `@` + CNAME `www` |
| `email_mx` | MX + SPF TXT |
| `full` | A + CNAME www + MX + SPF TXT + DMARC TXT |

---

## UX Rules

**Use dropdowns, not free text, wherever choices exist:**

| Field | Input type |
|-------|-----------|
| Domain selection | Dropdown of DB domains |
| DNS record type | Dropdown: A / AAAA / CNAME / MX / TXT / NS |
| Protocol for proxy | Dropdown: HTTP / HTTPS |
| SSL cert selection | Dropdown of issued certs |
| DNS template | Dropdown with Apply button |

**Automatic cascading — no manual steps:**
- Create domain → DNS + nginx + webroot all created automatically
- Create proxy → DNS record auto-added
- Enable SSL → nginx config auto-updated to HTTPS
- Delete anything → full cleanup cascade

**Orphan prevention:**
- SSL button disabled until nginx is active
- Proxy creation blocked if subdomain already in use
- Domain delete blocked if proxies or certs still exist

---

## Build Phases

| Phase | Status | Covers |
|-------|--------|--------|
| **Phase 1** | ✅ Done | Project skeleton, DB models, base UI shell, dashboard, install scripts |
| **Phase 2** | ✅ Done | Domain Manager: create/delete/detail, webroot, nginx, DNS cascade |
| **Phase 3** | ✅ Done | DNS Manager: view records, add/delete, apply templates |
| **Phase 4** | ✅ Done | SSL Manager: issue/renew/revoke certs, auto-update nginx |
| **Phase 5** | ✅ Done | Reverse Proxy Manager: create/delete, auto-DNS, optional SSL |
| **Phase 6** | ✅ Done | Admin Error Tracker: capture failures, detail view, copy report |
| **Phase 7** | ✅ Done | VPS install.sh + update.sh, sudoers, hardened PowerDNS/nginx setup |

---

## Server Setup (Fresh VPS)

Ubuntu **22.04 / 24.04**, run as root:

```bash
# 1. Clone the repo somewhere (not necessarily /opt)
git clone <your-repo-url> /root/srv-t
cd /root/srv-t

# 2. One-shot install (packages, PowerDNS, nginx, app, systemd)
#    SERVER_IP is required. PANEL_DOMAIN is optional — omit for IP-only.
sudo SERVER_IP=x.x.x.x \
     CERTBOT_EMAIL=you@example.com \
     bash scripts/install.sh

#    Optional domain later (also keeps IP access):
#    sudo SERVER_IP=x.x.x.x PANEL_DOMAIN=panel.example.com bash scripts/install.sh

# 3. Open the panel
#    http://x.x.x.x/                  ← works with IP only
#    http://panel.example.com/        ← if you set PANEL_DOMAIN + DNS
```

### Push this repo to GitHub (Windows)

Double-click **`push-to-github.bat`**, paste your repo URL when asked, done.

Install layout:

| Path | Purpose |
|------|---------|
| `/opt/srv-panel/app` | FastAPI app (from `backend/`) |
| `/opt/srv-panel/venv` | Python virtualenv |
| `/opt/srv-panel/.env` | Secrets (never overwritten by update) |
| `/opt/srv-panel/app/panel.db` | SQLite state |
| `/opt/srv-panel/scripts` | install/update helpers on the server |

### Updating after `git pull`

```bash
cd /root/srv-t && git pull
sudo bash scripts/update.sh
```

Preserves `.env`, `panel.db`, DNS zones, and Let's Encrypt certs.  
Details and flags: [`scripts/README.md`](scripts/README.md).

---

## Design System Reference

Full rules in [`rules/DESIGN.md`](rules/DESIGN.md).

Quick reference:
- **Colors:** `#FFFFFF` bg · `#0B0C0B` sidebar · `#C7F464` accent · `#DC2626` danger
- **Spacing:** 4 / 8 / 12 / 16 / 24 px
- **Sidebar:** 200px wide · dark bg · pistachio active state
- **Buttons:** 34px height · flat · no radius · no shadow
- **Font:** Inter · 13px body · 11px labels uppercase
- No rounded corners anywhere. No shadows. No decorative gradients.

---

## Code Rules

- **Max 200 lines per Python file** — split if exceeded
- **Max 150 lines per HTML template** — extract partials
- **Max 200 lines per JS file** — split into modules
- Routes → Services → Utils chain. No shortcuts.
- `nginx -t` before every nginx reload. Always.
- No `os.system()`. Use `utils/shell.py` only.
- No raw hex colors in CSS. Use CSS variables from `:root` only.
