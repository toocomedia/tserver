# Railpack Apps VPS acceptance

Run this only on a disposable VPS domain after the read-only checker passes.

1. Deploy one Git project for Node.js, Python, PHP, Ruby, Go, Java, and a static site; then deploy one Dockerfile project and one registry image. Confirm each domain responds through Nginx and no app port is publicly reachable.
2. Deploy an app with MariaDB, PostgreSQL, Redis, and MongoDB attachments. From the app container, resolve each `db-<kind>` alias; from the host, confirm no managed database has a published port.
3. Rotate every generated credential, restart the app, create a backup, restore it with the required typed confirmation, and verify the application data again.
4. Deploy WordPress, complete the automatic install, create content, update WordPress, back it up, restore it, remove only the application, then test the separately confirmed data deletion on a disposable site.

The panel must not claim broad web-app support until every item has been recorded as passing on the target VPS.
