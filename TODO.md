# Railpack Apps — Remaining Work

## Current working core

- [x] Git repository deployments through Railpack
- [x] Dockerfile deployments
- [x] Ready container-image deployments
- [x] Private app ports and Nginx domain routing
- [x] Build/deployment log, retry, start, stop, restart, and remove actions
- [x] Docker, BuildKit, Railpack, Nginx, Python, and Jinja VPS checks
- [x] PostgreSQL Manager option for compatible applications

## Required before claiming "any app"

- [ ] Add managed Docker database containers.
  - [ ] MariaDB/MySQL (required for WordPress and many PHP apps)
  - [ ] PostgreSQL
  - [ ] Redis
  - [ ] MongoDB
- [ ] Keep each database private on its app Docker network; never publish a database port publicly by default.
- [ ] Create persistent data volumes for every managed database.
- [ ] Add database lifecycle actions: create, reconnect, credentials, backup, restore, and safe deletion.
- [ ] Let users choose: panel-managed database, Docker database, or external database.
- [ ] Detect the likely database from the Git repository and suggest the correct choice, while keeping it editable.

## WordPress support

- [ ] Add a WordPress preset.
- [ ] Create a private MariaDB container with the WordPress app.
- [ ] Create persistent volumes for WordPress uploads and MariaDB data.
- [ ] Set WordPress database variables automatically.
- [ ] Ask for the site title, administrator account, password, and email.
- [ ] Support install, update, backup, restore, and removal without deleting data unless explicitly confirmed.

## App compatibility

- [ ] Test a real deployment for Node.js, Python, PHP, Ruby, Go, Java, static sites, Dockerfile projects, and registry images.
- [ ] Add presets for common applications where automatic detection is insufficient.
- [ ] Allow application-specific environment variables through the UI, with secrets stored only in the protected app environment file.
- [ ] Improve port detection and make the detected port editable before deployment.
- [ ] Show clear runtime logs when the build succeeds but the application fails its health check.

## UI and reliability

- [ ] Finish the Railpack Apps UI/UX rebuild after live browser testing on the VPS.
- [ ] Make deployment state, live output, error, and available actions easy to understand on one screen.
- [ ] Prevent stale deployment/delete states from blocking actions; provide a safe retry/recovery path.
- [ ] Keep the Docker dependency view based on live daemon health, not only its saved panel-toggle state.
- [ ] Add UI/Jinja regression coverage for every create, deploy, control, and removal state.

## Definition of done

The plugin can be described as supporting "any web app" only after it can build or run Git/Dockerfile/image sources, attach the app to an appropriate managed or external database, preserve data safely, and has real VPS deployment tests for each supported runtime.
