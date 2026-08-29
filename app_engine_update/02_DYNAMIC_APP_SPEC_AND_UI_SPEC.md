# Dynamic AppSpec & UI/UX Technical Specification

This document specifies the dynamic data contracts, secret vault integration, interactive questionnaire UI, and the App page persistent draft plan card.

---

## 1. Dynamic `AppSpec` Contract (Synthesized by AI)

Instead of static YAML files stored in the repository, the AI dynamically synthesizes an `AppSpec` JSON payload when proposing an application or stack:

```json
{
  "name": "wordpress",
  "display_name": "WordPress with MariaDB",
  "web_service_name": "wordpress",
  "web_port": 80,
  "services": {
    "wordpress": {
      "image": "wordpress:php8.3-apache",
      "port": 80,
      "depends_on": ["db"],
      "environment": {
        "WORDPRESS_DB_HOST": "db:3306",
        "WORDPRESS_DB_USER": "wordpress",
        "WORDPRESS_DB_NAME": "wordpress"
      },
      "volumes": [
        {
          "name_suffix": "wp-content",
          "container_mount_path": "/var/www/html/wp-content"
        }
      ]
    },
    "db": {
      "image": "mariadb:11",
      "port": 3306,
      "environment": {
        "MYSQL_DATABASE": "wordpress",
        "MYSQL_USER": "wordpress"
      },
      "volumes": [
        {
          "name_suffix": "db-data",
          "container_mount_path": "/var/lib/mysql"
        }
      ],
      "health_check": {
        "probe_type": "command",
        "command": ["mariadb-admin", "ping", "-h", "localhost"]
      }
    }
  },
  "required_secrets": [
    {
      "key": "MYSQL_ROOT_PASSWORD",
      "purpose": "MariaDB Root Password",
      "generator": "password",
      "service_name": "db",
      "environment_key": "MYSQL_ROOT_PASSWORD"
    },
    {
      "key": "WORDPRESS_DB_PASSWORD",
      "purpose": "WordPress Database Password",
      "generator": "password",
      "service_name": "wordpress",
      "environment_key": "WORDPRESS_DB_PASSWORD"
    },
    {
      "key": "WORDPRESS_DB_PASSWORD",
      "purpose": "MariaDB User Password Binding",
      "generator": "password",
      "service_name": "db",
      "environment_key": "MYSQL_PASSWORD"
    }
  ],
  "url_templates": {}
}
```

---

## 2. Secret Vault Symbolic Binding Model

### How Secrets Work:
1. **AI Never Reads or Generates Secrets**:
   - The AI only writes the requirement: `{"key": "APP_SECRET", "generator": "urlsafe64", "service_name": "web"}`.
   - Supported generators: `password` (alphanumeric secure pass), `urlsafe64`, `hex32`, `hex64`, `base64_32`.
2. **Secret Reuse Across Containers**:
   - Notice in the example above: `WORDPRESS_DB_PASSWORD` is generated once.
   - It is bound to `wordpress` as `WORDPRESS_DB_PASSWORD` and to `db` as `MYSQL_PASSWORD`.
   - Both containers receive the exact same generated password in their private `.env` files.
   - **The AI never sees the password value.**

---

## 3. Chat UI: Step-by-Step Questionnaire System

When the AI needs user input (e.g. domain, site title, database mode), it emits interactive tags. The chat frontend renders these in a dedicated, step-by-step card.

### Tag Formats:
- **Options / Choices**:
  ```text
  [OPTION:Label (Recommended)|choice_key]
  ```
- **Text / Configuration Inputs**:
  ```text
  [INPUT:key|default_value|Friendly Label|required_or_optional]
  ```

### Interactive UI Behavior:
1. **Step-by-Step Window**:
   - The questions are presented one screen at a time: `Step 1 of 3: Target Domain`, `Step 2 of 3: Site Title`, `Step 3 of 3: Database`.
2. **Optimal Defaults**:
   - Recommended options are pre-selected.
   - Input fields have sensible defaults pre-filled (e.g. `My Awesome Site`).
3. **Continue & Skip Actions**:
   - User can press **Enter** or click **Continue** to accept the optimal default.
   - If an input is marked `optional`, a **Skip** button is shown.
4. **Combined Submission**:
   - When the user finishes the last step, the modal collects all answers into a single message:
     ```text
     Setup interview answers:
     domain_name: myblog.com
     site_title: My Tech Blog
     database: docker
     ```
   - The AI receives all answers at once and immediately outputs the completed deployment plan card.

---

## 4. "Apply Plan" & App Page Review Flow

### The User Experience Flow:
Instead of forcing the user to deploy blindly from chat, the AI outputs a single primary action button at the end of the proposal: **"Apply Plan"**.

```text
┌─────────────────────────────────────────────────────────────┐
│ 📦 Deployment Proposal: WordPress with MariaDB              │
│ Domain: blog.example.com · 2 Services (web, db) · 2 Volumes │
│                                                             │
│                    [ Apply Plan ]                           │
└─────────────────────────────────────────────────────────────┘
```

### What Happens When User Clicks "Apply Plan":
1. **Applies to App Engine Inputs**:
   - The client invokes `applyAiAppPlan(planData)`.
   - Populates the target domain dropdown and confirms SSL request.
   - Sets source type (Git URL + branch or Docker image reference).
   - Fills internal port, health check endpoint, and custom start commands.
   - Populates environment variables into the configuration rows.
   - Toggles and binds required databases (PostgreSQL, MariaDB, Redis, MongoDB).
   - Configures storage mount paths and named volumes.
   - Renders the multi-service Compose configuration panel.
2. **Closes the Chat Window**:
   - The chat modal automatically closes (`window.AiHelper.close()`).
3. **Focuses the Final Review Step**:
   - The App Engine wizard advances to the final review step.
   - All settings configured by the AI are clearly visible in the panel UI.
   - The primary **"Deploy Stack"** (or **"Deploy App"**) button is prominently displayed and highlighted.
4. **User Control**:
   - The user can visually review every port, volume, and environment key configured by the AI.
   - The user can make manual tweaks if desired, or simply click **"Deploy Stack"** to launch.

### Persistent Draft on App Page (If User Leaves Chat):
- If the user closes the chat before clicking apply, or navigates away, the unapplied plan remains saved in the database as an `AiActionPlan`.
- The App Engine list and create pages display a persistent **"Draft Plan Ready"** banner:
  ```text
  ┌─────────────────────────────────────────────────────────────┐
  │ 🚀 Ready to Review: WordPress with MariaDB                  │
  │ Created by AI Assistant for blog.example.com                │
  │                                                             │
  │          [ Discard Draft ]   [ Apply & Review ]             │
  └─────────────────────────────────────────────────────────────┘
  ```

---

## 5. Diagnostic, Patch & Redeploy Loop

When an existing app encounters errors:

1. **Diagnosis**:
   - User opens chat with context of the failed app.
   - AI runs `get_app_logs(app_id)` to read container error output.
   - AI runs `get_app_engine_diagnostics(app_id)` to check container exit codes, memory/CPU spikes, and port bindings.
2. **Root Cause Analysis**:
   - AI explains the specific error (e.g. "PostgreSQL connection refused: database container ran out of memory").
3. **Patch Proposal**:
   - AI calls `propose_container_app_patch(app_id=12, patch={...})` modifying only the affected parameters (e.g. memory limit from 512MB to 1024MB or fixing database host).
4. **App Page Review & Redeployment**:
   - A **Patch Draft Card** appears on the App's detail page.
   - User clicks **Apply & Redeploy** -> App Engine safely stops the container, updates the Compose configuration, and restarts the service cleanly.
