# PostgreSQL Manager — Internal API Reference

**Plugin ID:** `postgres_manager`  
**Version:** 1.0.0 (Local mode)  
**Base prefix:** `/plugins/postgres_manager/api`

---

## Authentication

All endpoints require an active panel session cookie (same as the UI).  
No additional API key is needed — the panel's `AuthMiddleware` protects every route.

---

## Option A — Direct Python Import (recommended for server-side plugins)

The cleanest way for another plugin to consume this plugin's logic is a direct import.
No HTTP overhead, no network call.

```python
from plugins.postgres_manager.service import postgres_service
from plugins.postgres_manager import queries as pg_queries

# Check if PostgreSQL is running
status = postgres_service.get_status()
# → {"installed": True, "running": True, "version": "...", "ram_mb": 34.2, ...}

# List databases
dbs = pg_queries.list_databases()
# → [{"name": "mydb", "owner": "postgres", "encoding": "UTF8", "size": "8192 bytes"}]

# Create a database
pg_queries.create_database("newdb", owner="myuser")

# Run a SELECT query
rows = pg_queries.run_query("mydb", "SELECT id, name FROM users LIMIT 10;")
# → [{"row": "1|alice"}, {"row": "2|bob"}]
```

> **Guard before calling:** Always check `status["running"]` before making CRUD calls.
> If the plugin itself is disabled, the import will raise `PluginUnavailableError`.

---

## Option B — HTTP API (recommended for cross-language or future remote plugins)

### GET `/api/status`

Returns the current service status (cached ≤30 s).

```json
{
  "installed": true,
  "running": true,
  "version": "psql (PostgreSQL) 15.6",
  "pid": 1234,
  "ram_mb": 34.2,
  "port_open": true,
  "mode": "local"
}
```

---

### GET `/api/databases`

Returns all user databases.

```json
[
  {"name": "mydb", "owner": "postgres", "encoding": "UTF8", "size": "8192 bytes"},
  {"name": "appdb", "owner": "appuser", "encoding": "UTF8", "size": "1488 kB"}
]
```

---

### POST `/api/databases`

Create a new database.

**Request body (JSON):**
```json
{"name": "newdb", "owner": "postgres"}
```

**Response:**
```json
{"status": "ok", "name": "newdb"}
```

**Errors:** `400` if name contains invalid characters or database already exists.

---

### DELETE `/api/databases/{name}`

Drop a database. Fails with `400` if active connections exist.

```json
{"status": "ok", "name": "newdb"}
```

---

### GET `/api/databases/{name}/tables`

List user tables in a database.

```json
[
  {"name": "users",    "size": "72 kB", "row_count": "1420"},
  {"name": "sessions", "size": "24 kB", "row_count": "302"}
]
```

---

### GET `/api/users`

List all PostgreSQL roles.

```json
[
  {"name": "postgres",  "superuser": true,  "can_login": true},
  {"name": "appuser",   "superuser": false, "can_login": true}
]
```

---

### POST `/api/users`

Create a login role.

**Request body (JSON):**
```json
{"name": "newuser", "password": "s3curePass!"}
```

**Errors:** `400` if name is invalid or password is shorter than 8 characters.

---

### DELETE `/api/users/{name}`

Drop a role.

---

### POST `/api/users/{name}/password`

Change a user's password.

**Request body (JSON):**
```json
{"password": "newSecurePass!"}
```

---

### POST `/api/query`

Run a read-only SELECT query.

**Request body (JSON):**
```json
{
  "db": "mydb",
  "sql": "SELECT id, email FROM users WHERE active = true LIMIT 20;"
}
```

**Response:**
```json
{
  "rows": [
    {"row": "1|alice@example.com"},
    {"row": "2|bob@example.com"}
  ],
  "count": 2
}
```

> **Note:** Rows are returned as pipe-delimited strings (psql `-A -t` format).
> Parse with `row.split("|")`. Column order matches the SELECT column list.

**Errors:**
- `400` — Non-SELECT statement submitted
- `400` — Query longer than 4 000 characters
- `500` — psql execution error or timeout

---

### POST `/api/service/{action}`

Control the PostgreSQL service. `action` must be `start`, `stop`, or `restart`.

```json
{"status": "ok", "action": "restart"}
```

---

## Error Format (all endpoints)

```json
{"detail": "Human-readable error message."}
```

HTTP status codes: `400` validation error · `500` server/psql error · `503` plugin disabled.

---

## Version Notes

- **v1.0** — Local mode only. `mode` field is always `"local"`.
- **v2.0** — Will add remote connection profiles. `mode` will be `"local"` or `"remote"`.
  See `PHASE2_REMOTE.md` for the full design.

---

## Identifier Rules

Database names and usernames must match: `^[a-zA-Z0-9_\-]{1,63}$`

Invalid names are rejected at both the Pydantic schema layer and the `queries.py` guard layer.
