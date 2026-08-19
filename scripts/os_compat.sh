#!/usr/bin/env bash
# Shared SRV Panel operating-system detection and capability checks.
# Source this file from installers, or execute it with --report/--require.

srv_os_detect() {
  local release_file="${SRV_OS_RELEASE_FILE:-/etc/os-release}"
  SRV_OS_ID="unknown"
  SRV_OS_VERSION_ID="unknown"
  SRV_OS_CODENAME="unknown"
  SRV_OS_PRETTY_NAME="Unknown Linux"
  SRV_OS_ARCH="${SRV_OS_ARCH:-$(uname -m 2>/dev/null || echo unknown)}"

  if [[ -r "$release_file" ]]; then
    local ID="" VERSION_ID="" VERSION_CODENAME="" UBUNTU_CODENAME="" PRETTY_NAME=""
    # shellcheck disable=SC1090
    . "$release_file"
    SRV_OS_ID="${ID:-unknown}"
    SRV_OS_VERSION_ID="${VERSION_ID:-unknown}"
    SRV_OS_CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-unknown}}"
    SRV_OS_PRETTY_NAME="${PRETTY_NAME:-${SRV_OS_ID} ${SRV_OS_VERSION_ID}}"
  fi

  case "$SRV_OS_ARCH" in
    x86_64|amd64) SRV_OS_ARCH="amd64" ;;
  esac

  SRV_OS_SUPPORTED=0
  SRV_OS_ERROR=""
  SRV_OS_CAPABILITIES=""

  if [[ "$SRV_OS_ARCH" != "amd64" ]]; then
    SRV_OS_ERROR="Unsupported CPU architecture ${SRV_OS_ARCH}. SRV Panel currently supports amd64 only."
  else
    case "${SRV_OS_ID}:${SRV_OS_VERSION_ID}" in
      ubuntu:22.04)
        SRV_OS_SUPPORTED=1
        SRV_OS_CAPABILITIES="core docker php mariadb postgresql railpack_apps php_external_repository"
        ;;
      ubuntu:24.04|ubuntu:26.04)
        SRV_OS_SUPPORTED=1
        SRV_OS_CAPABILITIES="core docker php mariadb postgresql railpack_apps native_python php_external_repository"
        ;;
      debian:12|debian:13)
        SRV_OS_SUPPORTED=1
        SRV_OS_CAPABILITIES="core docker php mariadb postgresql railpack_apps native_python"
        ;;
      ubuntu:*)
        SRV_OS_ERROR="Unsupported Ubuntu version ${SRV_OS_VERSION_ID}. Supported versions: 22.04, 24.04, 26.04."
        ;;
      debian:*)
        SRV_OS_ERROR="Unsupported Debian version ${SRV_OS_VERSION_ID}. Supported versions: 12, 13."
        ;;
      *)
        SRV_OS_ERROR="Unsupported operating system ${SRV_OS_PRETTY_NAME}. Supported systems: Ubuntu 22.04/24.04/26.04 and Debian 12/13."
        ;;
    esac
  fi

  export SRV_OS_ID SRV_OS_VERSION_ID SRV_OS_CODENAME SRV_OS_PRETTY_NAME
  export SRV_OS_ARCH SRV_OS_SUPPORTED SRV_OS_ERROR SRV_OS_CAPABILITIES
}

srv_os_require_supported() {
  srv_os_detect
  if [[ "$SRV_OS_SUPPORTED" != "1" ]]; then
    printf 'ERROR: %s\n' "$SRV_OS_ERROR" >&2
    return 1
  fi
}

srv_os_supports() {
  local capability="$1"
  srv_os_detect
  [[ " $SRV_OS_CAPABILITIES " == *" $capability "* ]]
}

srv_os_report() {
  srv_os_detect
  printf 'id=%s\n' "$SRV_OS_ID"
  printf 'version_id=%s\n' "$SRV_OS_VERSION_ID"
  printf 'codename=%s\n' "$SRV_OS_CODENAME"
  printf 'pretty_name=%s\n' "$SRV_OS_PRETTY_NAME"
  printf 'arch=%s\n' "$SRV_OS_ARCH"
  printf 'supported=%s\n' "$SRV_OS_SUPPORTED"
  printf 'capabilities=%s\n' "$SRV_OS_CAPABILITIES"
  printf 'error=%s\n' "$SRV_OS_ERROR"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:---report}" in
    --report) srv_os_report ;;
    --require)
      srv_os_require_supported
      srv_os_report
      ;;
    *)
      printf 'Usage: %s [--report|--require]\n' "$0" >&2
      exit 2
      ;;
  esac
fi
