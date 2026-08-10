# MariaDB Dependency Operations

## Ownership and versions

`dependency.json` version is the SRV Panel driver version. The runtime version
is the installed `mariadb-server` package version detected on the VPS.

Panel-managed MariaDB installs and updates use the APT repositories already
configured on the server. This may be an Ubuntu mirror, a VPS-provider mirror,
or an already configured MariaDB repository. The panel never replaces or adds
a package repository automatically.

An existing MariaDB installation is marked external. The panel reports its
health and version but does not rewrite its configuration, control it, update
it, or expose MariaDB Manager controls for it.

## Network and data policy

Panel-managed MariaDB binds to `127.0.0.1` only. It has no public database
listener or remote-access feature.

Package-only removal preserves `/var/lib/mysql`. Full data removal is a future
separate destructive operation and must require `REMOVE MARIADB AND ALL DATA`.

## Update policy

The Update button checks the configured APT sources and displays the available
candidate version. Only a newer patch/minor release within the installed major
line may be applied. A major version candidate is reported but blocked until a
dedicated migration workflow exists.

Applying an update requires `UPDATE MARIADB`. The fixed update script creates
and verifies a full timestamped dump in `/var/backups/srv-panel/mariadb/`, then
upgrades the fixed MariaDB packages, restarts the service, and verifies a local
socket connection. A failed backup or health check fails the update.

## Manager boundary

MariaDB Manager owns panel-created databases, restricted users, and password
resets. It uses the root Unix socket through a fixed root-owned helper. It does
not expose arbitrary SQL, root credentials, remote access, or Docker MariaDB
containers managed by Railpack Apps.
