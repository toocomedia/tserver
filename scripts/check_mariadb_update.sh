#!/usr/bin/env bash
# Check the MariaDB candidate supplied by this VPS's configured APT sources.
set -euo pipefail

major() {
  local version="${1#*:}"
  printf '%s' "${version%%.*}"
}

apt-get update -qq
installed="$(dpkg-query -W -f='${Version}' mariadb-server 2>/dev/null || true)"
candidate="$(apt-cache policy mariadb-server | awk '/Candidate:/ { print $2; exit }')"

if [[ -z "$installed" || -z "$candidate" || "$candidate" == "(none)" ]]; then
  echo "MariaDB package candidate is unavailable from configured APT repositories." >&2
  exit 1
fi

available=false
major_change=false
if dpkg --compare-versions "$candidate" gt "$installed"; then
  available=true
  if [[ "$(major "$candidate")" != "$(major "$installed")" ]]; then
    major_change=true
  fi
fi

printf 'installed=%s\n' "$installed"
printf 'candidate=%s\n' "$candidate"
printf 'available=%s\n' "$available"
printf 'major_change=%s\n' "$major_change"
printf 'source=Configured APT repositories\n'
