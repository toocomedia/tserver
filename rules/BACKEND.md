# Backend Rules

## Language & Framework
- Python 3.11+
- FastAPI for all HTTP endpoints
- Jinja2 for HTML template rendering
- SQLite via SQLAlchemy (async) for database
- Alembic for migrations

## File Structure Rules
- Max 200 lines per file. Split if exceeded.
- One file = one responsibility. No mixing of concerns.
- No business logic in route files.
- No raw SQL in route files.

## Folder Structure
```
backend/
  main.py              # App entry point only. No routes here.
  config.py            # Settings and environment variables
  database.py          # DB engine and session setup
  
  routers/             # One router file per feature
    domains.py
    dns.py
    ssl.py
    proxy.py
  
  services/            # Business logic. One file per feature.
    domain_service.py
    dns_service.py
    ssl_service.py
    proxy_service.py
    nginx_service.py   # Nginx config generation and reload
    powerdns_service.py
  
  models/              # SQLAlchemy ORM models
    domain.py
    dns_record.py
    proxy.py
  
  schemas/             # Pydantic request/response schemas
    domain.py
    dns.py
    ssl.py
    proxy.py
  
  templates/           # Jinja2 HTML templates
    layout.html        # Base layout
    partials/          # Reusable template parts
    pages/             # One file per page
  
  static/              # CSS, JS, icons
    css/
      main.css
    js/
      main.js
  
  utils/               # Shared helpers
    shell.py           # Safe shell command runner
    validators.py      # Input validation helpers
```

## Naming Rules
- Files: snake_case
- Classes: PascalCase
- Functions: snake_case
- Constants: UPPER_SNAKE_CASE
- Routes: kebab-case URLs (/domain-manager, /dns-records)

## Route Rules
- Routes only: validate input, call service, return response.
- No direct DB calls in routers.
- No Nginx/PowerDNS calls in routers.
- Always use response schemas (Pydantic models).

## Service Rules
- Services handle all business logic.
- Services call utils (shell, nginx, powerdns).
- Services raise HTTPException with clear messages.
- One function = one job. Keep functions under 40 lines.

## Shell Command Rules
- Never use os.system(). Use subprocess with timeout.
- Always run nginx -t before nginx reload.
- Capture stdout and stderr. Return both.
- Log every shell command that runs.

## Nginx Config Rules
- Generate one .conf file per domain.
- Never edit an existing config manually in code — always regenerate from template.
- Validate with nginx -t before enabling.
- Store generated config path in DB.
- Use 000-default.conf as drop-all default_server.

## Error Handling
- Always return structured JSON errors.
- Never expose raw exceptions to the frontend.
- Log full errors server-side.

## Security Rules
- Sanitize all domain name inputs.
- No user input goes directly into shell commands.
- No user input goes directly into nginx config templates without escaping.
- Panel routes are protected by session auth (`middleware/auth.py`). Public: `/login`, `/logout`, `/static/*`, `/api/health`.
- Admin user is seeded by `install.sh` or `scripts/create_admin.sh` (password hashed with bcrypt in SQLite).

## Comments
- Comment why, not what.
- Mark TODOs as: # TODO(v2): description
- No commented-out dead code.
