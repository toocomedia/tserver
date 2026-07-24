# Docker Dependency Operations

Docker Engine is a trusted SRV Panel dependency. Its summary card is available at `/dependencies`; click it to open `/dependencies/docker`, where the panel reports whether the CLI is installed, whether the daemon answers, detected version, dependent plugins, and the last lifecycle error.

## States

- `not_installed`: Docker CLI is not detected. Docker-dependent plugins are paused.
- `healthy`: Docker daemon answers and the dependency is enabled.
- `stopped`: Docker exists but the daemon is unavailable. Dependents are paused.
- `disabled`: The administrator disabled Docker through the panel. Dependents remain paused.
- `enabling` / `disabling`: A serialized lifecycle operation is in progress.

The panel refreshes cached health after lifecycle actions. If Docker is stopped or removed outside the panel, the next health refresh pauses dependent plugins automatically.

## Install and Guided Uninstall

The **Install Docker** detail-page action supports Ubuntu 22.04 and 24.04. It runs the fixed core installer, configures Docker's official apt repository, installs Engine/CLI/containerd/Buildx/Compose, enables the service, and verifies daemon health. It refuses unsupported systems and conflicting packages rather than removing software automatically. The steps follow the [official Docker Ubuntu installation documentation](https://docs.docker.com/engine/install/ubuntu/).

Docker package removal remains guided and is never executed by the panel.

Before uninstalling, check the API/page precheck for dependent plugins and unmanaged containers. Remove or migrate dependents first. SRV Panel never deletes `/var/lib/docker`; deleting it is a separate permanent administrator action.

## Disable and Recovery

Disable shows affected plugins, requires confirmation, disables and stops `docker.socket` and `docker.service`, and verifies that the daemon is no longer answering. Enable enables/starts both units, waits for a healthy response, and then allows eligible plugins to resume.

If an action fails, the previous desired state is retained and the error appears on the dependency card. Correct sudo/systemd permissions or the daemon failure, then retry. An operation interrupted by a panel restart is reset to idle with a recovery warning.

If the page reports permission denied for `/var/run/docker.sock`, refresh the panel's fixed sudo policy and restart through the normal updater:

```bash
sudo bash /opt/srv-panel/scripts/update.sh
```

The panel uses `sudo -n docker` for daemon checks and owned-resource operations instead of adding the panel service user to the root-equivalent `docker` group.

## Troubleshooting Commands

```bash
sudo systemctl status docker.service docker.socket
sudo journalctl -u docker.service -n 100 --no-pager
docker info
```

Never use `docker system prune` as a recovery step unless the administrator has independently reviewed and accepted its data impact.
