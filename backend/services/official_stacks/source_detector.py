"""Source repository inspection and official vendor stack detection."""
from __future__ import annotations

from typing import Any, Dict

from services.official_stacks.catalog import match_repository


def detect_official_stack(repository_url: str) -> Dict[str, Any]:
    """Inspects a repository URL against the official vendor stack catalog."""
    match = match_repository(repository_url)
    if match is None:
        return {"is_official_stack": False}
    stack, version = match
    return {
        "is_official_stack": True,
        "catalog_id": stack.catalog_id,
        "name": stack.display_name,
        "vendor": stack.vendor_name,
        "version": version,
        "description": stack.description,
        "services_count": len(stack.services),
        "services": list(stack.services.keys()),
        "recommended_ram_mb": stack.recommended_ram_mb,
        "minimum_ram_mb": stack.minimum_ram_mb,
        "web_internal_port": stack.web_internal_port,
        "post_install_message": stack.post_install_message,
        "docs_url": stack.docs_url,
    }
