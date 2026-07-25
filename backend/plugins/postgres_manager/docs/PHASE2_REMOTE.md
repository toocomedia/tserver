# Phase 2: Remote / SSH PostgreSQL Support

**Target version:** 2.0.0  
**Status:** Design complete — not yet implemented.

---

## What Phase 2 adds

The plugin will support connecting to a PostgreSQL instance running on a **separate VPS**,
managed entirely from this panel. No agent is needed on the remote machine — only a reachable
PostgreSQL port (direct TCP or via SSH tunnel).

---

## New files in v2.0

| File | Purpose |
|---|---|
| `store.py` | Encrypted SQLite store for connection profiles |
| `tunnel.py` | Optional SSH tunnel using `paramiko` |

---

## Changes to existing files

### `service.py`
- Add `active_profile: dict | None` (default `None` = local mode)
- `connect(profile)` → opens a transient connection using the active profile
- `get_status()` → when profile is active: probe the remote host instead of `systemctl`
- `mode` field changes from `"local"` to `"remote"` when a profile is active
- `pause()` → also closes any open SSH tunnel

### `queries.py`
- `_run_psql(args)` → currently calls `sudo -u postgres psql`
- In v2: if profile is active, connect via `psql -h HOST -p PORT -U USER` with password from env
- No other changes — all CRUD functions stay the same

### `router.py`
- Add connection profile CRUD endpoints:
  - `GET  /api/connections` — list saved profiles
  - `POST /api/connections` — add profile
  - `PUT  /api/connections/{id}` — update profile
  - `DELETE /api/connections/{id}` — remove profile
  - `POST /api/connections/{id}/test` — test connectivity
  - `POST /api/connections/{id}/activate` — set as active
- Update `GET /` to pass `profiles` to the template
- Add `/settings` tab to the UI sidebar

### `templates/postgres.html`
- Add "Settings" tab to the left sidebar
- Tab is shown only in v2.0+

### `templates/partials/_pg_settings.html` *(new)*
- Profile list table: label, host, port, user, last-tested status
- Add / Edit / Delete / Test / Activate buttons
- Activate switches the active profile (UI reloads, status updates)

---

## `store.py` design

```python
"""
store.py — Encrypted SQLite store for remote connection profiles.
Passwords are encrypted with Fernet (AES-256-CBC) using the panel SECRET_KEY.
"""
from cryptography.fernet import Fernet
import base64, hashlib, sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "profiles.db"

def _fernet(secret_key: str) -> Fernet:
    # Derive a 32-byte key from the panel SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())
    return Fernet(key)

def save_profile(label, host, port, user, password, ssh_user=None, ssh_key_path=None):
    ...

def list_profiles() -> list[dict]:
    ...

def delete_profile(profile_id: int):
    ...
```

---

## `tunnel.py` design

```python
"""
tunnel.py — Optional SSH tunnel to a remote PostgreSQL server.
Opens a forwarded local port on demand; closes after the request completes.
Requires: pip install paramiko
"""
import paramiko, threading

def open_tunnel(ssh_host, ssh_user, ssh_key_path, remote_pg_port=5432) -> int:
    """
    Forward 127.0.0.1:<local_port> → ssh_host → 127.0.0.1:<remote_pg_port>
    Returns the local_port to use in psql -p <local_port>
    """
    ...

def close_tunnel(local_port: int):
    ...
```

---

## New dependency

```
cryptography>=42.0   # Fernet AES-256, already common in Python environments
paramiko>=3.0        # SSH tunnel (optional, only installed if SSH mode enabled)
```

Add to `requirements.txt` before building v2.0.

---

## Security notes for v2.0

- Passwords stored using Fernet with key derived from `config.SECRET_KEY` via SHA-256.
- SSH private keys stored on disk at `plugins/postgres_manager/keys/` with `chmod 600`.
- Never log passwords, keys, or decrypted values.
- Profile passwords are never returned in API responses (write-only).
- SSH host key checking must be enabled (no `RejectPolicy` bypass).

---

## Migration from v1.0

No migration needed. v1.0 stores nothing in the DB.
v2.0 creates `profiles.db` on first use. If no profile is activated, the plugin
continues in local mode exactly as v1.0.
