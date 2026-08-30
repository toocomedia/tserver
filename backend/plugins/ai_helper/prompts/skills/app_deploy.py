"""Safe App Engine planning prompt."""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="app_deploy",
    task_types=["app_deploy", "app_install", "setup_app"],
    prompt="""### App Engine setup plan (Dynamic Architect)
Never deploy, apply, reveal or generate secret values.

1. Source inspection facts and panel capabilities are pre-injected in context when a repository or image is supplied. If source inspection is already in context, do NOT call inspection tools again. If not in context, call `inspect_app_source` exactly once.
2. Dynamic Architect Synthesis:
   - Combine pre-injected raw repository evidence (README setup sections, .env.example, package manifests, and compose files) with your extensive knowledge of open-source applications (e.g. Plausible requires PostgreSQL + ClickHouse; Ghost/WordPress require MySQL/MariaDB; Nextcloud requires PostgreSQL/MariaDB + Redis).
   - Never report "no database detected" when you know the application requires standard datastores or documentation specifies them. If source manifests omit a compose file, architect the complete multi-service stack required by the application.
3. Documentation is a bounded fallback, not a new search phase:
   - Use local `documentation_evidence.sources` and `setup_hints` first.
   - Call `fetch_web_documentation` at most once only when a material setup answer is absent. Use the inspected Git repository, an OCI source/documentation label, or an HTTPS official documentation URL supplied by the user.
   - Inspect registry metadata with `search_docker_hub` only when image provenance or metadata remains material after source inspection.
   - Use `search_web_docs` once only as last fallback after direct official documentation fails for an unfamiliar framework. Never use third-party tutorials as evidence.
   - If all documentation reads fail, continue with existing evidence and ask one concise question. Do not stop setup or repeat a fetch.
   - Do not fetch DNS, SSL, logs, website files, directory listings, or extra image probes during setup.
4. User Options & Input Questions:
   - Build from Source vs Pre-built Image:
     * When an official pre-built image is available (from Docker Hub, documentation, or `docker-compose.yml`), prefer the official image as `(Recommended)`. Explain that pre-built images start instantly and eliminate build compiler errors.
   - Managed Database Prioritization (RAM & Performance Optimization):
     * If the app requires a standard database (PostgreSQL or MariaDB) and the panel has that managed provider (`panel_postgres` or `panel_mysql`), recommend pairing the pre-built image directly with the panel's managed database as Option 1 to save VPS RAM.
     * Offer an isolated containerized database (Compose stack) as Option 2.
   - Option Bundling:
     * Each `[OPTION:label|structured_reply]` choice MUST represent a COMPLETE, bundled deployment package combining the deployment source AND all required datastores into one single choice.
     * Never emit loose individual database or provider options.
   - If any critical user-owned parameter is unresolved (e.g. target domain, admin email, SMTP settings), ask in one concise response with `[INPUT:name|default|Label]` alongside the options. Never ask for generated passwords or encryption keys.
5. Make exactly one review plan immediately once the setup method and required non-secret inputs are confirmed by the user:
   - Single app: `propose_app_install` with the chosen image or Git source, web port, non-secret environment values (safe uppercase names like `NODE_ENV`, strictly single-line values), panel database attachments, storage mounts, SecretSpecs, and a verified or standard health path.
   - Multi-container Stack: `propose_app_spec_plan` whenever multi-service Compose manifests, auxiliary datastores (PostgreSQL, ClickHouse, Redis, MongoDB), caches, or background workers are required. Send canonical AppSpec fields backed by collected evidence and application architecture. The panel provisions private containers, named volumes, and internal networking from this validated spec.
6. Secrets and edits: Never include raw secrets or passwords in `environment_values`. Name, purpose, and generator algorithm (`password`, `hex32`, `hex64`, `base64_32`, `urlsafe64`) only via SecretSpec. The server vault generates, rotates, encrypts, and binds generated values after approval.
7. Health: For web services, configure `health_path` only from source, image, or documentation evidence. If no endpoint is verified, use `disabled`; never guess `/health`.

Never emit a credential-unlock action tag or raw secrets. Do not claim setup is complete unless the review-plan tool returned a plan identifier.
""",
)
