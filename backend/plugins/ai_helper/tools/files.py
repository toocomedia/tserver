"""
tools/files.py — Confined, sandboxed Read-Only file manager tool handlers.
Supports flexible target resolution by domain, kind:id, or resource ID.
Enforces strict secret masking on ALL file reads; bypassed only with explicit session consent.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.file_manager import file_operations, file_targets

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret Detection Patterns — applied to ALL file reads by default
# ---------------------------------------------------------------------------

# Matches assignment-style credential lines in any config/code file
_INLINE_SECRET_RE = re.compile(
    r"((?:KEY|SECRET|PASS(?:WORD)?|TOKEN|AUTH|CREDENTIAL|PRIVATE|PWD|WEBHOOK|"
    r"SIGNING|ENCRYPTION|JWT|API_?KEY|ACCESS_?TOKEN|CLIENT_?SECRET|"
    r"DB_?PASS(?:WORD)?|DATABASE_?(?:URL|PASS(?:WORD)?)|SMTP_?PASS(?:WORD)?|"
    r"RSA|DSA|ECDSA)\s*=\s*)(\S+)",
    re.IGNORECASE,
)

# JSON/YAML style: "password": "value" or password: value
_JSON_SECRET_RE = re.compile(
    r"""(["']?(?:password|passwd|secret(?:_?key)?|api[_-]?key|auth[_-]?token|"""
    r"""private[_-]?key|access[_-]?token|client[_-]?secret|db[_-]?pass(?:word)?|"""
    r"""database[_-]?(?:url|password)|smtp[_-]?pass(?:word)?|jwt[_-]?secret|"""
    r"""encryption[_-]?key|webhook[_-]?secret|signing[_-]?key|"""
    r"""credentials?)["']?\s*[:=]\s*)(["\']?)(\S{4,}?)(\3)""",
    re.IGNORECASE,
)

# High-risk filenames that are fully blocked unless secrets_allowed
_BLOCKED_FILENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging", ".env.development",
    "secrets.json", "credentials.json", "service-account.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    ".htpasswd", "wp-config.php", "settings.php",
})

# High-risk extensions that are fully blocked unless secrets_allowed
_BLOCKED_EXTENSIONS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks", ".pkcs12", ".crt", ".cer",
})


def _is_high_risk_file(file_path: str) -> bool:
    """Returns True if the file is in the blocked names/extensions list."""
    import os
    basename = os.path.basename((file_path or "").strip().lower().lstrip("/"))
    _, ext = os.path.splitext(basename)
    return basename in _BLOCKED_FILENAMES or ext in _BLOCKED_EXTENSIONS


def _mask_all_secrets(content: str) -> str:
    """
    Masks credential values in any file type.
    Applied to ALL file reads when secrets are not allowed.
    """
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            lines.append(line)
            continue
        # Mask KEY=value style (env, ini, shell)
        masked = _INLINE_SECRET_RE.sub(r"\1••••••••", line)
        # Mask JSON/YAML "password": "value" style
        masked = _JSON_SECRET_RE.sub(r"\1\2••••••••\4", masked)
        lines.append(masked)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Legacy alias (kept for backwards compat with any direct callers)
# ---------------------------------------------------------------------------
def _mask_env_secrets(content: str) -> str:
    return _mask_all_secrets(content)


# ---------------------------------------------------------------------------
# Target Resolution
# ---------------------------------------------------------------------------

async def _resolve_file_target(db: AsyncSession, target_id: str, target_type: str = "") -> file_targets.FileTarget:
    """Resolves target_id string flexibly into a FileTarget."""
    clean_id = (target_id or "").strip()
    # Strip any UI mention prefixes if passed directly by the AI model
    for prefix in ("@domain:", "@app:", "@file:", "@db:"):
        if clean_id.lower().startswith(prefix):
            clean_id = clean_id[len(prefix):].strip()
            break

    # 1. Direct kind:id parse
    try:
        return file_targets.parse_target(clean_id)
    except Exception:
        pass

    clean_lower = clean_id.lower()

    # 2. Match from file manager active targets
    try:
        targets = await file_targets.list_targets(db)
        for t in targets:
            tid = (t.get("id") or "").lower()
            dom = (t.get("domain") or "").lower()
            preset = (t.get("preset") or "").lower()
            ttype = (t.get("target_type") or "").lower()

            if clean_lower and (clean_lower == dom or clean_lower == tid or clean_lower == preset):
                return file_targets.parse_target(t.get("id"))
            if clean_lower and clean_lower in (dom, preset, tid):
                return file_targets.parse_target(t.get("id"))
            if target_type and target_type.lower() in (ttype, dom):
                if str(t.get("resource_id", "")) == clean_id or tid.endswith(f":{clean_id}"):
                    return file_targets.parse_target(t.get("id"))
            if tid.endswith(f":{clean_id}"):
                return file_targets.parse_target(t.get("id"))
        for t in targets:
            dom = (t.get("domain") or "").lower()
            if clean_lower and (clean_lower in dom or dom in clean_lower):
                return file_targets.parse_target(t.get("id"))
    except Exception as e:
        logger.debug("Error querying file_targets: %s", e)

    # 3. Direct Database Lookup by Domain name or App ID
    from sqlalchemy import select
    from models.domain import Domain
    from models.php_website import PhpWebsite
    from models.container_app import ContainerApp
    from models.hosted_app import HostedApp

    # Search Domain table
    dom_stmt = select(Domain).where(Domain.name.ilike(f"%{clean_lower}%"))
    dom_res = await db.execute(dom_stmt)
    matched_domain = dom_res.scalars().first()

    if matched_domain:
        if matched_domain.project_type == "php":
            php_site = (await db.execute(select(PhpWebsite).where(PhpWebsite.domain_id == matched_domain.id))).scalars().first()
            if php_site:
                return file_targets.FileTarget("php", php_site.id, matched_domain.name, "PHP site", php_site.status)
        elif matched_domain.project_type == "container":
            c_app = (await db.execute(select(ContainerApp).where(ContainerApp.domain_id == matched_domain.id))).scalars().first()
            if c_app:
                return file_targets.FileTarget("container", c_app.id, matched_domain.name, "Container app", c_app.status)
        elif matched_domain.project_type == "python":
            py_app = (await db.execute(select(HostedApp).where(HostedApp.domain_id == matched_domain.id))).scalars().first()
            if py_app:
                return file_targets.FileTarget("python", py_app.id, matched_domain.name, "Python app", py_app.status)

        return file_targets.FileTarget("static", matched_domain.id, matched_domain.name, "Static site", "ready")

    # Search PHP Websites directly
    php_sites = (await db.execute(select(PhpWebsite))).scalars().all()
    for ps in php_sites:
        if str(ps.id) == clean_id or (ps.root_path and clean_lower in ps.root_path.lower()):
            return file_targets.FileTarget("php", ps.id, None, "PHP site", ps.status)

    raise ValueError(f"Managed application target '{clean_id}' not found in registered domains or applications.")


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------

async def list_website_directory(
    db: AsyncSession,
    target_id: str = "",
    relative_path: str = "",
    target_type: str = "",
    secrets_allowed: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Lists files and folders inside a website/application root directory."""
    try:
        effective_id = target_id or kwargs.get("target") or ""
        target = await _resolve_file_target(db, effective_id, target_type)
        context = await file_targets.resolve_context(db, target, "application")
        result = file_operations.list_entries(context, relative_path or "")
        return {
            "status": "ok",
            "target_id": target.id,
            "domain": target.domain,
            "path": relative_path,
            "entries": result.get("entries", []),
            "has_more": result.get("has_more", False),
        }
    except Exception as exc:
        targets = await file_targets.list_targets(db)
        available = [t.get("id") for t in targets]
        return {
            "status": "error",
            "message": f"Could not list directory: {str(exc)}",
            "available_target_ids": available,
        }


async def read_website_file(
    db: AsyncSession,
    target_id: str = "",
    file_path: str = "",
    target_type: str = "",
    max_chars: int = 8000,
    secrets_allowed: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Reads a file in read-only mode from a verified website root directory.
    High-risk credential files are fully blocked unless secrets_allowed=True.
    All other files have secret values masked unless secrets_allowed=True.
    """
    # Gate 1: block high-risk files entirely unless user consented
    if not secrets_allowed and _is_high_risk_file(file_path):
        return {
            "status": "secrets_blocked",
            "file_path": file_path,
            "message": (
                f"'{file_path}' is a sensitive credential file and cannot be read without explicit user consent. "
                "The user must type 'I allow secrets' or click the unlock button in chat."
            ),
            "action_required": "ALLOW_SECRETS",
        }

    try:
        effective_id = target_id or kwargs.get("target") or ""
        target = await _resolve_file_target(db, effective_id, target_type)
        context = await file_targets.resolve_context(db, target, "application")
        res = file_operations.read_text(context, file_path)
        content = res.get("content", "")

        # Gate 2: mask secrets in all other files unless user consented
        if not secrets_allowed:
            content = _mask_all_secrets(content)

        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... [Truncated {len(content) - max_chars} characters]"

        return {
            "status": "ok",
            "target_id": target.id,
            "domain": target.domain,
            "file_path": file_path,
            "size": res.get("size", len(content)),
            "content": content,
            "secrets_masked": not secrets_allowed,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Could not read file: {str(exc)}",
        }
