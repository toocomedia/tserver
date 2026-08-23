#!/usr/bin/env bash
# A10 — App Engine AI setup acceptance on VPS
#
# Safe default: creates and validates a reviewed setup plan only.
# Set APPLY=1 to also call the same Deploy reviewed setup endpoint used by chat.
set -euo pipefail

PANEL_URL="${PANEL_URL:-http://localhost:8000}"
TARGET_DOMAIN="${TARGET_DOMAIN:-c.c.tooco.net}"
TARGET_REPO="${TARGET_REPO:-https://github.com/plausible/analytics}"
TASK_TYPE="${TASK_TYPE:-app_deploy}"
SESSION_ID="${SESSION_ID:-accept_ai_setup_$(date +%s)}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-240}"
APPLY="${APPLY:-0}"

COOKIE_FILE="${COOKIE_FILE:-}"
SESSION_COOKIE="${SESSION_COOKIE:-}"

if ! command -v curl >/dev/null 2>&1; then
  echo "FAIL: curl is required."
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "FAIL: python3 is required."
  exit 1
fi

if [ -z "$COOKIE_FILE" ] && [ -z "$SESSION_COOKIE" ]; then
  echo "FAIL: provide auth with COOKIE_FILE=/path/to/cookies.txt or SESSION_COOKIE='session=...'."
  echo "      Export a logged-in browser cookie from the panel, then rerun this script."
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

AUTH_ARGS=()
if [ -n "$COOKIE_FILE" ]; then
  AUTH_ARGS=(-b "$COOKIE_FILE" -c "$COOKIE_FILE")
else
  AUTH_ARGS=(-H "Cookie: $SESSION_COOKIE")
fi

BASE="${PANEL_URL%/}"
CHAT_URL="$BASE/plugins/ai_helper/api/chat"
AI_PAGE="$BASE/plugins/ai_helper/"

echo "=== A10: App Engine AI setup acceptance ==="
echo "  Panel:  $BASE"
echo "  Domain: $TARGET_DOMAIN"
echo "  Repo:   $TARGET_REPO"
echo "  Apply:  $APPLY"

echo "[1] Fetch authenticated AI page and CSRF token..."
AI_HTML="$TMP_DIR/ai.html"
HTTP_CODE=$(curl -sS -L -m 30 -w '%{http_code}' -o "$AI_HTML" "${AUTH_ARGS[@]}" "$AI_PAGE")
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: could not open AI page; HTTP $HTTP_CODE"
  exit 1
fi
if grep -qi '<form[^>]*login\|name="password"\|/login' "$AI_HTML"; then
  echo "FAIL: auth cookie is not logged in; received login page."
  exit 1
fi
CSRF=$(python3 - "$AI_HTML" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', text)
if not m:
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', text)
print(m.group(1) if m else "")
PY
)
if [ -z "$CSRF" ]; then
  echo "FAIL: CSRF token not found on authenticated page."
  exit 1
fi
echo "  CSRF token found."

echo "[2] Run non-streaming App Engine setup chat..."
REQ_JSON="$TMP_DIR/chat_request.json"
RESP_JSON="$TMP_DIR/chat_response.json"
python3 - "$REQ_JSON" "$SESSION_ID" "$TARGET_DOMAIN" "$TARGET_REPO" "$TASK_TYPE" <<'PY'
import json, sys
path, session_id, domain, repo, task_type = sys.argv[1:]
payload = {
    "session_id": session_id,
    "stream": False,
    "task_type": task_type,
    "message": f"Please analyze and configure this application for domain {domain}:\n{repo}",
    "context": f"App Engine setup target domain: {domain}\nSelected repository: {repo}",
}
open(path, "w", encoding="utf-8").write(json.dumps(payload))
PY
HTTP_CODE=$(curl -sS -m "$TIMEOUT_SECONDS" -w '%{http_code}' -o "$RESP_JSON" \
  "${AUTH_ARGS[@]}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$CHAT_URL" \
  --data-binary "@$REQ_JSON")
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: chat endpoint returned HTTP $HTTP_CODE"
  python3 -m json.tool "$RESP_JSON" 2>/dev/null || cat "$RESP_JSON"
  exit 1
fi

PLAN_ID=$(python3 - "$RESP_JSON" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
text = data.get("response") or ""
bad = [
    "No reviewed setup plan was created",
    "The reviewed setup plan could not be created",
    "Nothing was applied",
    "provider stopped before creating",
]
for marker in bad:
    if marker in text:
        print("")
        raise SystemExit(f"FAIL_MARKER:{marker}")
m = re.search(r"\[ACTION:APP_SETUP_PLAN:(plan_[0-9a-f]{16})\]", text)
if m:
    print(m.group(1))
PY
) || {
  echo "FAIL: setup chat did not create a usable reviewed plan."
  python3 -m json.tool "$RESP_JSON" 2>/dev/null || cat "$RESP_JSON"
  exit 1
}
if [ -z "$PLAN_ID" ]; then
  echo "FAIL: APP_SETUP_PLAN action tag missing from chat response."
  python3 -m json.tool "$RESP_JSON" 2>/dev/null || cat "$RESP_JSON"
  exit 1
fi
echo "  Plan created: $PLAN_ID"

echo "[3] Fetch and validate action plan payload..."
PLAN_JSON="$TMP_DIR/plan.json"
HTTP_CODE=$(curl -sS -m 30 -w '%{http_code}' -o "$PLAN_JSON" \
  "${AUTH_ARGS[@]}" \
  -H "Accept: application/json" \
  "$BASE/plugins/ai_helper/api/action-plans/$PLAN_ID")
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: action plan fetch returned HTTP $HTTP_CODE"
  python3 -m json.tool "$PLAN_JSON" 2>/dev/null || cat "$PLAN_JSON"
  exit 1
fi
python3 - "$PLAN_JSON" "$TARGET_DOMAIN" "$TARGET_REPO" <<'PY'
import json, re, sys
path, domain, repo = sys.argv[1:]
data = json.load(open(path, encoding="utf-8"))
plan = data.get("plan") or {}
payload = plan.get("payload") or {}
action_type = plan.get("action_type")
if action_type not in {"app_install", "stack_install", "official_stack_install"}:
    raise SystemExit(f"FAIL: unexpected action_type {action_type!r}")
if plan.get("status") != "awaiting_approval":
    raise SystemExit(f"FAIL: plan status is {plan.get('status')!r}, expected awaiting_approval")
payload_text = json.dumps(payload, sort_keys=True)
if domain and domain not in payload_text:
    raise SystemExit(f"FAIL: target domain {domain!r} missing from plan payload")
for forbidden in ("compose:", "docker_compose", "/var/run/docker.sock", "privileged", "host.docker.internal"):
    if forbidden in payload_text:
        raise SystemExit(f"FAIL: unsafe field/text leaked into plan payload: {forbidden}")
if re.search(r"(SECRET|PASSWORD|TOKEN|KEY)[A-Z0-9_]*\s*[=:]\s*[A-Za-z0-9+/_.~-]{24,}", payload_text):
    raise SystemExit("FAIL: payload appears to contain a raw secret value")
if "plausible/analytics" in repo.lower() and action_type not in {"stack_install", "official_stack_install"}:
    raise SystemExit("FAIL: Plausible must produce a restricted stack plan, not a single-app plan")
if action_type in {"stack_install", "official_stack_install"}:
    services = payload.get("services") or []
    if len(services) < 2:
        raise SystemExit("FAIL: stack plan has fewer than two services")
    manifest = payload.get("stack_manifest") or {}
    if not manifest:
        raise SystemExit("FAIL: stack plan missing persisted manifest")
    if not payload.get("manifest_hash"):
        raise SystemExit("FAIL: stack plan missing manifest hash")
print(f"  Plan OK: action_type={action_type}, services={payload.get('services') or 'single-app'}")
PY

if [ "$APPLY" != "1" ]; then
  echo "[4] APPLY=0, skipping deployment queue step."
  echo "=== A10 PASSED: reviewed setup plan is created and safe ==="
  exit 0
fi

echo "[4] Deploy reviewed setup plan..."
DEPLOY_JSON="$TMP_DIR/deploy.json"
HTTP_CODE=$(curl -sS -m 120 -w '%{http_code}' -o "$DEPLOY_JSON" \
  "${AUTH_ARGS[@]}" \
  -H "Accept: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$BASE/plugins/railpack_apps/deploy-reviewed-plan/$PLAN_ID")
if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: deploy-reviewed-plan returned HTTP $HTTP_CODE"
  python3 -m json.tool "$DEPLOY_JSON" 2>/dev/null || cat "$DEPLOY_JSON"
  exit 1
fi
python3 - "$DEPLOY_JSON" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
if data.get("status") != "ok":
    raise SystemExit(f"FAIL: deployment response status is {data.get('status')!r}")
if not data.get("app_id") or not data.get("deployment_id"):
    raise SystemExit("FAIL: deployment response missing app_id/deployment_id")
print(f"  Deployment queued: app_id={data['app_id']} deployment_id={data['deployment_id']}")
PY

echo "=== A10 PASSED: reviewed setup deployed/queued ==="
