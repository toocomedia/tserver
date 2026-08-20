"""Persist explicit approvals for unverified plugin/platform combinations."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import config


class PluginPlatformApprovalService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(config.PLUGIN_PLATFORM_APPROVALS_PATH)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, list[str]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        approvals = payload.get("approvals", {}) if isinstance(payload, dict) else {}
        if not isinstance(approvals, dict):
            return {}
        return {
            str(plugin_id): [str(selector) for selector in selectors]
            for plugin_id, selectors in approvals.items()
            if isinstance(selectors, list)
        }

    def is_approved(self, plugin_id: str, selector: str) -> bool:
        with self._lock:
            return selector in self._load().get(plugin_id, [])

    def approve(self, plugin_id: str, selector: str) -> None:
        with self._lock:
            approvals = self._load()
            selectors = set(approvals.get(plugin_id, []))
            selectors.add(selector)
            approvals[plugin_id] = sorted(selectors)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temporary.write_text(
                json.dumps({"version": 1, "approvals": approvals}, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)


plugin_platform_approval_service = PluginPlatformApprovalService()
