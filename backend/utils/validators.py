"""
utils/validators.py — Input validation helpers
All user input is validated here before hitting services.
"""
import re
import ipaddress

# RFC 1035 + IDN friendly domain pattern
_DOMAIN_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*"
    r"\.[A-Za-z]{2,}$"
)

# Subdomain label only (no dots allowed — just the prefix)
_SUBDOMAIN_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def is_valid_domain(domain: str) -> bool:
    """Return True if domain is a valid fully-qualified domain name."""
    if not domain or len(domain) > 253:
        return False
    return bool(_DOMAIN_RE.match(domain.lower()))


def is_valid_subdomain_label(label: str) -> bool:
    """Return True if label is a valid single subdomain prefix (no dots)."""
    if not label or len(label) > 63:
        return False
    return bool(_SUBDOMAIN_LABEL_RE.match(label.lower()))


def is_valid_ip(ip: str) -> bool:
    """Return True if ip is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def is_valid_port(port: int) -> bool:
    """Return True if port is in valid range 1–65535."""
    return 1 <= port <= 65535


def sanitize_domain(domain: str) -> str:
    """Lowercase and strip whitespace from domain. Raises ValueError if invalid."""
    domain = domain.strip().lower()
    if not is_valid_domain(domain):
        raise ValueError(f"Invalid domain name: {domain!r}")
    return domain


def sanitize_subdomain_label(label: str) -> str:
    """Lowercase and strip whitespace from subdomain label. Raises ValueError if invalid."""
    label = label.strip().lower()
    if not is_valid_subdomain_label(label):
        raise ValueError(f"Invalid subdomain label: {label!r}")
    return label
