"""
tools/web_reader.py — SSRF-hardened web documentation reader & markdown extractor.
Enforces HTTPS-only, private/reserved IP blocking, redirect re-validation, and size limits.
"""
from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
from typing import Any, Dict, List, Optional
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

# Max payload limits
MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB
MAX_DOC_CHARS = 8000
MAX_REDIRECTS = 3
FETCH_TIMEOUT_SECONDS = 10.0

# Disallowed IP ranges (Carrier-grade NAT, benchmark, etc.)
_EXTRA_DISALLOWED_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),  # Shared address space / CGNAT
    ipaddress.ip_network("198.18.0.0/15"),  # Benchmarking
]


def _is_ip_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Checks if an IP is private, loopback, link-local, cloud metadata, or reserved."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    return any(ip in net for net in _EXTRA_DISALLOWED_NETWORKS)


def _validate_and_resolve_host(hostname: str) -> List[str]:
    """
    Resolves hostname to IP addresses and validates against SSRF ranges.
    Raises ValueError if hostname resolves to any disallowed IP.
    """
    clean_host = hostname.strip().strip("[]")
    # If host is directly an IP literal
    try:
        ip_obj = ipaddress.ip_address(clean_host)
        if _is_ip_disallowed(ip_obj):
            raise ValueError(f"Direct access to IP '{clean_host}' is blocked for security.")
        return [str(ip_obj)]
    except ValueError as e:
        if "blocked for security" in str(e):
            raise

    # Hostname DNS resolution
    try:
        addr_info = socket.getaddrinfo(clean_host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{clean_host}': {exc}") from exc

    resolved_ips: List[str] = []
    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if _is_ip_disallowed(ip_obj):
                raise ValueError(f"Hostname '{clean_host}' resolved to restricted IP '{ip_str}'.")
            resolved_ips.append(ip_str)
        except ValueError as exc:
            if "restricted IP" in str(exc):
                raise
            continue

    if not resolved_ips:
        raise ValueError(f"No valid public IP addresses found for hostname '{clean_host}'.")
    return resolved_ips


def _html_to_clean_markdown(html_content: str) -> str:
    """Extracts clean, readable text, headings, code snippets, and tables from HTML."""
    if not html_content:
        return ""

    # Remove script, style, nav, footer, header, svg, noscript
    cleaned = re.sub(r"<(script|style|nav|footer|header|svg|noscript|iframe)[^>]*>[\s\S]*?</\1>", " ", html_content, flags=re.IGNORECASE)
    cleaned = re.sub(r"<!--[\s\S]*?-->", " ", cleaned)

    # Convert code blocks: <pre><code>...</code></pre> or <pre>...</pre>
    def _pre_replace(m: re.Match) -> str:
        code_text = re.sub(r"<[^>]+>", "", m.group(1))
        return f"\n```\n{html.unescape(code_text.strip())}\n```\n"

    cleaned = re.sub(r"<pre[^>]*>(?:<code[^>]*>)?([\s\S]*?)(?:</code>)?</pre>", _pre_replace, cleaned, flags=re.IGNORECASE)

    # Convert inline code: <code>...</code>
    cleaned = re.sub(r"<code[^>]*>([\s\S]*?)</code>", lambda m: f"`{html.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())}`", cleaned, flags=re.IGNORECASE)

    # Convert headings
    cleaned = re.sub(r"<h1[^>]*>([\s\S]*?)</h1>", lambda m: f"\n# {html.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())}\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<h2[^>]*>([\s\S]*?)</h2>", lambda m: f"\n## {html.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())}\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<h[3-6][^>]*>([\s\S]*?)</h[3-6]>", lambda m: f"\n### {html.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())}\n", cleaned, flags=re.IGNORECASE)

    # Convert list items
    cleaned = re.sub(r"<li[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {html.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())}", cleaned, flags=re.IGNORECASE)

    # Strip all remaining tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)

    # Normalize whitespace
    lines = [line.strip() for line in cleaned.splitlines()]
    result = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


async def fetch_web_documentation(
    url: str,
    max_chars: int = MAX_DOC_CHARS,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    SSRF-protected HTTP fetcher for external documentation and Git setup guides.
    Returns clean, structured markdown for LLM consumption.
    """
    clean_url = (url or "").strip()
    if not clean_url:
        return {"status": "error", "message": "URL cannot be empty."}

    parsed = urllib.parse.urlsplit(clean_url)
    if parsed.scheme.lower() != "https":
        return {
            "status": "error",
            "message": "Only secure HTTPS URLs are permitted for external documentation reading.",
        }

    # GitHub shortcut: Convert github.com/owner/repo to raw README
    gh_match = re.match(r"^https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)(?:/tree/[^/]+)?/?$", clean_url)
    if gh_match:
        owner, repo = gh_match.group(1), gh_match.group(2)
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"
        result = await _fetch_url_internal(raw_url, max_chars)
        if result.get("status") == "ok":
            return result
        # Fallback to master branch
        raw_url_master = f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md"
        result_master = await _fetch_url_internal(raw_url_master, max_chars)
        if result_master.get("status") == "ok":
            return result_master

    return await _fetch_url_internal(clean_url, max_chars)


async def _fetch_url_internal(url: str, max_chars: int) -> Dict[str, Any]:
    """Executes validated HTTP fetch with redirect tracking and SSRF re-validation."""
    current_url = url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,text/plain,text/markdown,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    redirect_count = 0
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            while redirect_count <= MAX_REDIRECTS:
                parsed = urllib.parse.urlsplit(current_url)
                if parsed.scheme.lower() != "https":
                    return {"status": "error", "message": "Redirect to non-HTTPS URL blocked."}
                if not parsed.hostname:
                    return {"status": "error", "message": "Invalid hostname in URL."}

                # Validate SSRF on current hop
                try:
                    _validate_and_resolve_host(parsed.hostname)
                except ValueError as exc:
                    return {"status": "blocked", "message": str(exc)}

                response = await client.get(current_url, headers=headers)

                # Handle redirect
                if response.status_code in (301, 302, 303, 307, 308):
                    loc = response.headers.get("Location")
                    if not loc:
                        break
                    current_url = urllib.parse.urljoin(current_url, loc)
                    redirect_count += 1
                    continue

                # Check status
                if response.status_code == 403 or response.status_code == 429:
                    return {
                        "status": "blocked",
                        "status_code": response.status_code,
                        "message": (
                            "This website is protected by Cloudflare anti-bot security or rate limiting. "
                            "Please ask the user to paste the docker-compose.yml, docker run snippet, or config directly into chat."
                        ),
                    }
                elif response.status_code >= 400:
                    return {
                        "status": "error",
                        "status_code": response.status_code,
                        "message": f"Documentation URL returned HTTP {response.status_code}.",
                    }

                # Check Content-Type & Size
                content_type = response.headers.get("Content-Type", "").lower()
                raw_bytes = response.content[:MAX_RESPONSE_BYTES]
                raw_text = raw_bytes.decode(response.encoding or "utf-8", errors="replace")

                # Format content
                if "html" in content_type:
                    parsed_doc = _html_to_clean_markdown(raw_text)
                else:
                    parsed_doc = raw_text.strip()

                if len(parsed_doc) > max_chars:
                    parsed_doc = parsed_doc[:max_chars] + f"\n... [Truncated {len(parsed_doc) - max_chars} characters]"

                # Wrap with untrusted content perimeter against prompt injection
                wrapped = (
                    "--- [EXTERNAL DOCUMENTATION CONTENT — UNTRUSTED REFERENCE DATA] ---\n"
                    f"{parsed_doc}\n"
                    "--- [END OF EXTERNAL DOCUMENTATION CONTENT] ---"
                )

                return {
                    "status": "ok",
                    "url": current_url,
                    "content": wrapped,
                    "length": len(parsed_doc),
                }

        return {"status": "error", "message": "Exceeded maximum redirect limit (3)."}
    except httpx.TimeoutException:
        return {"status": "error", "message": "Connection timed out while fetching documentation (10s limit)."}
    except Exception as exc:
        logger.warning("Error fetching web documentation '%s': %s", url, exc)
        return {"status": "error", "message": f"Could not read documentation: {str(exc)}"}
