# Docker Dependency Operations

Docker Engine is a trusted SRV Panel dependency. Its card is available at `/dependencies` and reports whether the CLI is installed, whether the daemon answers, detected version, dependent plugins, and the last lifecycle error.

## States

- `not_installed`: Docker CLI is not detected. Docker-dependent plugins are paused.
- `healthy`: Docker daemon answers and the dependency is enabled.
- `stopped`: Docker exists but the daemon is unavailable. Dependents are paused.
- `disabled`: The administrator disabled Docker through the panel. Dependents remain paused.
- `enabling` / `disabling`: A serialized lifecycle operation is in progress.

The panel refreshes cached health after lifecycle actions. If Docker is stopped or removed outside the panel, the next health refresh pauses dependent plugins automatically.

## Guided Install and Uninstall

Phase 1 does not execute host package installation or removal. The page displays guided commands for supported Ubuntu 22.04 and 24.04 servers. Review the current official Docker documentation before running them.

Before uninstalling, check the API/page precheck for dependent plugins and unmanaged containers. Remove or migrate dependents first. SRV Panel never deletes `/var/lib/docker`; deleting it is a separate permanent administrator action.

## Disable and Recovery

Disable shows affected plugins, requires confirmation, disables and stops `docker.socket` and `docker.service`, and verifies that the daemon is no longer answering. Enable enables/starts both units, waits for a healthy response, and then allows eligible plugins to resume.

If an action fails, the previous desired state is retained and the error appears on the dependency card. Correct sudo/systemd permissions or the daemon failure, then retry. An operation interrupted by a panel restart is reset to idle with a recovery warning.

## Troubleshooting Commands

```bash
sudo systemctl status docker.service docker.socket
sudo journalctl -u docker.service -n 100 --no-pager
docker info
```

Never use `docker system prune` as a recovery step unless the administrator has independently reviewed and accepted its data impact.
