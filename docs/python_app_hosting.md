# Python App Hosting

This document is the internal contract for hosting Python web applications in
SRV Panel. Version 1 uses the system Python runtime and systemd. Docker is not
part of this feature.

## Scope

Supported in version 1:

- one public web application per managed domain;
- Git repository source;
- FastAPI, Flask, Django, and generic Python entrypoints when detection is
  unambiguous;
- isolated virtual environments;
- Nginx proxy and existing or new Let's Encrypt SSL;
- PostgreSQL managed by the panel or an external `DATABASE_URL`;
- SQLite as local persistent application storage;
- deployment progress, logs, start, stop, restart, and strict delete;
- source-aware Git updates with automatic rollback;
- automatic CPU and memory reporting per hosted Python app.

Not included:

- Docker, Conda, Node, PHP, Go, workers, cron jobs, webhooks, automatic Git
  deploys, or ZIP upload/update;
- application source editing in the panel;
- arbitrary private networking or public binding to an application port;
- automatic installation of missing project dependencies outside the app's own
  build command.

## Core dependencies

### Git & SSH

**Git & SSH** is a core dependency used to inspect and deploy repository
sources. It reports health and version in Dependencies. It cannot be stopped or
uninstalled because the panel itself and hosted applications rely on it.

Repository handling rules are defined in
[`git_dependency_internal_api.md`](git_dependency_internal_api.md). Application
hosting must use that contract rather than exposing repository credentials or
raw Git output to the browser.

### Python Runtime

**Python Runtime** requires Python 3.11 or newer with `venv` and `pip`. It is a
shared core runtime, not one daemon per application. It reports health and
version but cannot be stopped or uninstalled from Dependencies because SRV
Panel uses Python too.

### Docker

Docker is neither required nor modified by Python app hosting. A Python app is
not a Docker dependency and does not create a container.

## Domain behavior

Selecting **Python App** when creating a domain creates the DNS/domain record
only. It does not create a static website webroot. The panel redirects to
**Complete Python App Setup** after the domain is created.

Only one hosted Python application can be attached to one domain. A duplicate
app setup is rejected. The app receives a private loopback port; its public URL
always uses the domain through Nginx.

## Source options

### Git repository

The source form accepts these public formats:

```text
https://github.com/owner/project
https://github.com/owner/project.git
git@github.com:owner/project.git
```

HTTPS and SSH URLs may omit `.git`. URLs must have a valid hostname and path;
embedded credentials, whitespace, shell syntax, and arbitrary URL schemes are
not accepted. Branch names are limited to safe Git branch characters.

The panel tries the selected branch first. If the form still contains the
default `main` but the repository does not have that branch, inspection clones
the repository default branch and saves it for later deployment. A deliberately
chosen non-default branch is not silently replaced.

For a public GitHub SSH URL where SSH is unavailable, the panel may retry with
the equivalent HTTPS URL and records that transport change. Private source
access requires the Git/SSH integration described in the Git internal API.

### ZIP upload

ZIP source and ZIP updates are **Coming soon**. Active hosting routes do not
upload, buffer, inspect, or extract ZIP archives. Existing ZIP apps from an
older panel version remain runnable and may redeploy their installed source.

## Safe project detection

Inspection is static: it reads project files and Python AST only. It does not
import the application, execute setup code, install requirements, or connect to
the app database.

Detection checks:

- package files: `requirements.txt`, `pyproject.toml`, `poetry.lock`,
  `uv.lock`, `Pipfile`, and `Procfile`;
- FastAPI and Flask app objects through AST scanning;
- Django `asgi.py` and `wsgi.py` conventions;
- explicit FastAPI configuration from `pyproject.toml`;
- `.env.example` and `.env.sample` variable names only, never values;
- PostgreSQL, SQLite, and async SQLAlchemy hints;
- Conda files (`environment.yml` and `environment.yaml`).

The detector proposes a build command, start command, framework, entrypoint,
package manager, required environment names, and storage/database hints.

### One-Click Deploy

One-click deploy is available only when there is one clear supported
configuration. It falls back to Advanced Setup when the project has multiple
entrypoints, required environment values, PostgreSQL review needs, Conda, or an
unsupported/ambiguous structure.

### Advanced Setup

Advanced Setup lets the administrator confirm or change:

- Git repository and branch;
- build command;
- start command;
- no managed database, panel-created PostgreSQL, or external `DATABASE_URL`.

Build and start commands must be non-empty and at most 1,000 characters. They
are administrator-only configuration and execute under the hosted app account;
do not use untrusted repository content or unreviewed shell commands.

## File layout and permissions

For hosted app ID `<id>`, the runtime layout is:

```text
/var/lib/srv-panel/apps/<id>/
├── current -> releases/42/ active release link
├── releases/
│   ├── 41/
│   │   ├── source/         previous project source
│   │   └── .venv/          previous isolated dependencies
│   └── 42/
│       ├── source/         active project source
│       └── .venv/          active isolated dependencies
└── data/                   persistent local application data

/var/lib/srv-panel/apps-env/<id>.env
/etc/systemd/system/srv-python-<app-id>.service
```

The shared `apps/` and `apps-env/` roots are protected with mode `0700`. The
environment file is written with mode `0600` and is never returned through the
HTML page or API after its value is saved.

The panel provides these environment values on every deployment:

```text
HOST=127.0.0.1
PORT=<assigned private port>
APP_DATA_DIR=/var/lib/srv-panel/apps/<id>/data
```

Project `.env` files are not copied into the protected panel environment file.
Applications that load their own `.env` must keep secrets out of Git; panel
environment values are the supported deployment secret store.

## systemd runtime and ports

Each app receives a unique port from the configured app-port range. The app
must listen on `127.0.0.1:<port>` only. It is not directly reachable from the
internet; Nginx is the only public listener.

The generated systemd unit uses the selected release:

- the configured hosted-app Linux user and group;
- `current/source/` as the logical `WorkingDirectory`;
- the protected environment file as `EnvironmentFile`;
- the app virtual environment to run the confirmed start command;
- `Restart=on-failure` for crash recovery.

The app ID permanently owns its service, port, app folder, environment file,
and virtual environments. System Python is shared, but installed requirements
are never shared between apps.

The panel does not claim a fixed CPU, RAM, or process quota for a Python app.
The application owner is responsible for efficient code and dependency choices.
Usage metrics expose actual consumption so the server owner can manage it.

## Deployment sequence

Every deployment creates a persisted deployment record and reports stages in
the app page:

```text
inspect/source → venv → dependencies → service → listener → nginx → ssl
```

The actual sequence is:

1. Lock the selected Git commit.
2. Prepare `releases/<deployment-id>/source` without changing the live app.
3. Create a new virtual environment inside that release.
4. Preserve panel environment values and add `HOST`, `PORT`, and `APP_DATA_DIR`.
5. Run the confirmed build command while the old release remains online.
6. Stop the app briefly, atomically switch `current`, and start the same unit
   on the same private port.
7. Require a local HTTP response from `127.0.0.1:<port>`.
8. If startup fails, restore `current`, restart the previous release, and
   report the rollback result in deployment status.
9. Generate Nginx only for an initial deployment; normal updates preserve the
   existing Nginx and SSL configuration.
10. Keep only the current and previous successful releases.

A failure before cutover leaves the running app untouched. A failure after
cutover automatically restores the previous release when possible. The failed
deployment record and output remain available for diagnosis.

## Source updates

### Git updates

**Check for updates** reads the configured remote branch through the Git
Dependency API. The panel stores the deployed SHA, available SHA, commit
subject/date, and check time. **Apply update** rechecks the branch, locks the
exact remote SHA, and starts a background update. If both SHAs match, deployment
is rejected as already current.

### ZIP updates

ZIP updates are coming soon and have no active upload endpoint.

Git updates preserve environment values, PostgreSQL data, SQLite files
under `APP_DATA_DIR`, domain, port, Nginx, and SSL. **Redeploy current version**
builds the deployed source again and is an advanced recovery action, not an
update check.

## Nginx and SSL

Nginx proxies the domain to the app's private loopback port. It forwards the
standard Host, client IP, and forwarded-protocol headers required by modern
Python frameworks.

If the domain already has an SSL certificate, a later app deployment detects
it and regenerates the HTTPS proxy rather than replacing it with a static-site
configuration. Nginx reloads automatically; the domain does not need a restart.

HTTP 502 normally means Nginx cannot reach the private app listener. HTTP 500
means the application itself raised an error. Use **Service logs** first for
application tracebacks.

## Databases and storage

### No managed database

The panel does not create a database or `DATABASE_URL`. The application may use
its own remote service or SQLite storage.

### Panel-created PostgreSQL

The panel creates an isolated role and database for the app and stores the
connection URL only in the protected environment file. The database and role
are not removed by Strict Delete; database deletion requires a separate,
confirmed PostgreSQL action.

Projects using SQLAlchemy `create_async_engine` receive a
`postgresql+asyncpg://` URL. Existing panel-managed URLs are corrected on the
next deployment. The project must include the matching async database driver,
such as `asyncpg`, in its own requirements.

### External `DATABASE_URL`

The application owner supplies the complete URL. The panel validates only that
it is present and protects it in the environment file. It does not test or
modify the remote database.

For SQLAlchemy async applications, `psycopg2` is not an async driver. Use the
correct dialect/driver pair, for example:

```text
postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/DATABASE
```

### SQLite

SQLite is not PostgreSQL and is not shown as a managed database. Use
`APP_DATA_DIR` for a SQLite file that must survive source replacement, for
example `APP_DATA_DIR/app.sqlite3` when the project supports that setting.

## Environment controls

The app page lists only saved variable names. Values are masked and never read
back into the browser. Newlines are rejected from environment values. `HOST`
and `PORT` are reserved by the panel; `DATABASE_URL` is managed by the selected
database mode and must not be removed through the normal variable-delete path.

Saving an environment value does not restart the application automatically.
Use **Restart** or **Redeploy current version** after changing a value.

## Lifecycle controls and strict cleanup

### Start

Start enables and starts the systemd unit, then verifies that it becomes active.
It makes a manually stopped app available again after reboot.

If the protected environment file is missing, Start refuses before systemd can
enter a restart loop. Redeploy or run the ownership repair command first. For
an external database, save `DATABASE_URL` again before repair.

### Restart

Restart restarts the existing unit and verifies that it becomes active. It does
not rebuild source or dependencies; use Redeploy current version for that.

### Stop

Stop first cancels a queued or running deployment so a background deployment
cannot relaunch the app. It then disables and stops the systemd unit and checks
that it is no longer active. The stopped state persists across reboot.

### Runtime dependencies and pause

Hosted apps use the shared dependency contract in
`services/app_dependency_service.py`. Every runtime requirement is checked
before create, start, restart, deploy, and update. Disabling a dependency pauses
its running apps, serves their Nginx offline page with HTTP `503`, and never
deletes their files or data. When the dependency returns, apps remain paused
until the user explicitly resumes them.

`postgres_mode=create` requires the existing PostgreSQL Manager service.
External `DATABASE_URL` and SQLite/no-database apps do not. Future dependencies
declare their app rule in `requirement_ids(app)` without duplicating an existing
plugin as a second Dependencies card.

### Strict Delete

Strict Delete stops and disables the unit, removes its unit file, reloads
systemd, removes Nginx, every release, persistent app folder, and
environment file, and deletes app/deployment records. It does not delete
PostgreSQL data.

## Logs and troubleshooting

Deployment output records panel stages. **Service logs** reads the last 200
lines from `journalctl` for that specific app unit.

Common failures:

| Symptom | First check |
| --- | --- |
| Git branch not found | Run Detect project; confirm saved branch. |
| Listener check fails | Service logs; verify the start command uses `$HOST` and `$PORT`. |
| HTTP 502 | Service is not listening on the assigned loopback port. |
| HTTP 500 | Application traceback, templates, environment, or database driver. |
| SQLAlchemy async/psycopg2 error | Use an async driver and matching URL dialect. |
| Git shows no update | Check the configured branch and compare the displayed SHAs. |
| Updated app fails health check | Review rollback state, deployment output, then service logs. |
| Service ownership conflict or missing env file | Run the ownership audit below; do not start another app on that port. |

### Ownership repair

Use this once after upgrading from old domain-based service naming. It is
read-only by default; `--apply` repairs records and units but leaves apps
stopped for explicit user start.

```text
cd /opt/srv-panel/app/backend
python3 app_hosting/docs/repair_app_ownership_on_vps.py
python3 app_hosting/docs/repair_app_ownership_on_vps.py --apply
```

## Usage metrics

The Usage page contains **Hosted Python Apps**. It automatically matches
processes whose command line belongs to:

```text
/var/lib/srv-panel/apps/<id>/
```

For each app it reports domain, process count, CPU percentage, memory, and live
status. Hosted Uvicorn processes are not counted as the SRV Panel process.

**Runtime Dependencies** explains shared tools correctly:

- Git & SSH is on-demand and has no resident process while idle.
- Python Runtime is shared; its actual CPU/RAM is assigned to hosted apps
  instead of being reported as misleading global Python usage.
- PostgreSQL and every future dependency registered by SRV Panel are listed
  automatically. Docker remains in **Stack Services** because that row reports
  the Docker daemon's live process use.

## VPS validation

The Python-hosting VPS diagnostic is read-only. It checks Git and SSH, Python
and venv, service state, loopback binding, Nginx syntax/proxy response, SSL,
PostgreSQL presence/connectivity, and environment-file permissions. It never
installs, restarts, deploys, deletes, or changes VPS state.

For dependency pause verification, run the read-only checker once while the app
is running and again after stopping PostgreSQL from PostgreSQL Manager:

```text
python3 backend/app_hosting/docs/test_dependency_pause_on_vps.py --service srv-python-42 --port 9101 --domain app.example.com --expect running
python3 backend/app_hosting/docs/test_dependency_pause_on_vps.py --service srv-python-42 --port 9101 --domain app.example.com --expect paused
```
