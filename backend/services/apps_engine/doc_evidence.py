"""Doc and env evidence extractor for App Engine inspection.

Extracts installation snippets from any markdown file (with early stop and strict caps)
and parses non-standard environment sample files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Maximum characters extracted from markdown documentation to minimize LLM token usage
MAX_DOC_SNIPPET_CHARS = 3500

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


def find_install_instructions(root: Path) -> dict[str, Any]:
    """
    Search markdown documentation files for installation and setup guides.
    Stops immediately after finding the first relevant match to save token usage.
    """
    if not root.is_dir():
        return {"found": False}

    # Collect actual markdown files from root and docs/
    md_files: list[Path] = [p for p in root.iterdir() if p.is_file() and p.name.lower().endswith(".md")]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        md_files.extend([p for p in docs_dir.iterdir() if p.is_file() and p.name.lower().endswith(".md")])

    # Sort files: priority doc names first, then others
    def _doc_priority(p: Path) -> int:
        name_lower = p.name.lower()
        if name_lower in _PRIORITY_DOC_NAMES:
            return _PRIORITY_DOC_NAMES.index(name_lower)
        return len(_PRIORITY_DOC_NAMES) + 1

    md_files.sort(key=_doc_priority)

    for doc_path in md_files:
        try:
            content = doc_path.read_text(encoding="utf-8", errors="ignore")[:40_000]
            if not content:
                continue

            match = _INSTALL_HEADER_RE.search(content)
            if match:
                start_pos = match.start()
                snippet = content[start_pos : start_pos + MAX_DOC_SNIPPET_CHARS]
                
                # Check for post-install admin commands and docker images inside snippet or file
                detected_cmds = _extract_admin_commands(content)
                detected_imgs = _extract_docker_images(content)
                
                return {
                    "found": True,
                    "file": doc_path.name,
                    "snippet": snippet.strip(),
                    "detected_admin_commands": detected_cmds,
                    "detected_docker_images": detected_imgs,
                }
            
            # If no heading matches, but file contains 'docker run' or 'docker-compose'
            if "docker run" in content.lower() or "docker-compose" in content.lower():
                snippet = content[:MAX_DOC_SNIPPET_CHARS]
                detected_cmds = _extract_admin_commands(content)
                detected_imgs = _extract_docker_images(content)
                return {
                    "found": True,
                    "file": doc_path.name,
                    "snippet": snippet.strip(),
                    "detected_admin_commands": detected_cmds,
                    "detected_docker_images": detected_imgs,
                }
        except Exception:
            continue

    return {"found": False}


def parse_expanded_env_samples(root: Path) -> dict[str, str]:
    """Parse any environment template file (TEMPLATE.env, .env.example, etc.)."""
    if not root.is_dir():
        return {}

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
                return result
        except Exception:
            pass

    return {}


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
