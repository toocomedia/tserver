"""Read bounded, non-secret Compose facts for App Engine planning; never execute or parse it as runtime config."""
from __future__ import annotations

import re
from pathlib import Path

_SERVICE = re.compile(r"^ {2}([A-Za-z0-9_-]{1,64}):\s*(?:#.*)?$")
_IMAGE = re.compile(r"^ {4}image:\s*([^#]+)")
_PORT = re.compile(r"(?<![0-9.])(\d{1,5})(?![0-9.])")


def inspect_compose_evidence(path: Path) -> dict[str, object]:
    """Return static service/image/port facts only; commands, environment and mounts stay unread."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore")[:200_000].splitlines()
    except OSError:
        return {}

    services: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    in_services = False
    in_ports = False
    for line in lines:
        if line.startswith("services:"):
            in_services = True
            continue
        if in_services and line and not line.startswith((" ", "\t", "#")):
            break
        if not in_services:
            continue
        service = _SERVICE.match(line)
        if service:
            current = {"name": service.group(1), "internal_ports": []}
            services.append(current)
            in_ports = False
            continue
        if current is None:
            continue
        if line.startswith("    ports:"):
            in_ports = True
            continue
        if line.startswith("    ") and not line.startswith("      "):
            in_ports = False
        image = _IMAGE.match(line)
        if image:
            value = image.group(1).strip().strip("'\"")
            if value and "$" not in value and len(value) <= 512:
                current["image"] = value
            continue
        if in_ports and line.startswith("      -"):
            ports = _PORT.findall(line)
            if ports:
                internal = int(ports[-1])
                if 1 <= internal <= 65535 and internal not in current["internal_ports"]:
                    current["internal_ports"].append(internal)

    safe_services = [item for item in services if item.get("image")][:8]
    ports = sorted({port for item in safe_services for port in item["internal_ports"]})
    return {
        "file": path.name,
        "detected_ports": ports,
        "services": safe_services,
        "evidence": [
            f"{path.name}: service '{item['name']}' uses image '{item['image']}'."
            for item in safe_services
        ],
        "notice": "Repository Compose was inspected as source evidence only; it will not be executed.",
    }
