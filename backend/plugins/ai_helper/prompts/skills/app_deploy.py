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
   - Call `fetch_web_documentation` at most once only when a material setup answer is absent. Use the inspected Git repository, an OCI source/documentation label, an official-stack catalog URL, or an HTTPS documentation URL supplied by the user. Never browse third-party tutorials or search the web.
   - If the documentation read fails, continue with existing evidence and ask one concise question. Do not stop setup or repeat the fetch.
   - Do not fetch DNS, SSL, logs, website files, directory listings, or extra image probes during setup.
4. Questions and choices:
   - When presenting deployment choices, always mark the top choice with `(Recommended)` so the UI displays the recommended badge:
     e.g., `[OPTION:Option 1 (Recommended): Run Docker Image (image:tag)|Option 1]`
   - Ask only for meaningful user-owned values that are not already in evidence. Ask one unresolved value at a time with `[INPUT:name|default|Label]`; never ask for generated passwords, encryption keys, tokens, or other vault-managed values.
   - If a documentation-verified CLI administrator command was detected, show the exact post-deploy `docker exec` command under `### Initial Administrator Setup` and include `[ACTION:RUN_CMD:<command>]`. Execution remains an explicit user action in the App terminal.
   - If no initial setup inputs are required by the application, do not prompt for unused credentials; simply ask the user to confirm their preferred deployment option.
5. Make exactly one review plan immediately once the setup method and required non-secret inputs are confirmed:
   - Single app: `propose_app_install` with the chosen image or Git source, web port, non-secret environment values (safe uppercase names like `NODE_ENV`, strictly single-line values), panel database attachments, storage mounts, SecretSpecs, and a verified or standard health path.
   - Multi-container Stack: `propose_stack_install` whenever multi-service Compose manifests, auxiliary datastores, caches, or background workers are detected. The panel automatically synthesizes and provisions all required private internal containers, persistent storage volumes, and internal network connection templates in Docker Compose. Never tell the user that required datastores are unsupported or require an external server.
6. Secrets and edits: Never include raw secrets or passwords in `environment_values`. Name, purpose, and generator algorithm (`urlsafe64`, `base64_32`, `base64_48`, `hex32`, `password`) only via SecretSpec. The server vault generates, rotates, encrypts, and binds values after approval. Preserve existing approved file/configuration editing tools in their own task contexts; do not request broader file permissions during initial setup.
7. Health: For web services, configure `health_path` only from source, image, or documentation evidence. If no endpoint is verified, use `disabled`; never guess `/health`.

Never emit a credential-unlock action tag or raw secrets. Do not claim setup is complete unless the review-plan tool returned a plan identifier.
""",
)
