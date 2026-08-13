#!/usr/bin/env bash
set -euo pipefail

VERSION="2.12.0"
TARGET="/usr/local/bin/wp"
BASE_URL="https://github.com/wp-cli/wp-cli/releases/download/v${VERSION}"
PHAR="wp-cli-${VERSION}.phar"

if [[ -x "$TARGET" ]] && "$TARGET" --allow-root --version 2>/dev/null | grep -q "WP-CLI ${VERSION}"; then
  exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/$PHAR" -o "$TMP_DIR/$PHAR"
curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/$PHAR.sha256" -o "$TMP_DIR/$PHAR.sha256"

EXPECTED="$(awk '{print $1}' "$TMP_DIR/$PHAR.sha256")"
ACTUAL="$(sha256sum "$TMP_DIR/$PHAR" | awk '{print $1}')"
[[ -n "$EXPECTED" && "$EXPECTED" == "$ACTUAL" ]] || {
  echo "WP-CLI checksum verification failed." >&2
  exit 1
}

install -m 0755 "$TMP_DIR/$PHAR" "$TARGET"
