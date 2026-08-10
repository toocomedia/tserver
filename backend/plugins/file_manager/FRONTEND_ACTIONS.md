# File Manager frontend action contract

This plugin currently provides backend APIs only. Do not add a sidebar item,
template, CSS, or JavaScript until the File Manager UI is intentionally built.

## Screen model

1. Load `GET /plugins/file_manager/api/apps` and let the user choose a running
   Apps Engine application.
2. Load `GET /plugins/file_manager/api/apps/{app_id}/roots` and show its roots
   as tabs or a compact selector. Never offer a server-path picker.
3. Load `GET .../roots/{root_id}/entries?path=` for the selected root and
   folder. Build breadcrumbs only from returned relative paths.

Every `POST` and `DELETE` request must include the existing session CSRF token
as `X-CSRF-Token`; JSON mutation endpoints do not accept an absent token.

Root metadata is required UI behavior:

- `persistence: live_runtime` means files are in the active container and a
  deploy/recreate replaces those edits. Show this warning before a write.
- `persistence: persistent` means the root is a managed Docker volume.
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

`...` means `/plugins/file_manager/api/apps/{app_id}/roots/{root_id}`.

## States and errors

- Disable all write controls while a request is in flight. On `409`, refresh
  roots/listing because the app may be deploying, stopped, deleted, or changed.
- On a text/upload ETag `409`, keep unsaved user content, show the exact server
  message, and offer Reload. Never overwrite automatically.
- A symlink is shown as a `symlink` entry but cannot be opened, edited,
  downloaded, moved, copied, or deleted through this UI. Explain that File
  Manager does not follow symlinks for safety.
- Runtime `.env` supports only open, save, and download. `PORT` and panel-
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
