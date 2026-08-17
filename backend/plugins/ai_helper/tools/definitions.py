"""
tools/definitions.py — Standard JSON tool specifications for LLM function calling.
Compatible with OpenAI, Anthropic Claude, DeepSeek, Groq, Google Gemini, and Ollama.
"""
from __future__ import annotations

from typing import Any, Dict, List

RAW_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "get_domains_and_ssl",
        "description": "Lists all registered domains on the VPS, their project type (static/php/python/container/proxy), document root path, SSL certificate status, expiration date, and target internal port.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain_name": {
                    "type": "string",
                    "description": "Optional domain filter (e.g. 'example.com'). If omitted, lists all domains.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_reverse_proxy_routes",
        "description": "Inspects Nginx reverse proxy routes, domain bindings, upstream internal targets (e.g. http://127.0.0.1:3000), and custom proxy headers.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional domain name to filter proxy configuration.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_dns_records",
        "description": "Queries PowerDNS zone records (A, AAAA, CNAME, MX, TXT, SRV, NS) for a given domain or all domains.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The domain name / zone to inspect (e.g. 'example.com').",
                }
            },
            "required": ["domain"],
        },
    },
    {
        "name": "get_apps_overview",
        "description": "Retrieves an overview of all installed applications across PHP sites, Python apps, and Container / Railpack apps, including status, runtime port, and Git repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_type": {
                    "type": "string",
                    "enum": ["all", "php", "python", "container"],
                    "description": "Filter by application type. Defaults to 'all'.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_app_logs",
        "description": "Fetches recent deployment or runtime logs for a specific application to diagnose build failures or startup crashes.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "integer",
                    "description": "The application ID.",
                },
                "app_type": {
                    "type": "string",
                    "enum": ["container", "python", "php"],
                    "description": "The type of application.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of recent lines to retrieve (default: 50, max: 100).",
                },
            },
            "required": ["app_id", "app_type"],
        },
    },
    {
        "name": "get_databases_overview",
        "description": "Lists active database instances (PostgreSQL, MariaDB, SQLite) and created database names. Does NOT return passwords or sensitive row data.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_website_directory",
        "description": "Lists files and subdirectories inside an application or website root directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "string",
                    "description": "Target identifier in format 'kind:id' (e.g. 'static:1', 'php:2', 'python:3', 'container:4').",
                },
                "relative_path": {
                    "type": "string",
                    "description": "Relative directory path within the root (default: empty for root).",
                },
            },
            "required": ["target_id"],
        },
    },
    {
        "name": "read_website_file",
        "description": "Reads a code or configuration file (e.g. package.json, composer.json, nginx.conf, Dockerfile, index.php) from a website root directory in read-only mode.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "string",
                    "description": "Target identifier in format 'kind:id' (e.g. 'static:1', 'php:2', 'python:3', 'container:4').",
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative file path within the root.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum number of characters to read (default: 8000).",
                },
            },
            "required": ["target_id", "file_path"],
        },
    },
]


def get_tool_definitions(provider_type: str = "openai_compatible") -> List[Dict[str, Any]]:
    """
    Returns tool definitions formatted for the target provider:
    - OpenAI / DeepSeek / Groq format: list of {'type': 'function', 'function': {...}}
    - Anthropic Claude format: list of {'name': ..., 'description': ..., 'input_schema': {...}}
    """
    if provider_type == "anthropic":
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in RAW_TOOL_SCHEMAS
        ]

    # OpenAI-compatible format
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in RAW_TOOL_SCHEMAS
    ]
