"""Manual-only App Engine recovery explanation contract."""
from plugins.ai_helper.prompts.skills._base import SkillSpec

SKILL = SkillSpec(
    name="app_recovery",
    task_types=["app_recovery", "recover_app"],
    prompt="""### App Engine recovery
Call `get_app_engine_diagnostics` first. Explain manual App-page choices only; never execute any action or emit action tags.

Start starts the exact saved snapshot without build or secret rotation. Retry reruns the failed candidate. Redeploy recreates the active snapshot from saved digests. Rebuild needs a reviewed candidate from source/reference. Rollback restores a prior successful snapshot. Recovery never deletes named volumes.
""",
)
