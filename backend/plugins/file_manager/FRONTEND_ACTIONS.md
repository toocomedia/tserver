# File Manager frontend action contract

File Manager exposes only panel-owned application roots. It never accepts a
server path from the browser.

## Screen model

1. Load `GET /plugins/file_manager/api/apps` and let the user choose a managed
   application target. Each entry has `id`, `target_type`, `domain`, `preset`,
   and `status`.
2. Load `GET /plugins/file_manager/api/apps/{target_id}/roots` and show its roots
   as tabs or a compact selector. Never offer a server-path picker.
3. Load `GET .../roots/{root_id}/entries?path=` for the selected root and
   folder. Build breadcrumbs only from returned relative paths.

Every `POST` and `DELETE` request must include the existing session CSRF token
as `X-CSRF-Token`; JSON mutation endpoints do not accept an absent token.

Root metadata is required UI behavior:

- `persistence: live_runtime` means files are from a running container or the
  active Python release. A deploy/recreate replaces those edits. Show this
  warning before a write.
- `persistence: persistent` means a static-site webroot, managed Docker volume,
  or Python application data folder.
- `sensitive: true` identifies Runtime `.env`; show a warning before opening,
  downloading, or saving it.
- Dotfiles, including application `.env`, are normal entries and must be shown.

## Buttons and API calls

| UI action | API | Required UI behavior |
| --- | --- | --- |
| Refresh | `GET .../entries` | Keep the selected root and folder; discard stale listing state. |
| Open folder | `GET .../entries?path={relative_path}` | Never construct an absolute path. |
| Back / breadcrumb | `GET .../entries` | Use returned relative paths only. |
| Open text editor | `GET .../text?path={relative_path}` | Enable only for text files no larger than 2 MB. Store the returned `etag`. |
| Save text | `POST .../text` with `{path, content, etag}` | Require the warning acknowledgement for a live root; refresh after success. |
| New folder | `POST .../directories` with `{path}` | Disable for Runtime `.env`; reject an empty name in the UI. |
| Upload | `POST .../upload` multipart fields `path`, `etag` (when replacing), and `file` | Limit selection to 100 MB; say “Use SFTP for larger files.” |
| Download | `GET .../download?path={relative_path}` | Show the sensitive-file warning for `.env`; browser downloads the response. |
| Rename / move | `POST .../move` with `{source_path, destination_path}` | Both paths must remain in the selected root. |
| Copy | `POST .../copy` with `{source_path, destination_path}` | Both paths must remain in the selected root. |
| Delete | `DELETE .../entries` with `{path, confirmation}` | Modal must require exact text `DELETE {path}` and state that deletion is permanent. |
| Properties | Use the selected entry returned by directory listing or text response | Show name, kind, size, modified time, sensitive flag, and root persistence. |

`...` means `/plugins/file_manager/api/apps/{target_id}/roots/{root_id}`.

## States and errors

- Disable all write controls while a request is in flight. On `409`, refresh
  roots/listing because the app may be deploying, stopped, deleted, or changed.
- On a text/upload ETag `409`, keep unsaved user content, show the exact server
  message, and offer Reload. Never overwrite automatically.
- A symlink is shown as a `symlink` entry but cannot be opened, edited,
  downloaded, moved, copied, or deleted through this UI. Explain that File
  Manager does not follow symlinks for safety.
- Container Runtime `.env` supports only open, save, and download. `PORT` and panel-
  managed database variables cannot be changed; render their returned `409`
  messages inline without displaying their values in notifications.
- A successful Runtime `.env` save returns `restart_required: true`. Tell the
  user that its values take effect after the next Apps Engine restart or
  redeploy; File Manager must not silently restart the app.
- Show `413` as the relevant 2 MB text or 100 MB transfer limit and link the
  user to their existing SFTP workflow for larger files.
- Treat `401`, `403`, `404`, `409`, `413`, and `502` as actionable API errors.
  Do not render error details with `innerHTML`.

## Explicit exclusions

This UI must not add a terminal, command runner, archive extraction, archive
creation, server-root picker, Docker shell access, or SFTP replacement. File
Manager manages only roots returned by its API for the selected app.

## Current target types

| `target_type` | File roots |
| --- | --- |
| `container` | Verified running Railpack container working directory and its declared volumes. |
| `python` | Verified active Python release source plus its persistent `data` folder. |
| `static` | Verified static domain webroot: `/var/www/{domain}/public` by default. |
| `php` | Verified native PHP website root: `/var/www/{domain}`. The selected document root is returned by the PHP Websites API. |

Python source changes are live-release edits and will be replaced by the next
deploy. Static-site and Python-data edits persist. An application `.env` inside
one of these roots is a normal visible file; the separate container Runtime
`.env` remains sensitive.

## Adding a future stack

Do not add stack-specific UI code or a server-path picker. Add one backend
target provider in `file_targets.py` for the new stack type. Its provider must:

1. return only database-owned targets with stable IDs such as `php:{id}`;
2. compute every allowed root from the target ID and panel configuration, never
   from a browser value or unchecked database path;
3. verify the root exists, is not a symlink, and is inside that stack's
   panel-owned storage;
4. reject access while that stack is deploying or deleting;
5. mark each root as `persistent` or `live_runtime`.

Once that provider exists, the existing list, read, edit, upload, download,
move, copy, delete, audit, CSRF, size-limit, and symlink protections apply
without changing File Manager routes or frontend actions.
