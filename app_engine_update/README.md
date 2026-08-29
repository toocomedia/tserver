# Dynamic App Engine & AI Helper Overhaul

This directory contains the specifications, architectural rules, and phased implementation roadmap for overhauling the App Engine and AI Helper.

---

## Key Pillars of the New Architecture

1. **No Static Templates & No Hardcoding**:
   - The AI Assistant dynamically synthesizes declarative **`AppSpec`** definitions for any application (single container or multi-tier stack).
   - Zero hardcoded framework names (`wordpress`, `shynet`, `django`) in Python services or database columns.
2. **Symbolic Secrets with Zero AI Access**:
   - The AI only declares symbolic requirements (e.g. `{MYSQL_ROOT_PASSWORD}`, `{APP_SECRET}`).
   - The server-side secret vault generates and binds them to containers without the AI ever seeing plaintext passwords.
3. **Step-by-Step Questionnaire UI/UX**:
   - When the AI needs input, it presents an interactive questionnaire window in chat—**one question per screen**.
   - Questions provide recommended/optimal defaults pre-selected, with a one-click **Continue** or **Skip** button.
   - Prevents AI drift and keeps the conversation laser-focused.
4. **App Page Draft Plan Persistence**:
   - Plans are saved in the database and displayed as a persistent **Draft Plan card** on the App Engine page (not lost in chat history).
   - User can review services, ports, volumes, and deploy with one click.
5. **Container Diagnostics & Patch Loop**:
   - Inspects failed container logs and resource metrics, diagnoses root causes, proposes a surgical patch to the `AppSpec`, and redeploys safely.

---

## Documents

- [01_ARCHITECTURE_RULES.md](file:///c:/Users/riadh/Desktop/srv-t/app_engine_update/01_ARCHITECTURE_RULES.md) — Core architectural and security rules.
- [02_DYNAMIC_APP_SPEC_AND_UI_SPEC.md](file:///c:/Users/riadh/Desktop/srv-t/app_engine_update/02_DYNAMIC_APP_SPEC_AND_UI_SPEC.md) — Technical specifications for dynamic `AppSpec`, symbolic secrets, chat questionnaire UI, and App page draft cards.
- [03_IMPLEMENTATION_PHASES.md](file:///c:/Users/riadh/Desktop/srv-t/app_engine_update/03_IMPLEMENTATION_PHASES.md) — 7 phased implementation steps.
