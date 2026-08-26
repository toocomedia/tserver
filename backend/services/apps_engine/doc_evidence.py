"""Doc and env evidence extractor for App Engine inspection.

Extracts bounded installation evidence from markdown files and parses
non-standard environment sample files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services.apps_engine import build_secrets

# Bounded evidence pack: at most three useful sections and 6,000 total chars.
MAX_DOC_SNIPPET_CHARS = 6000
MAX_DOC_SOURCE_CHARS = 2200
MAX_DOC_SOURCES = 3

# Heading patterns that indicate setup / installation instructions
_INSTALL_HEADER_RE = re.compile(
    r"(?im)^#{1,4}\s*(?:installation|install|getting\s+started|quick\s*start|setup|deploy(?:ment)?|docker(?:\s+compose)?|running\s+with\s+docker|configuration|environment\s+variables)",
)

# CLI commands of interest for post-deploy or initial admin creation
_ADMIN_COMMAND_PATTERNS = [
    re.compile(r"(?:docker\s+(?:run|exec)[^\n]*\s+)?(?:\./manage\.py|python\s+manage\.py)\s+(?:registeradmin|createsuperuser)[^\n]*", re.IGNORECASE),
    re.compile(r"(?:docker\s+(?:run|exec)[^\n]*\s+)?(?:php\s+artisan|artisan)\s+(?:admin:create|migrate|db:seed)[^\n]*", re.IGNORECASE),
    re.compile(r"(?:docker\s+(?:run|exec)[^\n]*\s+)?(?:wp\s+core\s+install)[^\n]*", re.IGNORECASE),
]

_PRIORITY_DOC_NAMES = [
    "guide.md", "install.md", "installation.md", "deploy.md", "deployment.md",
    "setup.md", "howto.md", "quickstart.md", "readme.md",
]

_ENV_TEMPLATE_PATTERNS = (
    "template.env", ".env.example", ".env.sample", ".env.template", "env.example",
    "env.sample", ".env.dist", "example.env", "default.env", "sample.env",
    "template.env", "env.default", "env.template", ".env.defaults",
)

_SETUP_INPUT_PATTERNS = (
    (
        "admin_email", "Admin Email", "email",
        re.compile(r"\b(?:admin(?:istrator)?|superuser|owner)\s+(?:e-?mail|email\s+address)\b|\bADMIN_EMAIL\b", re.IGNORECASE),
    ),
    (
        "admin_username", "Admin Username", "text",
        re.compile(r"\b(?:admin(?:istrator)?|superuser|owner)\s+(?:user(?:name)?|login)\b|\bADMIN_(?:USER|USERNAME)\b", re.IGNORECASE),
    ),
    (
        "site_name", "Site Name", "text",
        re.compile(r"\b(?:site|instance)\s+(?:name|title)\b|\b(?:SITE|INSTANCE)_(?:NAME|TITLE)\b", re.IGNORECASE),
    ),
    (
        "smtp_host", "SMTP Host", "text",
        re.compile(r"\b(?:SMTP|EMAIL|MAIL)_(?:HOST|SERVER)\b|\bSMTP\s+(?:host|server)\b", re.IGNORECASE),
    ),
    (
        "smtp_port", "SMTP Port", "number",
        re.compile(r"\b(?:SMTP|EMAIL|MAIL)_PORT\b|\bSMTP\s+port\b", re.IGNORECASE),
    ),
    (
        "smtp_username", "SMTP Username", "text",
        re.compile(r"\b(?:SMTP|EMAIL|MAIL)(?:_(?:HOST|SERVER))?_(?:USER|USERNAME)\b|\bSMTP\s+(?:user|username)\b", re.IGNORECASE),
    ),
    (
        "sender_email", "Sender Email", "email",
        re.compile(r"\b(?:SERVER|FROM|SENDER|DEFAULT)_EMAIL\b|\b(?:sender|from)\s+email\b", re.IGNORECASE),
    ),
)
_EXTRA_SECRET_PARTS = ("LICENSE_KEY", "LICENSE_TOKEN", "SMTP_PASS", "ADMIN_PASS")
_SECRET_NAME_RE = re.compile(
    r"(?:PASS(?:WORD)?|SECRET|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|LICENSE[_-]?KEY)",
    re.IGNORECASE,
)


def find_install_instructions(
    root: Path,
    env_sample: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Search markdown documentation files for a small, structured setup evidence pack.
    Compatibility fields ``file`` and ``snippet`` retain the first/combined result.
    """
    if not root.is_dir():
        return {"found": False}

    md_files = _markdown_files(root)
    admin_command_hints: list[dict[str, str]] = []
    image_hints: list[dict[str, str]] = []
    sources: list[dict[str, str]] = []
    remaining = MAX_DOC_SNIPPET_CHARS

    for doc_path in md_files:
        if len(sources) >= MAX_DOC_SOURCES or remaining <= 0:
            break
        try:
            content = redact_secret_values(doc_path.read_text(encoding="utf-8", errors="ignore")[:40_000])
            if not content:
                continue
            sections = _relevant_sections(content)
            if not sections:
                continue
            for heading, raw_snippet in sections:
                if len(sources) >= MAX_DOC_SOURCES or remaining <= 0:
                    break
                snippet = raw_snippet[:min(MAX_DOC_SOURCE_CHARS, remaining)].strip()
                if not snippet:
                    continue
                source = {"file": doc_path.name, "heading": heading, "snippet": snippet}
                sources.append(source)
                remaining -= len(snippet)
                evidence = _source_label(source)
                for command in _extract_admin_commands(snippet):
                    _append_evidenced_value(admin_command_hints, "command", command, evidence)
                for image in _extract_docker_images(snippet):
                    _append_evidenced_value(image_hints, "image", image, evidence)
        except Exception:
            continue

    parsed_env, env_file = _read_expanded_env_sample(root)
    effective_env = env_sample if isinstance(env_sample, dict) else parsed_env
    hints = _extract_setup_hints(sources, effective_env, env_file)
    detected_cmds = [item["command"] for item in admin_command_hints]
    detected_imgs = [item["image"] for item in image_hints]
    if detected_cmds and not any(item.get("name") == "admin_email" for item in hints["required_inputs"]):
        _append_hint(hints["required_inputs"], {
            "name": "admin_email", "label": "Admin Email", "kind": "email",
            "secret": False, "evidence": _source_label(sources[0]) if sources else "documentation",
        })
    hints["admin_commands"] = admin_command_hints
    hints["docker_images"] = image_hints
    if not sources:
        return {"found": False, "sources": [], "setup_hints": hints}

    combined = "\n\n---\n\n".join(source["snippet"] for source in sources)[:MAX_DOC_SNIPPET_CHARS]
    return {
        "found": True,
        "file": sources[0]["file"],
        "snippet": combined,
        "sources": sources,
        "detected_admin_commands": detected_cmds,
        "detected_docker_images": detected_imgs,
        "setup_hints": hints,
    }


def parse_expanded_env_samples(root: Path) -> dict[str, str]:
    """Parse any environment template file (TEMPLATE.env, .env.example, etc.)."""
    result, _ = _read_expanded_env_sample(root)
    return result


def redact_secret_values(text: str) -> str:
    """Remove credential values from documentation before AI-visible use."""
    safe = re.sub(
        r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END \1-----",
        "[PRIVATE KEY REDACTED]",
        text or "",
        flags=re.IGNORECASE,
    )
    lines: list[str] = []
    for line in safe.splitlines():
        assignment = re.match(r"^(\s*(?:export\s+)?([A-Z][A-Z0-9_-]*)\s*=\s*).*$", line)
        if assignment and _SECRET_NAME_RE.search(assignment.group(2)):
            lines.append(f"{assignment.group(1)}[REDACTED]")
            continue
        line = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s]+)",
            lambda match: f"{match.group(1)}=[REDACTED]"
            if _SECRET_NAME_RE.search(match.group(1)) else match.group(0),
            line,
        )
        line = re.sub(
            r'(["\']([A-Za-z0-9_-]*(?:password|secret|token|api[_-]?key|private[_-]?key|license[_-]?key)[A-Za-z0-9_-]*)["\']\s*:\s*)["\'][^"\']*["\']',
            lambda match: f'{match.group(1)}"[REDACTED]"',
            line,
            flags=re.IGNORECASE,
        )
        line = re.sub(
            r"(--(?:password|secret|token|api-key|private-key|license-key)(?:=|\s+))\S+",
            r"\1[REDACTED]",
            line,
            flags=re.IGNORECASE,
        )
        line = re.sub(r"(https?://[^\s:/@]+:)[^\s@]+(@)", r"\1[REDACTED]\2", line)
        lines.append(line)
    return "\n".join(lines)


def ai_safe_env_sample(env_sample: dict[str, str]) -> dict[str, str]:
    """Keep useful defaults while omitting all credential-like sample values."""
    return {
        key: "[REDACTED]"
        if build_secrets.is_sensitive_key(key) or _SECRET_NAME_RE.search(key)
        else value
        for key, value in env_sample.items()
    }


def _read_expanded_env_sample(root: Path) -> tuple[dict[str, str], str]:
    """Return the first environment template and its evidence filename."""
    if not root.is_dir():
        return {}, ""

    # Collect actual candidate files from root
    all_files = [p for p in root.iterdir() if p.is_file() and p.name != ".env"]
    
    def _env_priority(p: Path) -> int:
        name_lower = p.name.lower()
        for idx, pattern in enumerate(_ENV_TEMPLATE_PATTERNS):
            if name_lower == pattern or name_lower.endswith(pattern):
                return idx
        if "env" in name_lower:
            return len(_ENV_TEMPLATE_PATTERNS) + 1
        return 999

    candidate_files = [p for p in all_files if _env_priority(p) < 999]
    candidate_files.sort(key=_env_priority)

    for path in candidate_files:
        try:
            result: dict[str, str] = {}
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key:
                    result[key] = val
            if result:
                return result, path.name
        except Exception:
            pass

    return {}, ""


def _markdown_files(root: Path) -> list[Path]:
    files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".md"]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        files.extend(p for p in docs_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md")

    def priority(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        rank = _PRIORITY_DOC_NAMES.index(name) if name in _PRIORITY_DOC_NAMES else len(_PRIORITY_DOC_NAMES)
        return rank, str(path).lower()

    return sorted(files, key=priority)


def _relevant_sections(content: str) -> list[tuple[str, str]]:
    matches = list(_INSTALL_HEADER_RE.finditer(content))
    sections: list[tuple[str, str]] = []
    for match in matches:
        header = match.group(0).strip()
        level = len(header) - len(header.lstrip("#"))
        end = len(content)
        for next_header in re.finditer(r"(?m)^#{1,4}\s+\S.*$", content[match.end():]):
            raw = next_header.group(0)
            next_level = len(raw) - len(raw.lstrip("#"))
            if next_level <= level:
                end = match.end() + next_header.start()
                break
        heading = header.lstrip("#").strip()
        sections.append((heading, content[match.start():end].strip()))
    if not sections and any(marker in content.lower() for marker in ("docker run", "docker-compose", "docker compose")):
        sections.append(("Docker setup", content.strip()))
    return sections


def _extract_setup_hints(
    sources: list[dict[str, str]],
    env_sample: dict[str, str],
    env_file: str,
) -> dict[str, list[dict[str, Any]]]:
    required_inputs: list[dict[str, Any]] = []
    secret_names: list[dict[str, Any]] = []

    for source in sources:
        evidence = _source_label(source)
        text = source["snippet"]
        for name, label, kind, pattern in _SETUP_INPUT_PATTERNS:
            match = pattern.search(text)
            if match and _is_documented_required(text, match.start(), match.end()):
                _append_hint(required_inputs, {"name": name, "label": label, "kind": kind, "secret": False, "evidence": evidence})

    env_evidence = env_file or "environment sample"
    for raw_key in env_sample:
        key = build_secrets.normalize_environment_key(raw_key)
        if not key:
            continue
        if build_secrets.is_sensitive_key(key) or any(part in key for part in _EXTRA_SECRET_PARTS):
            _append_hint(secret_names, {"name": key, "evidence": env_evidence})
            continue
    return {"required_inputs": required_inputs, "secret_names": secret_names}


def _is_documented_required(text: str, start: int, end: int) -> bool:
    """An env example alone is not a user question; require explicit docs wording."""
    context = text[max(0, start - 180):min(len(text), end + 180)]
    return bool(re.search(
        r"\b(?:required|must|need(?:s|ed)?\s+to|configure|provide|enter|set)\b",
        context,
        re.IGNORECASE,
    ))


def _append_hint(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    existing = next((entry for entry in items if entry.get("name") == item.get("name")), None)
    if existing is None:
        items.append(item)
        return
    for key, value in item.items():
        if value and not existing.get(key):
            existing[key] = value


def _source_label(source: dict[str, str]) -> str:
    heading = source.get("heading") or "Setup"
    return f"{source.get('file', 'documentation')}#{heading}"


def _append_evidenced_value(items: list[dict[str, str]], key: str, value: str, evidence: str) -> None:
    if not any(item.get(key) == value for item in items):
        items.append({key: value, "evidence": evidence})


def _extract_admin_commands(text: str) -> list[str]:
    """Find administrative setup commands (e.g. registeradmin, createsuperuser)."""
    commands: list[str] = []
    for pattern in _ADMIN_COMMAND_PATTERNS:
        for match in pattern.finditer(text):
            cmd = match.group(0).strip()
            if cmd and cmd not in commands:
                commands.append(cmd)
    return commands


def _extract_docker_images(text: str) -> list[str]:
    """Dynamically find docker images mentioned in documentation (e.g. docker pull / docker run)."""
    images: list[str] = []
    for pattern in (
        r"docker\s+(?:pull|run)[^\n]*?\s+([a-z0-9_.-]+(?:/[a-z0-9_.-]+)+(?::[a-z0-9_.-]+)?)",
        r"(?:image:\s*|using\s+`)([a-z0-9_.-]+(?:/[a-z0-9_.-]+)+(?::[a-z0-9_.-]+)?)",
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            img = match.group(1).strip().strip("'\"`")
            if img and img not in images and not img.startswith("http"):
                images.append(img)
    return images
