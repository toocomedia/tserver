# App Engine & AI Helper Architecture: Core Rules & UI/UX Standards

These rules define the architecture for the dynamic AI-driven App Engine and the interactive chat UI/UX.

---

## 1. Zero Hardcoded Code Rule
- **No Application Names in Python Logic**:
  - Never write `if "wordpress" in ...`, `if "shynet" in ...`, `if "django" in ...`, or `if "strapi" in ...` in backend services, routers, models, or serializers.
- **No Static Template Files in Repo**:
  - We do not store static `wordpress.yaml`, `umami.yaml`, etc.
  - The AI Assistant is the **dynamic architect**: it dynamically synthesizes the Compose `AppSpec` based on the user's intent, the Git repository's files, or the Docker image reference.
- **No App-Specific Database Columns**:
  - The database table `container_apps` must never have columns tailored to a single application (e.g. `wordpress_content_volume`, `wordpress_site_title`, etc.).
  - All application configuration belongs in generic configuration JSON, snapshot manifests, or environment variables.
- **No Dedicated Per-App Python Services**:
  - Delete single-app runner services like `container_app_wordpress_service.py`. Any management command (like WP-CLI or Django manage.py) runs through the **generic container command execution service** (`command_service.py`).

---

## 2. Universal Dynamic Compose Engine (`AppSpec`)
- Every application—whether a single container, a LAMP stack, or a complex distributed system—is represented as a validated, declarative **`AppSpec`**.
- Docker Compose (`compose_runtime.py`) is the single execution engine:
  - 1-container apps run as a 1-service Compose project (`srv-stack-{id}`).
  - Multi-container apps run with their linked auxiliary services (PostgreSQL, MariaDB, Redis, etc.).
  - Guarantees isolated private bridge networks (`srv-stack-net-{id}`), named volumes (`srv-stack-{id}-{suffix}`), and resource limits across all apps.

---

## 3. Secret Vault & Password Policy: Zero AI Access
- **Symbolic Secret References**:
  - The AI Assistant never generates, reads, stores, or sees plaintext passwords, secret keys, or private tokens.
  - The AI only declares the **symbolic requirement** and generator type:
    ```json
    {"key": "DB_PASSWORD", "service_name": "db", "generator": "password"}
    ```
- **Reuse Across Services**:
  - The AI can bind the same secret across multiple services without seeing its value.
  - For example, `{DB_PASSWORD}` is generated for the `db` container and automatically referenced in the `web` container's environment (`DATABASE_URL=postgresql://user:{DB_PASSWORD}@db:5432/dbname`).
  - The server-side secret vault securely generates, encrypts, and injects the actual value into container `.env` files at deployment time.

---

## 4. Chat UI/UX: Step-by-Step Questionnaire Modal
- **One Question at a Time**:
  - When the AI needs user input (e.g. target domain, site name, database preference), it presents an interactive questionnaire window in chat.
  - Questions are shown **one per screen** (not a giant wall of chat text).
- **Optimal Defaults & Easy Skip**:
  - Every question provides a recommended/optimal default choice pre-selected.
  - The user can simply click **Continue** (or hit Enter) to accept the optimal default, or click **Skip** for optional settings.
- **No AI Drift / Simple & Focused**:
  - The AI only asks questions if critical information is genuinely missing (e.g. domain name).
  - The AI does NOT output philosophical explanations or long multi-turn commentary.
  - Once inputs are received, the AI produces the final deployment plan card immediately.

---

## 5. App Page Draft Plan Persistence
- **Saved on App Page, Not Lost in Chat**:
  - When the AI generates a plan (`AiActionPlan`), it is saved in the database and displayed directly on the **App Engine page** (or App Create wizard) as a persistent **Draft Plan card**.
  - The user does not need to search through chat history:
    - The App page shows the draft plan with its services, internal ports, storage volumes, and domain route.
    - The user can click **Deploy Plan**, **Edit Plan Settings**, or **Discard Draft** directly on the App page.

---

## 6. Diagnostic, Patch & Redeploy Loop
- When a container fails or reports unhealthy state:
  1. The user (or auto-healing) requests AI diagnosis.
  2. The AI reads container logs via `get_app_logs(app_id)` and diagnostics via `get_app_engine_diagnostics(app_id)`.
  3. The AI diagnoses the root cause (e.g. port mismatch, missing database migration, missing environment variable).
  4. The AI proposes a **patch plan** (`propose_container_app_patch`) modifying only the broken settings in the `AppSpec`.
  5. The patch plan appears on the App page for user review and one-click redeployment.

---

## 7. "Apply Plan" UX: Review-Before-Deploy Flow
- **Single Primary Action in Chat**:
  - At the conclusion of the AI's proposal, the chat outputs one clean action button: **"Apply Plan"**.
- **Seamless Form Population**:
  - Clicking **"Apply Plan"** transfers the AI's synthesized `AppSpec` into all relevant App Engine form inputs (domain, source, port, services, environment variables, storage mounts, database attachments, and secret requirements).
- **Auto-Close Chat & Review Focus**:
  - The chat drawer automatically closes (or collapses).
  - The App Engine wizard advances to the review step, displaying all populated settings in the panel UI.
  - The primary **"Deploy"** button is highlighted so the user can review exactly what the AI configured and click Deploy when satisfied.

---

## 8. Strict Phase Gate & Verification Protocol
- **Rule 8.1: Scope Preview Before Code**:
  - Before making any code modifications for a phase, the assistant must explicitly list the exact 1–2 files that will be touched. No unannounced files may be altered.
- **Rule 8.2: Automated Test Gate for Every Phase**:
  - Every phase must provide a single, copy-pasteable test command (e.g. `python -m unittest backend/tests/...`).
  - The test suite must pass with 0 errors and 0 regressions before the phase is considered done.
- **Rule 8.3: Practical VPS / Browser Verification**:
  - Every phase must define a practical check the user can run on their VPS or test in their browser to verify real-world behavior.
- **Rule 8.4: Mandatory Stop & Wait**:
  - The assistant must **STOP** immediately after completing a phase's verification.
  - The assistant must **NEVER** begin the next phase until the user explicitly replies:
    > *"Phase X confirmed, proceed to Phase Y"*
  - Any unauthorized code modification without prior phase confirmation is strictly prohibited.
- **Rule 8.5: Status Tracking in Roadmap**:
  - Upon completion and verification of each phase, the assistant must immediately update [03_IMPLEMENTATION_PHASES.md](file:///c:/Users/riadh/Desktop/srv-t/app_engine_update/03_IMPLEMENTATION_PHASES.md) marking the phase as `[COMPLETED]` with the date, verified test results, and updated status in the Phase Tracking Dashboard.

