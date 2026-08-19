#!/bin/bash
# progress.sh — Visual step-progress display for tserver installer
# Source this file; do NOT execute it directly.
#
# Usage:
#   TOTAL_STEPS=12              # set before sourcing, or override after
#   source scripts/progress.sh
#   _progress_init              # call once at the start of the script
#
#   step_start "Label"          # prints step banner, starts timer
#   step_ok                     # prints ✓ done (Ns)
#   step_skip "reason"          # prints ↷ skipped: reason
#   step_warn "message"         # prints ⚠ message (inside a step)

STEP_CURRENT=0
TOTAL_STEPS="${TOTAL_STEPS:-12}"
INSTALL_LOG="${INSTALL_LOG:-/var/log/tserver-install.log}"
_STEP_START_TIME=0

# ── Colours ──────────────────────────────────────────────────────────────────
_P_GRN='\033[0;32m'
_P_CYN='\033[0;36m'
_P_YLW='\033[1;33m'
_P_GRY='\033[0;90m'
_P_NC='\033[0m'
_P_BOLD='\033[1m'

# ── Init ─────────────────────────────────────────────────────────────────────
_progress_init() {
  # Try standard log location; fall back to /tmp on permission error
  local log_dir
  log_dir="$(dirname "$INSTALL_LOG")"
  if mkdir -p "$log_dir" 2>/dev/null && touch "$INSTALL_LOG" 2>/dev/null; then
    : # log_dir writable
  else
    INSTALL_LOG="/tmp/tserver-install.log"
    touch "$INSTALL_LOG" 2>/dev/null || true
  fi
  {
    echo "================================================================="
    echo " tserver install — started $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo " OS: ${OS_NAME:-unknown}  (${OS_FAMILY:-?})"
    echo "================================================================="
  } >> "$INSTALL_LOG"
  echo ""
  printf "${_P_CYN}${_P_BOLD}  tserver installer — %d steps${_P_NC}\n" "$TOTAL_STEPS"
  printf "${_P_GRY}  Log: %s${_P_NC}\n\n" "$INSTALL_LOG"
}

# ── Box drawing helper ────────────────────────────────────────────────────────
_box_line() {
  # _box_line <char> <width>
  local char="$1" width="$2"
  printf '%0.s'"$char" $(seq 1 "$width")
}

# ── Step banner ───────────────────────────────────────────────────────────────
step_start() {
  local label="$*"
  STEP_CURRENT=$(( STEP_CURRENT + 1 ))
  _STEP_START_TIME=$(date +%s 2>/dev/null || echo 0)

  local BOX_W=54
  local top_text="  tserver  —  Step ${STEP_CURRENT} of ${TOTAL_STEPS}"
  local lbl_text="  ${label}"

  printf '\n'
  printf "${_P_CYN}╔$(_box_line '═' $BOX_W)╗${_P_NC}\n"
  printf "${_P_CYN}║${_P_BOLD}%-${BOX_W}s${_P_NC}${_P_CYN}║${_P_NC}\n" "$top_text"
  printf "${_P_CYN}║${_P_NC}%-${BOX_W}s${_P_CYN}║${_P_NC}\n"           "$lbl_text"
  printf "${_P_CYN}╚$(_box_line '═' $BOX_W)╝${_P_NC}\n"

  printf '[%s] STEP %d/%d: %s\n' \
    "$(date -u '+%H:%M:%S')" "$STEP_CURRENT" "$TOTAL_STEPS" "$label" \
    >> "$INSTALL_LOG" 2>/dev/null || true
}

# ── Step result helpers ───────────────────────────────────────────────────────
step_ok() {
  local now elapsed
  now=$(date +%s 2>/dev/null || echo 0)
  elapsed=$(( now - _STEP_START_TIME ))
  printf "${_P_GRN}  ✓ done${_P_GRY} (${elapsed}s)${_P_NC}\n"
  printf '[%s] STEP %d OK (%ds)\n' \
    "$(date -u '+%H:%M:%S')" "$STEP_CURRENT" "$elapsed" \
    >> "$INSTALL_LOG" 2>/dev/null || true
}

step_skip() {
  local reason="${1:-}"
  printf "${_P_YLW}  ↷ skipped${_P_GRY}${reason:+: $reason}${_P_NC}\n"
  printf '[%s] STEP %d SKIPPED%s\n' \
    "$(date -u '+%H:%M:%S')" "$STEP_CURRENT" "${reason:+: $reason}" \
    >> "$INSTALL_LOG" 2>/dev/null || true
}

step_warn() {
  local msg="$*"
  printf "${_P_YLW}  ⚠  ${msg}${_P_NC}\n"
  printf '[%s] STEP %d WARN: %s\n' \
    "$(date -u '+%H:%M:%S')" "$STEP_CURRENT" "$msg" \
    >> "$INSTALL_LOG" 2>/dev/null || true
}
