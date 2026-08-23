"""Official Vendor Stacks Catalog and registry."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from services.official_stacks.schema import OfficialStackDefinition

# Registry of authoritative, server-verified vendor stack definitions.
_CATALOG: Dict[str, OfficialStackDefinition] = {}


def register_stack(stack: OfficialStackDefinition) -> None:
    """Registers an official stack definition in the catalog."""
    _CATALOG[stack.catalog_id] = stack


def unregister_stack(catalog_id: str) -> None:
    """Unregisters a stack definition from the catalog."""
    _CATALOG.pop(catalog_id, None)


def get_stack(catalog_id: str) -> Optional[OfficialStackDefinition]:
    """Retrieves an official stack definition by catalog identifier."""
    return _CATALOG.get(catalog_id)


def list_stacks() -> List[OfficialStackDefinition]:
    """Lists all registered official stack definitions."""
    return list(_CATALOG.values())


def clear_catalog() -> None:
    """Clears all registered stack definitions."""
    _CATALOG.clear()


def match_repository(url: str) -> Optional[Tuple[OfficialStackDefinition, str]]:
    """Matches a Git repository or image reference against registered official catalog stacks."""
    cleaned = (url or "").strip().lower().rstrip("/")
    if not cleaned:
        return None
    # Normalize github / git URLs
    clean_repo = re.sub(r"\.git$", "", cleaned)
    clean_repo = re.sub(r"^git@([^:]+):", r"https://\1/", clean_repo)
    clean_repo = re.sub(r"^ssh://git@([^/]+)/", r"https://\1/", clean_repo)
    for stack in _CATALOG.values():
        for official_repo in stack.official_repositories:
            norm_official = re.sub(r"\.git$", "", official_repo.lower().rstrip("/"))
            norm_official = re.sub(r"^git@([^:]+):", r"https://\1/", norm_official)
            if clean_repo == norm_official or clean_repo.startswith(norm_official + "/"):
                return stack, stack.default_version
    return None
