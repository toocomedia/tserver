#!/usr/bin/env bash
set -euo pipefail
# App resources are deliberately preserved. PluginManager blocks uninstall while any exist.
docker rm -f srv-panel-buildkit >/dev/null 2>&1 || true
exit 0
