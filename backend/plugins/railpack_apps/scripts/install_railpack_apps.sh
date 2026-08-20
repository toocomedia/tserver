#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not available." >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "Docker BuildKit/buildx is required." >&2
  exit 1
fi
RAILPACK_VERSION="0.23.0"
if ! command -v railpack >/dev/null 2>&1 || ! railpack --version 2>/dev/null | grep -q "$RAILPACK_VERSION"; then
  curl -fsSL https://railpack.com/install.sh | RAILPACK_VERSION="$RAILPACK_VERSION" sh -s -- --bin-dir /usr/local/bin
fi
if ! docker inspect --format '{{.State.Running}}' srv-panel-buildkit 2>/dev/null | grep -qx true; then
  docker rm -f srv-panel-buildkit >/dev/null 2>&1 || true
  docker run -d --name srv-panel-buildkit --restart unless-stopped --privileged \
    --label 'srv-panel.engine=railpack-buildkit' moby/buildkit:buildx-stable-1 >/dev/null
fi
if ! docker buildx inspect srv-panel-builder >/dev/null 2>&1; then
  docker buildx create --name srv-panel-builder --driver docker-container >/dev/null 2>&1 || true
fi
railpack --version

