"""Build canonical AppSpec plan payloads from bounded source-inspection manifests."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from services import container_app_image_inspect_service
from services.apps_engine.app_spec_codec import app_spec_to_dict
from services.apps_engine.security_policy import validate_app_spec
from services.official_stacks.proposal_manifest import stack_from_proposal, validate_stack_settings


def build_payload(
    stack_manifest: dict[str, Any],
    *,
    domain_name: str,
    nonsecret_settings: dict[str, str] | None,
    evidence: list[str] | None,
    repository_url: str = "",
    source_type: str = "",
) -> dict[str, Any]:
    """Normalize inspection output without network access or runtime side effects."""
    clean_evidence = [str(item).strip()[:1024] for item in evidence or [] if str(item).strip()][:12]
    manifest = copy.deepcopy(stack_manifest)
    for service in manifest.get("services") or []:
        if isinstance(service, dict) and service.get("image"):
            container_app_image_inspect_service.validate_image_reference(str(service["image"]))
    legacy_spec = stack_from_proposal(manifest, clean_evidence)
    settings = validate_stack_settings(legacy_spec, nonsecret_settings)
    canonical = app_spec_to_dict(legacy_spec)
    spec = validate_app_spec(canonical)
    canonical = app_spec_to_dict(spec)
    normalized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    payload: dict[str, Any] = {
        "deploy_type": "app_spec",
        "domain_name": domain_name.strip().lower(),
        "app_spec": canonical,
        "app_spec_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "environment_values": settings,
        "evidence": clean_evidence,
    }
    if repository_url and repository_url.strip():
        payload["repository_url"] = repository_url.strip()
    if source_type and source_type.strip():
        payload["source_type"] = source_type.strip()
    return payload

