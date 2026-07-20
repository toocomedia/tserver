# VPS Control Panel — Implementation Plan

## Overview

A lightweight self-hosted VPS control panel built on:
- **Nginx** — web server, reverse proxy, static site hosting
- **PowerDNS** — DNS server with REST API
- **Certbot + Let's Encrypt** — SSL certificate issuance and renewal
- **FastAPI (Python)** — backend API + template rendering
- **Jinja2 + Vanilla JS** — frontend UI
- **SQLite + SQLAlchemy** — panel state database

The panel manages 4 modules: Domain Manager, DNS Manager, SSL Manager, Reverse Proxy Manager.
All modules are **interconnected** — actions in one automatically trigger related actions in others.

---

## UX Logic Rules (Smart Connected UI)

> [!IMPORTANT]
> These rules govern how forms, inputs, and actions interact. No action is isolated.

### Rule 1 — Use selects/lists instead of free text wherever possible
| Situation | Use |
|-----------|-----|
| Choosing a domain | Dropdown of existing domains from DB |
| Choosing a record type | Dropdown: A, AAAA, CNAME, MX, TXT |
| Choosing an SSL cert | Dropdown of issued certs |
| Choosing a proxy protocol | Dropdown: HTTP, HTTPS |
| Entering IP | Free text only when no alternative |
| Entering port | Number input with validation |
| Entering domain name | Free text only on domain creation |

### Rule 2 — Automatic cascading actions
| Trigger | Auto Action |
|---------|-------------|
| Create domain | Auto-create DNS zone in PowerDNS + A record pointing to server IP |
| Create reverse proxy | Auto-create DNS A/CNAME record for that subdomain |
| Enable SSL on domain | Auto-configure Nginx SSL block + run Certbot |
| Enable SSL on reverse proxy | Auto-configure Nginx SSL + run Certbot for that subdomain |
| Delete domain | Warn: removes DNS zone + Nginx config + SSL cert |
| Delete reverse proxy | Auto-remove DNS record + Nginx config block |

### Rule 3 — No orphan actions
- You cannot enable SSL on a domain that has no Nginx config yet.
- You cannot create a reverse proxy for a subdomain that has no DNS record pointing here.
- The panel enforces this order: **DNS → Nginx config → SSL**.

### Rule 4 — Status is always visible
- Every domain, DNS record, SSL cert, and reverse proxy shows a live status badge.
- Status is fetched from the real system (nginx -T, certbot certificates, PowerDNS API) not from DB alone.

---

## Project Folder Structure

```
srv-t/
  rules/
    DESIGN.md
    BACKEND.md
    FRONTEND.md

  backend/
    main.py                    # App entry, mounts routers, serves templates
    config.py                  # Settings: server IP, PowerDNS API key, paths
    database.py                # SQLAlchemy async engine + session
    
    routers/
      domains.py               # Domain CRUD routes
      dns.py                   # DNS record routes
      ssl.py                   # SSL issue/renew/revoke routes
      proxy.py                 # Reverse proxy routes
      system.py                # Server status, nginx reload, health check
    
    services/
      domain_service.py        # Domain logic: create zone, nginx config, link records
      dns_service.py           # PowerDNS API calls: zones, records, templates
      ssl_service.py           # Certbot CLI calls: issue, renew, revoke, list
      proxy_service.py         # Reverse proxy logic: validate, auto-dns, nginx config
      nginx_service.py         # Generate/delete/reload nginx configs
      cascade_service.py       # Orchestrates multi-step cascading actions
    
    models/
      domain.py                # Domain ORM model
      dns_record.py            # DNS record ORM model
      ssl_cert.py              # SSL cert ORM model
      proxy.py                 # Reverse proxy ORM model
    
    schemas/
      domain.py                # Pydantic: DomainCreate, DomainResponse
      dns.py                   # Pydantic: RecordCreate, RecordResponse
      ssl.py                   # Pydantic: CertRequest, CertResponse
      proxy.py                 # Pydantic: ProxyCreate, ProxyResponse
    
    templates/
      layout.html              # Base shell: sidebar + topbar + content block
      partials/
        sidebar.html
        topbar.html
        table.html
        status_badge.html
        modal.html
        alert.html
        confirm_dialog.html    # Reusable delete confirmation modal
      pages/
        dashboard.html
        domains/
          index.html           # Domain list table
          create.html          # Add domain form
          detail.html          # Domain detail: info + quick actions
        dns/
          index.html           # DNS zones list
          records.html         # Records table for a zone
          create_record.html   # Add record form (type dropdown, not free text)
        ssl/
          index.html           # All certs list + status
          issue.html           # Issue SSL form (domain dropdown)
        proxy/
          index.html           # All reverse proxies list
          create.html          # Create reverse proxy (domain/subdomain dropdowns)
    
    static/
      css/
        main.css               # :root tokens, reset, base
        layout.css             # Sidebar, topbar, content area
        components.css         # Buttons, tables, badges, forms, modals
        utils.css              # Spacing helpers
      js/
        main.js                # Global: fetch wrapper, toast, modal init
        modules/
          modal.js             # Open/close modal logic
          toast.js             # Notification toasts
          confirm.js           # Confirm dialog logic
          domains.js           # Domain page interactions
          dns.js               # DNS page interactions
          ssl.js               # SSL page interactions
          proxy.js             # Proxy page interactions + auto-DNS UI feedback
    
    utils/
      shell.py                 # subprocess runner with timeout + logging
      validators.py            # Domain name, IP, port validation
      nginx_templates.py       # Nginx config string generators
      powerdns.py              # PowerDNS REST API client
    
    migrations/                # Alembic migration files
    
  nginx-configs/               # Panel-generated nginx site configs (managed by panel)
    000-default.conf           # Drop-all default_server (444)
    panel.conf                 # Panel UI itself

  scripts/
    install.sh                 # Server setup script (installs deps, configures PowerDNS)
    setup_powerdns.sh          # PowerDNS initial config + API enable
    setup_nginx.sh             # Nginx initial config + default drop block
```

---

## Phase 1 — Foundation (Server Setup + Project Skeleton)

**Goal**: Working FastAPI app, DB, PowerDNS running, Nginx default-drop in place.

### 1.1 — Server prerequisites script (`scripts/install.sh`)
- Install: Python 3.11+, pip, nginx, powerdns, powerdns-backend-sqlite3, certbot, python3-certbot-nginx
- Enable and start: nginx, pdns

### 1.2 — PowerDNS initial config (`scripts/setup_powerdns.sh`)
- Enable SQLite3 backend
- Enable REST API on `127.0.0.1:8081`
- Set API key (stored in `config.py`)
- Create initial DB schema

### 1.3 — Nginx initial config (`scripts/setup_nginx.sh`)
- Remove default nginx site
- Place `000-default.conf` (drop-all `default_server` returning 444)
- Place `panel.conf` serving the FastAPI app (via uvicorn on `127.0.0.1:8000`)
- Generate a self-signed dummy cert for the default_server block

### 1.4 — FastAPI skeleton
- `main.py`: app init, mount all routers, static files, templates
- `config.py`: reads env vars (SERVER_IP, PDNS_API_KEY, PDNS_URL, NGINX_SITES_PATH, etc.)
- `database.py`: SQLAlchemy async engine pointing to `panel.db`
- All 4 models created + DB tables initialized on startup
- Health check route: `GET /api/health` returns nginx status + powerdns status

### 1.5 — Base UI shell
- `layout.html`: full HTML shell with sidebar, topbar, content block
- `sidebar.html`: nav items (Dashboard, Domains, DNS, SSL, Proxy)
- `topbar.html`: page title + action slot
- `main.css` + `layout.css` with all DESIGN.md tokens
- Dashboard page: server info (IP, hostname, nginx status, cert count, domain count)

**Deliverable**: Panel loads at `http://panel-ip` showing dashboard.

---

## Phase 2 — Domain Manager

**Goal**: Add, view, delete domains. Auto-creates DNS zone + Nginx static config on creation.

### 2.1 — Backend
**`services/domain_service.py`**
- `create_domain(name)`:
  1. Validate domain name format
  2. Check: domain not already in DB
  3. Check: no duplicate `server_name` in nginx configs
  4. Call `dns_service.create_zone(name)` → PowerDNS creates zone
  5. Call `dns_service.add_record(name, "A", server_ip)` → points domain to server
  6. Call `nginx_service.create_static_site(name)` → generates nginx config with default static HTML
  7. Run `nginx -t` → if fails, rollback all above steps
  8. Run `nginx -s reload`
  9. Save to DB

- `delete_domain(id)`:
  1. Check: no active SSL cert (warn user, require force flag)
  2. Check: no active reverse proxy using this domain
  3. Remove nginx config
  4. Remove DNS zone from PowerDNS
  5. Delete from DB

**`services/nginx_service.py`**
- `create_static_site(domain)` → generates `/nginx-configs/domain.conf` with:
  - HTTP block serving a default `index.html` (required for certbot HTTP-01 challenge)
  - Clean `default_server` protection (exact `server_name` only)

**`utils/nginx_templates.py`**
- `static_site_config(domain, webroot)` → returns nginx config string

### 2.2 — Routes (`routers/domains.py`)
- `GET /domains` → list page
- `GET /domains/create` → create form page
- `POST /domains` → create domain (calls `domain_service.create_domain`)
- `GET /domains/{id}` → detail page
- `POST /domains/{id}/delete` → delete with confirmation

### 2.3 — UI
**`pages/domains/index.html`**
- Table: Domain name | DNS Status | Nginx Status | SSL Status | Actions
- Status badges pulled from real system on page load (JS fetch)
- "Add Domain" button → goes to create page

**`pages/domains/create.html`**
- Single input: domain name (free text, validated)
- Info text: "This will auto-create a DNS zone and point it to this server"
- Submit button: "Create Domain"
- On success: redirect to domain detail

**`pages/domains/detail.html`**
- Domain info row: name, server IP, created date
- Quick action buttons (inline row): Issue SSL | View DNS Records | Delete
- Status panel: DNS zone status | Nginx config status | SSL status
- No orphan actions: "Issue SSL" button disabled until nginx config confirmed active

**Deliverable**: Can add a domain → DNS zone appears in PowerDNS → Nginx serves default page.

---

## Phase 3 — DNS Manager

**Goal**: View and manage DNS records per zone. Provide templates for common setups.

### 3.1 — DNS Templates
Pre-built templates stored in `config.py` (or a `dns_templates.json` file):
- **Basic Web**: A record + www CNAME
- **Email (MX)**: MX record + SPF TXT record
- **Full**: A + www CNAME + MX + SPF + DMARC TXT
- **Subdomain**: A or CNAME pointing to server IP

### 3.2 — Backend
**`services/dns_service.py`**
- `create_zone(domain)` → POST to PowerDNS API
- `delete_zone(domain)` → DELETE to PowerDNS API
- `list_records(domain)` → GET zone records from PowerDNS API
- `add_record(domain, name, type, content, ttl)` → PATCH to PowerDNS API
- `delete_record(domain, name, type)` → PATCH to PowerDNS API
- `apply_template(domain, template_name)` → calls `add_record` multiple times

**Routes (`routers/dns.py`)**
- `GET /dns` → all zones list
- `GET /dns/{domain}/records` → records table for zone
- `POST /dns/{domain}/records` → add record
- `POST /dns/{domain}/records/template` → apply template
- `POST /dns/{domain}/records/{record_id}/delete` → delete record

### 3.3 — UI
**`pages/dns/index.html`**
- Table: Zone name | Record count | Status | Actions (View Records | Delete Zone)

**`pages/dns/records.html`**
- Toolbar: "Add Record" button | "Apply Template" dropdown (select + apply button)
- Table: Name | Type | Content | TTL | Actions (Delete)
- Add record form (inline or modal):
  - **Type**: dropdown (A, AAAA, CNAME, MX, TXT, NS) — not free text
  - **Name**: text input
  - **Content**: text input (label changes based on type: "IP Address" for A, "Target" for CNAME)
  - **TTL**: number input, default 3600

**Deliverable**: Can view/add/delete DNS records. Templates apply multiple records in one click.

---

## Phase 4 — SSL Manager

**Goal**: Issue, renew, revoke SSL certs for domains and subdomains. Auto-update Nginx.

### 4.1 — Backend
**`services/ssl_service.py`**
- `issue_cert(domain, include_www)`:
  1. Check: domain has nginx config (HTTP must be working for HTTP-01 challenge)
  2. Run: `certbot --nginx -d domain.com [-d www.domain.com] --non-interactive --agree-tos -m admin@domain.com`
  3. Capture output. If success: update DB record (cert path, expiry date)
  4. Nginx auto-updated by certbot `--nginx` plugin

- `renew_cert(domain)`:
  - Run: `certbot renew --cert-name domain.com --non-interactive`

- `revoke_cert(domain)`:
  - Run: `certbot revoke --cert-name domain.com --non-interactive`
  - Remove SSL directives from nginx config, regenerate HTTP-only config
  - Reload nginx

- `list_certs()`:
  - Run: `certbot certificates` and parse output
  - Returns: domain, expiry date, cert path, status

**Routes (`routers/ssl.py`)**
- `GET /ssl` → certs list page
- `GET /ssl/issue` → issue form (domain dropdown)
- `POST /ssl/issue` → run certbot
- `POST /ssl/{cert_name}/renew` → renew specific cert
- `POST /ssl/{cert_name}/revoke` → revoke with confirmation

### 4.2 — UI
**`pages/ssl/index.html`**
- Table: Domain | Expiry Date | Days Left | Status | Actions (Renew | Revoke)
- Status badge: green if >30 days, yellow if <30, red if expired
- "Issue SSL" button in topbar

**`pages/ssl/issue.html`**
- **Domain**: dropdown — lists all domains from DB that have nginx config + no active cert
- **Include www**: checkbox (auto-adds `www.domain.com` to cert)
- Info block: explains HTTP-01 challenge requirement
- Submit: "Issue Certificate" (primary button)
- Real-time output: shows certbot log lines as they stream (SSE or polling)

**Deliverable**: Click "Issue SSL" on a domain → cert issued → Nginx auto-configured for HTTPS.

---

## Phase 5 — Reverse Proxy Manager

**Goal**: Point `subdomain.domain.com` to `external-ip:port` with optional SSL. Auto-creates DNS record. Never conflicts with existing sites.

### 5.1 — Backend
**`services/proxy_service.py`**
- `create_proxy(subdomain, domain, target_ip, target_port, protocol, enable_ssl)`:
  1. Validate: `subdomain.domain` is not already used (check DB + nginx configs)
  2. Validate: `target_ip:target_port` reachable (optional ping check)
  3. Check: parent `domain` exists in DB (domain must be managed by panel)
  4. Call `dns_service.add_record(domain, subdomain, "A", server_ip)` — points subdomain DNS to THIS server (not to target IP — traffic comes in, nginx forwards it)
  5. Call `nginx_service.create_proxy_config(subdomain, domain, target_ip, target_port, protocol)`
  6. Run `nginx -t` → rollback DNS record if fails
  7. Reload nginx
  8. If `enable_ssl`: call `ssl_service.issue_cert(f"{subdomain}.{domain}")`
  9. Save to DB

- `delete_proxy(id)`:
  1. Remove nginx config
  2. Remove DNS record for subdomain
  3. Revoke SSL cert if exists
  4. Reload nginx
  5. Delete from DB

**`utils/nginx_templates.py`** — add:
- `proxy_config(subdomain, domain, target_ip, target_port, protocol)` → returns nginx reverse proxy config string

**Routes (`routers/proxy.py`)**
- `GET /proxy` → proxy list
- `GET /proxy/create` → create form
- `POST /proxy` → create proxy (full cascade)
- `POST /proxy/{id}/delete` → delete with confirmation

### 5.2 — UI
**`pages/proxy/index.html`**
- Table: Subdomain | Target | Protocol | SSL | DNS Status | Actions
- Active proxy row: border-left 4px accent (per DESIGN.md active row rule)

**`pages/proxy/create.html`**
- **Domain**: dropdown — lists managed domains from DB
- **Subdomain**: text input (only the prefix, e.g. "app" — label shows "app.[selected-domain]")
- **Target IP**: text input
- **Target Port**: number input (1–65535)
- **Protocol**: dropdown — HTTP / HTTPS
- **Enable SSL**: toggle checkbox — if checked, shows info: "Will issue cert for subdomain.domain after proxy is created"
- Preview line (live, JS-driven): shows final result as user fills form:
  `https://app.example.com → http://1.2.3.4:9000`
- Submit: "Create Reverse Proxy"

**JS logic in `proxy.js`**:
- Domain dropdown change → update live preview
- Subdomain input change → update live preview
- SSL toggle → show/hide SSL info block
- On submit: disable button, show "Creating..." state

**Deliverable**: Fill form → proxy created → DNS record auto-added → Nginx configured → SSL optionally issued. All in one action.

---

## Phase 6 — Admin Error Tracker

**Goal**: Capture every panel failure with full detail. Admin UI to review, copy reports for debugging, resolve, and clear noise.

### 6.1 — Backend
**`models/error_event.py`** — table `error_events`:
level, source, operation, message, detail, traceback, request_*, context_json, resolved

**`services/error_service.py`**
- `record(...)` — always commits on a dedicated session (survives action rollback); never raises
- `list_errors` / `get` / `mark_resolved` / `delete` / `clear_resolved` / `clear_all`
- `format_report(event)` — plain-text clipboard report

**`middleware/error_capture.py`**
- Request ID middleware (`X-Request-ID`)
- Exception handlers: unhandled Exception always; HTTP 5xx always; POST 4xx on managed modules

**Service enrichment** (rich context):
- `domain_service` create failure
- `cascade_service` proxy create failure
- `ssl_service` certbot / nginx SSL failure

### 6.2 — Routes (`routers/errors.py`, prefix `/admin/errors`)
- `GET /admin/errors/` — list + filters (status, source, search)
- `GET /admin/errors/{id}` — detail + copy report
- `GET /admin/errors/{id}/report.txt` — download
- `POST .../resolve` | `reopen` | `delete`
- `POST .../clear-resolved` | `clear-all`

### 6.3 — UI
**Sidebar** — Admin → Errors  
**List** — When | Level | Source | Operation | Message | Status | View  
**Detail** — meta + detail/context/traceback `<pre>` + **Copy report** + resolve/delete  
**Dashboard** — open error count card  

**Deliverable**: Fail a domain/proxy/SSL action → row appears under Admin → Errors → Copy report works.

---

## Phase 7 — VPS Install & Update Scripts

**Goal**: One-command install and safe updates on Ubuntu VPS without wiping DB, `.env`, zones, or certs.

### 7.1 — `scripts/install.sh`
- Root-only bootstrap: apt → user `panel` → venv → rsync `backend/` → `/opt/srv-panel/app`
- Prompts or env: `SERVER_IP`, `PANEL_DOMAIN`, `CERTBOT_EMAIL`
- Creates/merges `.env` (does not wipe existing `PDNS_API_KEY`)
- Calls hardened `setup_powerdns.sh` + `setup_nginx.sh`
- Installs `/etc/sudoers.d/srv-panel` for nginx/certbot/file helpers
- systemd `srv-panel.service` + health check on `:8000/api/health`

### 7.2 — `scripts/update.sh`
- Backup `panel.db` + `.env` under `/opt/srv-panel/backups/`
- rsync app excluding DB; pip install requirements; restart service
- Flags: `--no-pip`, `--restart-only`, `--refresh-panel-nginx`

### 7.3 — Setup hardening
- **PowerDNS**: reuse API key + existing SQLite zones; API self-check
- **Nginx**: ACME webroot `/var/www/acme-challenge`, panel proxy headers, drop-all default

### 7.4 — Runtime privileges
- `PRIVILEGED_SUDO=true` in `.env`
- `utils/shell.py` prefixes privileged cmds with `sudo -n` when not root
- `nginx_service` writes configs via `shell.write_file` / symlink / remove

**Deliverable**: Fresh VPS + `install.sh` → panel up; `update.sh` after pull → new code, same data.

---

## Cascade Service (Cross-Module Orchestration)

**`services/cascade_service.py`** — max 200 lines, split if needed

This service handles multi-step operations atomically:

```python
# Example: create_reverse_proxy_full()
async def create_reverse_proxy_full(data):
    steps_done = []
    try:
        await dns_service.add_record(...)   ; steps_done.append("dns")
        await nginx_service.create_proxy(...); steps_done.append("nginx")
        await nginx_service.test_config()    # raises on failure
        await nginx_service.reload()
        if data.enable_ssl:
            await ssl_service.issue_cert(...); steps_done.append("ssl")
        await db.save_proxy(...)
    except Exception as e:
        await rollback(steps_done, data)     # undo in reverse order
        raise
```

Rollback map:
- `"dns"` → delete DNS record
- `"nginx"` → delete nginx config file
- `"ssl"` → revoke cert

---

## Database Schema

### `domains`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| name | TEXT UNIQUE | e.g. example.com |
| server_ip | TEXT | server IP at time of creation |
| nginx_config_path | TEXT | path to generated .conf |
| dns_zone_created | BOOLEAN | |
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
| managed | BOOLEAN | true = panel-managed, false = manual |

### `ssl_certs`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| domain_id | FK → domains | |
| full_domain | TEXT | e.g. sub.example.com |
| cert_path | TEXT | /etc/letsencrypt/live/... |
| expiry_date | DATETIME | |
| auto_renew | BOOLEAN | default true |
| issued_at | DATETIME | |

### `reverse_proxies`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| domain_id | FK → domains | parent domain |
| subdomain | TEXT | prefix only |
| full_domain | TEXT | computed: sub.domain.com |
| target_ip | TEXT | |
| target_port | INTEGER | |
| protocol | TEXT | http/https |
| ssl_enabled | BOOLEAN | |
| ssl_cert_id | FK → ssl_certs | nullable |
| nginx_config_path | TEXT | |
| created_at | DATETIME | |

### `error_events`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| created_at | DATETIME | |
| level | TEXT | error / warning / critical |
| source | TEXT | domain, dns, ssl, proxy, nginx, powerdns, system, http |
| operation | TEXT | e.g. create_proxy, issue_cert |
| message | TEXT | short summary |
| detail | TEXT | stderr / API body |
| traceback | TEXT | nullable |
| request_method | TEXT | nullable |
| request_path | TEXT | nullable |
| request_id | TEXT | nullable |
| context_json | TEXT | nullable JSON bag |
| resolved | BOOLEAN | default false |
| resolved_at | DATETIME | nullable |

---

## Verification Plan (No Test Runs — Real Server Only)

> [!IMPORTANT]
> All verification is done live on a real server. No mocking, no unit test environments.

| Phase | Verification |
|-------|-------------|
| Phase 1 | Panel loads. Dashboard shows real nginx/powerdns status. `GET /api/health` returns 200. |
| Phase 2 | Add domain → PowerDNS zone appears via API check. Nginx config file created. `curl http://domain.com` returns default page. |
| Phase 3 | Add A record → dig confirms DNS resolves. Apply template → multiple records appear. |
| Phase 4 | Issue SSL → `certbot certificates` shows cert. `curl https://domain.com` works. |
| Phase 5 | Create proxy → DNS record added. `curl https://sub.domain.com` reaches `target_ip:port`. Delete proxy → DNS removed, nginx config gone. |
| Phase 6 | Force a failed action → row under Admin → Errors. Copy report has detail/context. Mark resolved / clear works. |
| Phase 7 | Fresh VPS `install.sh` → health OK. `update.sh` preserves DB/zones. Re-run PowerDNS setup keeps API key. |

---

## Build Order (Sequential, No Skipping)

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
```

Each phase is fully functional before the next starts.
No placeholder data. No mocked services. Every save hits the real system.
