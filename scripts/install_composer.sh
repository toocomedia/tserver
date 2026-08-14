#!/usr/bin/env bash
set -euo pipefail

VERSION="2.10.2"
SHA256="5ee7125f8a30a34d246cefdc0bc85b8a783b28f2aec968994118512350d28027"
TARGET="/usr/local/bin/composer"
URL="https://getcomposer.org/download/${VERSION}/composer.phar"

if [[ -f "$TARGET" ]] && [[ "$(sha256sum "$TARGET" | awk '{print $1}')" == "$SHA256" ]]; then
  chmod 0755 "$TARGET"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
curl -fL --retry 3 --connect-timeout 15 "$URL" -o "$TMP_DIR/composer.phar"
echo "$SHA256  $TMP_DIR/composer.phar" | sha256sum --check --status || {
  echo "Composer checksum verification failed." >&2
  exit 1
}

install -m 0755 "$TMP_DIR/composer.phar" "$TARGET"
