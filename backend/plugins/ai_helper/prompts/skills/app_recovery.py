"""App Engine recovery and diagnostic action contract."""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="app_recovery",
    task_types=["app_recovery", "recover_app"],
    prompt="""### App Engine recovery & diagnostic repair
1. Call `get_app_engine_diagnostics` first to inspect build deployment logs, container stderr/stdout, health states, and service dependencies.
2. When root cause is identified (such as missing or invalid environment variables, database URLs, port bindings, or framework secrets on single apps or official stacks):
   - Explicitly list the **files and configuration variables being edited** in chat.
   - Explain the **container lifecycle**: upon applying the fix, existing containers are safely stopped and recreated with `--force-recreate` using the new snapshot, replacing the failed state without deleting persistent volumes.
   - YOU MUST EXECUTE `propose_container_app_patch(app_id=..., patch=..., environment_values=..., secret_requirements=..., evidence=...)` (use uppercase keys and single-line values). NEVER provide text-only manual edit advice without calling the tool. Executing the tool creates the reviewed patch and automatically generates the "Apply Fix & Redeploy" button for the user.
3. For operational actions without configuration changes: Start starts the exact saved snapshot without rebuild. Retry reruns the failed candidate. Redeploy recreates the active snapshot. Rebuild needs a reviewed candidate from source/reference. Rollback restores a prior successful snapshot. Recovery never deletes named volumes.
""",
)
