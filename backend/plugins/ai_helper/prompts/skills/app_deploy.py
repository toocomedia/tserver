"""Safe App Engine planning prompt."""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="app_deploy",
    task_types=["app_deploy", "app_install", "setup_app"],
    prompt="""### App Engine setup plan
Never deploy, apply, reveal or generate secret values.

1. Source inspection facts and panel capabilities are pre-injected in context when a repository or image is supplied. If source inspection is already in context, do NOT call inspection tools again. If not in context, call `inspect_app_source` exactly once.
2. Do not fetch external documentation, DNS, SSL, logs, website files, directory listings, or extra image probes during setup.
3. Interactive Source & Setup Decision:
   - If `official_image_recommendation` is detected in inspection and the user has not already chosen a deployment method:
     * DO NOT call `propose_app_install` or `propose_stack_install` yet.
     * Explain why the official image is recommended, and output the choice tags:
       [OPTION:Option 1 (Recommended): Official Docker Image (Image name & port)|Option 1]
       [OPTION:Option 2: Build from Git source code|Option 2]
   - If `documentation_evidence` in inspection facts indicates initial setup requirements (e.g., admin registration command like `registeradmin`, database selection, or email settings):
     * Explain the installation steps found in the documentation snippet.
     * Highlight any user inputs needed (such as Admin Email for account creation).
4. Make exactly one review plan immediately once the setup method is confirmed (or if no official image recommendation exists):
   - Single app: `propose_app_install` with the chosen image or Git source, web port, non-secret environment values (safe uppercase names like `NODE_ENV`, strictly single-line values), panel database attachments, storage mounts, SecretSpecs, and a verified or standard health path.
   - Multi-container Stack: `propose_stack_install` whenever multi-service Compose manifests, auxiliary datastores, caches, or background workers are detected. The panel automatically synthesizes and provisions all required private internal containers, persistent storage volumes, and internal network connection templates in Docker Compose. Never tell the user that required datastores are unsupported or require an external server.
5. Secrets: Never include raw secrets or passwords in `environment_values`. Name, purpose, and generator algorithm (`urlsafe64`, `base64_32`, `base64_48`, `hex32`, `password`) only via SecretSpec. The server generates, encrypts, and binds values securely upon deployment. External connection URLs are user-entered only. Never emit a credential-unlock action tag or output raw secrets in chat.
6. Health: For web services, configure `health_path` (`/api/health`, `/health`, or `/`) and startup timeout so private readiness is verified and diagnostics are logged during deployment.

Never emit a credential-unlock action tag or raw secrets. Do not claim setup is complete unless the review-plan tool returned a plan identifier.
""",
)
