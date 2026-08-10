# File Manager VPS acceptance

Run this only against a disposable Apps Engine Laravel or WordPress app. These
checks modify files and one check restarts/redeploys the selected application.

## Prerequisites

- The panel is served over HTTPS, File Manager is enabled, Docker is healthy,
  and the selected app is `running`.
- Use an authenticated browser session or `curl` cookie plus its CSRF token.
- Set `APP_ID`, choose the `application` root and, for WordPress, the separate
  `wordpress-content` root. Use an otherwise unused filename such as
  `.srv-panel-file-manager-acceptance.txt`.

## API checks

1. `GET /plugins/file_manager/api/apps` lists the selected Apps Engine app,
   and `GET .../apps/{APP_ID}/roots` returns no host or server roots.
2. List the application root. Confirm dotfiles are included; a Laravel `.env`
   is visible if it exists in the image. Attempt `../etc/passwd` and verify a
   `400` response. Attempt a known symlink and verify it is refused with `409`.
3. Create, edit, download, rename, copy, and delete the temporary file. Check
   the downloaded bytes exactly match the saved text. Confirm every mutation
   fails without `X-CSRF-Token`.
4. In the persistent WordPress-content or configured data root, upload the
   temporary file, restart the selected app through Apps Engine, and confirm
   the file still exists with the same bytes. Delete it with the exact
   `DELETE {path}` confirmation.
5. In the live application root, create a temporary file, redeploy the app,
   and confirm the API warns it is `live_runtime` and the rebuild replaces the
   live edit. Do not perform this on an app with uncommitted customer changes.
6. Open Runtime `.env`, save an allowed value, and verify the response has
   `restart_required: true`. Restart/redeploy explicitly, then verify the app
   sees the updated value. Verify `PORT` and panel-managed database values
   cannot be changed.

Finally, inspect `file_manager_events`: it must record actor, app, root, path,
action, result, size/count, IP, and request ID, but never file contents or
environment values.
