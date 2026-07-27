# Git & SSH Dependency Internal API

Git is a core source dependency, not a Docker feature. Any panel feature that
needs a repository checks `dependency_manager.is_healthy("git")` first.

Hosted-app integrations must keep Git commands in the Git source service:

- validate a repository URL and branch before use;
- create one read-only deploy key per hosted app;
- clone a selected branch into an immutable release staging directory;
- read the remote branch revision, message, and commit date for update checks;
- check out the exact revision locked when the administrator applies an update;
- return concise command failures without tokens or private-key material.

Callers must never construct `git clone`, `git pull`, or SSH commands from form
input. Repository credentials and private deploy keys stay outside the panel DB
and deployment logs. ZIP upload is currently disabled and marked Coming soon.

## Repository service

`backend/dependencies/git/repository_service.py` is the shared implementation.
Callers use:

- `validate_source(url, branch)` for provider-neutral input validation;
- `temporary_clone(url, branch)` for static project inspection;
- `remote_revision(url, branch)` for a read-only update check;
- `clone(url, branch, target, revision=sha)` for release preparation.

The service returns a normalized checkout and `GitRevision` containing the full
SHA, concise subject, and commit time. Application routes and hosting services
must not call `git`, `ssh`, `subprocess`, or provider APIs directly.

Update checks never modify the active release. Applying an update rechecks the
configured branch, records its exact SHA in the deployment, and checks out that
SHA even if the branch changes while the deployment is running.
