# Implementation Roadmap: Dynamic App Engine & AI Overhaul

This roadmap defines the ordered execution phases for implementing the dynamic AI-driven App Engine, secret vault isolation, interactive chat UI, and app-page plan persistence.

## Phase Tracking & Status Dashboard

| Phase | Description | Status | Test Gate Status | Completed Date |
|:---|:---|:---:|:---:|:---:|
| **Phase 1** | Core Cleanup & Hardcoded Logic Removal | **COMPLETED** | Passed (11/11 & 22/22 OK) | 2026-08-29 |
| **Phase 2** | Dynamic Compose `AppSpec` Engine & Symbolic Secrets | **COMPLETED** | Passed (6/6 & 4/4 OK) | 2026-08-29 |
| **Phase 3** | UI/UX — Step-by-Step Chat Questionnaire System | **COMPLETED** | Passed (9/9 OK) | 2026-08-29 |
| **Phase 4** | UI/UX — "Apply Plan" Client Handoff & Persistent Drafts | **COMPLETED** | Passed (35/35 OK) | 2026-08-29 |
| **Phase 5** | Container Diagnostic, Patch & Redeploy Loop | **COMPLETED** | Passed (3/3 & 22/22 OK) | 2026-08-29 |
| **Phase 6** | AI Latency Optimization (Sub-2s Response) | **COMPLETED** | Passed (28/28 & 22/22 OK) | 2026-08-29 |

Every phase adheres to **Rule 8 (Strict Phase Gate & Verification Protocol)**:
1. Scope preview before touching files.
2. Automated test gate with exact command.
3. Practical VPS / browser verification check.
4. Mandatory status update in roadmap upon completion.
5. Mandatory stop and wait for explicit approval before proceeding to the next phase.

---

## Phase 1: Core Cleanup & Hardcoded Logic Removal `[COMPLETED]`

### Status: COMPLETED
- **Completed**: 2026-08-29
- **Automated Test Gate**: Passed (0 errors, 0 failures)
  - `python -m unittest backend/tests/test_railpack_apps_command.py` (11 tests OK)
  - `python -m unittest backend/tests/test_ai_app_setup.py` (22 tests OK)
  - Verified on local and remote VPS environment.

### 1. Scope Preview (Files to Touch)
- `backend/services/container_app_wordpress_service.py` (Deprecated legacy runner facade)
- `backend/plugins/railpack_apps/documentation_service.py` (Declarative recipes registry)
- `backend/plugins/railpack_apps/command_service.py` (Declarative quick command recipes registry)
- `backend/models/container_app.py` (Marked custom WordPress columns as deprecated)

### 2. Implementation Actions
- Delete `container_app_wordpress_service.py`; any container execution runs through generic `command_service.py`.
- Replace hardcoded `if "shynet"`, `elif "wordpress"`, etc., in `documentation_service.py` and `command_service.py` with generic container command execution.
- Deprecate single-app table columns in `models/container_app.py` without breaking existing database tables.

### 3. Automated Test Gate (Command to Run)
```bash
python -m unittest backend/tests/test_railpack_apps_command.py
python -m unittest backend/tests/test_ai_app_setup.py
```
*Criteria*: All tests must pass with `OK` (0 errors, 0 failures).

### 4. Practical VPS / Real Verification Check
- Check the App Engine management page in browser (`/plugins/railpack_apps/`): verify app detail pages and terminal command lists load with zero errors.

### 5. Approval Gate
- **STOP & WAIT**. Do nothing until user confirms:
  > *"Phase 1 confirmed, proceed to Phase 2"*

---

## Phase 2: Dynamic Compose `AppSpec` Engine & Symbolic Secrets `[COMPLETED]`

### Status: COMPLETED
- **Completed**: 2026-08-29
- **Automated Test Gate**: Passed (0 errors, 0 failures)
  - `python -m unittest backend/tests/test_apps_engine_safe_deployment.py` (6 tests OK)
  - `python -m unittest backend/tests/test_template_export_and_normalization.py` (4 tests OK)
  - Full regression suite passed (38 tests OK across commands, UI, and apps).

### 1. Scope Preview (Files to Touch)
- `backend/services/apps_engine/app_spec.py`
- `backend/services/apps_engine/security_policy.py`
- `backend/services/apps_engine/reviewed_setup_deploy.py`
- `backend/services/official_stacks/compose_runtime.py`
- `backend/plugins/railpack_apps/static/js/railpack-app-create.js`
- `backend/tests/test_template_export_and_normalization.py`

### 2. Implementation Actions
- Ensure `security_policy.py` allows dynamic multi-container and single-container specifications synthesized by AI.
- Ensure the server secret vault securely resolves symbolic keys (e.g. `{MYSQL_ROOT_PASSWORD}`, `{APP_SECRET}`) across linked containers (web + db) without exposing plaintext to AI.
- Ensure `compose_runtime.py` renders unified Compose projects (`srv-stack-{id}`) with private bridge networks and named volumes.
- Removed hardcoded framework checks in `compose_runtime.py`.

### 3. Automated Test Gate (Command to Run)
```bash
python -m unittest backend/tests/test_apps_engine_safe_deployment.py
python -m unittest backend/tests/test_template_export_and_normalization.py
```
*Criteria*: All tests must pass with `OK`.

### 4. Practical VPS / Real Verification Check
- Verify that a Compose JSON payload with symbolic secrets renders valid docker-compose configuration files in `/etc/srv-stack/` with correct secret substitution in `.env`.

### 5. Approval Gate
- **STOP & WAIT**. Do nothing until user confirms:
  > *"Phase 2 confirmed, proceed to Phase 3"*

---

## Phase 3: UI/UX — Step-by-Step Chat Questionnaire System `[COMPLETED]`

### Status: COMPLETED
- **Completed**: 2026-08-29
- **Automated Test Gate**: Passed (0 errors, 0 failures)
  - `python -m unittest backend/tests/test_ai_setup_interview.py` (9 tests OK)
  - Regression suites passed (34 tests OK).

### 1. Scope Preview (Files to Touch)
- `backend/plugins/ai_helper/static/js/modules/chat_setup_interview.js`
- `backend/plugins/ai_helper/services/chat.py`
- `backend/plugins/ai_helper/prompts/skills/app_deploy.py`

### 2. Implementation Actions
- Update `chat_setup_interview.js` to render questions **one screen at a time**: Step X of Y.
- Pre-select recommended choices for `[OPTION:label (Recommended)|reply]`.
- Provide sensible defaults for `[INPUT:key|default|label|optional]` with a clean **Skip** button for optional inputs.
- Make the prompt laser-focused: only ask questions when critical facts (like domain) are missing; submit all answers in one combined payload.

### 3. Automated Test Gate (Command to Run)
```bash
python -m unittest backend/tests/test_ai_setup_interview.py
```
*Criteria*: All 9 interview tests pass with `OK`.

### 4. Practical VPS / Real Verification Check
- Open AI Helper chat in browser: type *"I want to install WordPress"*.
- Verify the interactive questionnaire appears one step at a time with pre-filled defaults and Skip buttons.
- Confirm keyboard **Enter** advances steps cleanly.

### 5. Approval Gate
- **STOP & WAIT**. Do nothing until user confirms:
  > *"Phase 3 confirmed, proceed to Phase 4"*

---

## Phase 4: UI/UX — "Apply Plan" Client Handoff & Persistent Drafts `[COMPLETED]`

### Status: COMPLETED
- **Completed**: 2026-08-29
- **Automated Test Gate**: Passed (0 errors, 0 failures)
  - `python -m unittest backend/tests/test_railpack_apps_ui.py backend/tests/test_ai_app_setup.py` (35 tests OK)
  - Full regression suite passed (30 tests OK).

### 1. Scope Preview (Files to Touch)
- `backend/plugins/railpack_apps/static/js/railpack-app-create.js`
- `backend/plugins/railpack_apps/router_create.py`
- `backend/plugins/railpack_apps/templates/railpack_apps_create.html`

### 2. Implementation Actions
- Server pre-hydration: `router_create.py` inspects `?plan=` query param and injects `initial_plan` into the page context.
- Template adds `<script id="initial-app-plan">` and a responsive `[data-reviewed-plan-banner]` with plan badges and a primary "Deploy Now" button.
- `railpack-app-create.js` saves plan draft to `sessionStorage` (`srv_app_plan_draft`) and restores it on page refresh.
- Provides `clearPlanDraft()` to discard draft and start clean.
- Automatically clears saved draft from `sessionStorage` once deployment begins.

### 4. Practical VPS / Real Verification Check
- In browser: Open chat, complete questions, click **"Apply Plan"**.
- Verify chat window closes automatically, App Engine form is 100% filled out with AI's settings, and the **"Deploy"** button is highlighted for review.

### 5. Approval Gate
- **STOP & WAIT**. Do nothing until user confirms:
  > *"Phase 4 confirmed, proceed to Phase 5"*

---

## Phase 5: Container Diagnostic, Patch & Redeploy Loop `[COMPLETED]`

### Status: COMPLETED
- **Completed**: 2026-08-29
- **Automated Test Gate**: Passed (0 errors, 0 failures)
  - `python -m unittest backend/tests/test_ai_app_fix.py` (3 tests OK)
  - `python -m unittest backend/tests/test_ai_app_setup.py` (22 tests OK)
  - Full regression suite passed (46 tests OK).

### 1. Scope Preview (Files to Touch)
- `backend/plugins/ai_helper/prompts/skills/container_error_resolver.py`
- `backend/services/apps_engine/deployment_drafts.py`
- `backend/tests/test_ai_app_fix.py`

### 2. Implementation Actions
- Added `app_debug` task type and refined classification heuristics in `container_error_resolver.py`.
- Enhanced `deployment_drafts.py` alias normalization (`port`, `web_port`, `web_health_path`, `start_command`).
- Created dedicated unit and integration suite in `backend/tests/test_ai_app_fix.py` validating patch proposals, exact diff representation, and non-destructive redeployment via `deploy_plan`.

### 3. Automated Test Gate (Command to Run)
```bash
python -m unittest backend/tests/test_ai_app_fix.py
```
*Criteria*: All tests pass with `OK`.

### 4. Practical VPS / Real Verification Check
- Simulate a container failure (e.g. invalid port or memory limit), ask AI in chat to diagnose it, and verify the patch plan appears on the App detail page for one-click recovery.

### 5. Approval Gate
- **STOP & WAIT**. Do nothing until user confirms:
  > *"Phase 5 confirmed, proceed to Phase 6"*

---

## Phase 6: AI Latency Optimization (Sub-2s Response) `[COMPLETED]`

### Status: COMPLETED
- **Completed**: 2026-08-29
- **Automated Test Gate**: Passed (0 errors, 0 failures)
  - `python -m unittest backend/tests/test_ai_helper.py` (28 tests OK)
  - `python -m unittest backend/tests/test_ai_app_setup.py` (22 tests OK)
  - Full regression suite passed (46 tests OK across all phases).

### 1. Scope Preview (Files to Touch)
- `backend/services/container_app_image_inspect_service.py`
- `backend/plugins/ai_helper/engine_streaming.py`
- `backend/plugins/ai_helper/services/chat.py`

### 2. Implementation Actions
- Inspect local image cache first via `docker inspect` before network pull, skipping pull entirely if present locally.
- Added in-memory TTL caching (`_INSPECT_CACHE`) with 15-minute validity for instant sub-millisecond repeat queries.
- Added real-time `<think>...</think>` streaming tag emission for reasoning models (e.g. DeepSeek-R1) and Anthropic thinking deltas.
- Fixed schema normalization for OpenAI-compatible streaming clients to preserve native `role: "tool"` responses.

### 3. Automated Test Gate (Command to Run)
```bash
python -m unittest backend/tests/test_ai_helper.py backend/tests/test_ai_app_setup.py
```
*Criteria*: All tests pass with `OK`.

### 4. Practical VPS / Real Verification Check
- Measure response latency in browser: verify AI responds and streams tokens in under 2 seconds without freezing.

### 5. Final Status
- **ALL 6 PHASES COMPLETED AND VERIFIED!**
