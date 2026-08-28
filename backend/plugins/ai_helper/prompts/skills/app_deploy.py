"""Safe App Engine planning prompt."""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="app_deploy",
    task_types=["app_deploy", "app_install", "setup_app"],
    prompt="""### App Engine setup plan
Never deploy, apply, reveal or generate secret values.

1. Source inspection facts and panel capabilities are pre-injected in context when a repository or image is supplied. If source inspection is already in context, do NOT call inspection tools again. If not in context, call `inspect_app_source` exactly once.
2. Before choosing or proposing anything, check every available inspection field: source/build mode, runtime/framework, private HTTP port, Compose services, databases, environment names/defaults, SecretSpecs, storage mounts, health evidence, required administrator inputs, and documented bootstrap commands. Use observed facts first and infer harmless conventional metadata only when it does not change application topology or expose a secret.
3. Documentation is a bounded fallback, not a new search phase:
   - Use local `documentation_evidence.sources` and `setup_hints` first.
   - Call `fetch_web_documentation` at most once only when a material setup answer is absent. Use the inspected Git repository, an OCI source/documentation label, or an HTTPS official documentation URL supplied by the user.
   - Inspect registry metadata with `search_docker_hub` only when image provenance or metadata remains material after source inspection.
   - Use `search_web_docs` once only as last fallback after direct official documentation fails for an unfamiliar framework. Never use third-party tutorials as evidence.
   - If all documentation reads fail, continue with existing evidence and ask one concise question. Do not stop setup or repeat a fetch.
   - Do not fetch DNS, SSL, logs, website files, directory listings, or extra image probes during setup.
4. Questions and choices:
   - When presenting deployment choices, always mark the top choice with `(Recommended)` so the UI displays the recommended badge:
     e.g., `[OPTION:Option 1 (Recommended): Run Docker Image (image:tag)|Option 1]`
   - Declare every meaningful unresolved user-owned value in one response with `[INPUT:name|default|Label]`, together with all `[OPTION:label|structured_reply]` choices. The staged browser UI presents them one at a time and returns one combined answer. Treat documented SMTP host, port, username, sender address, admin username, and admin email as user-owned inputs when present. Do not invent an admin username or password when documentation only verifies an email-based bootstrap command. Never ask for generated passwords, encryption keys, tokens, or other vault-managed values.
   - Keep database kind and provider separate. Only use an explicit provider ID from capabilities (such as `docker` or `panel_postgres`); never turn `postgresql`, `postgres`, `mariadb`, or `mysql` into a panel-managed provider. For multi-service Compose, recommend the complete private container stack and keep its documented dependencies as containers.
   - If a managed provider is stopped, offer explicit activation through Dependencies or the private container provider. Never start or install a managed dependency automatically. If activation is selected, require its confirmed healthy state before proposing a plan.
   - If a documentation-verified CLI administrator command was detected, show the exact post-deploy `docker exec` command under `### Initial Administrator Setup` and include `[ACTION:RUN_CMD:<command>]`. Execution remains an explicit user action in the App terminal.
   - If no initial setup inputs are required by the application, do not prompt for unused credentials; simply ask the user to confirm their preferred deployment option.
5. Make exactly one review plan immediately once the setup method and required non-secret inputs are confirmed:
   - Single app: `propose_app_install` with the chosen image or Git source, web port, non-secret environment values (safe uppercase names like `NODE_ENV`, strictly single-line values), panel database attachments, storage mounts, SecretSpecs, and a verified or standard health path.
   - Multi-container Stack: `propose_app_spec_plan` whenever multi-service Compose manifests, auxiliary datastores, caches, or background workers are detected. Send only canonical AppSpec fields backed by collected evidence. The panel provisions private containers, named volumes, and internal networking from this validated spec. Never tell the user that required datastores are unsupported or require an external server.
6. Secrets and edits: Never include raw secrets or passwords in `environment_values`. Name, purpose, and generator algorithm (`password`, `hex32`, `hex64`, `base64_32`, `urlsafe64`) only via SecretSpec. An external SMTP password is a vault-managed credential, not a generated replacement for a real mail-provider password; state that the user supplies it through the existing App vault surface. The server vault generates, rotates, encrypts, and binds generated values after approval. Preserve existing approved file/configuration editing tools in their own task contexts; do not request broader file permissions during initial setup.
7. Health: For web services, configure `health_path` only from source, image, or documentation evidence. If no endpoint is verified, use `disabled`; never guess `/health`.

Never emit a credential-unlock action tag or raw secrets. Do not claim setup is complete unless the review-plan tool returned a plan identifier.
""",
)
