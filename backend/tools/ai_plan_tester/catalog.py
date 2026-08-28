"""
tools/ai_plan_tester/catalog.py — Benchmark catalog of applications for AI plan testing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class BenchmarkApp:
    name: str
    slug: str
    tier: int
    source_type: str  # "git" | "image"
    target: str       # Repo URL or image reference
    expected_port: int
    expected_database: str  # "none" | "sqlite" | "postgresql" | "mariadb" | "clickhouse" | "redis"
    is_multi_container: bool
    description: str


_BENCHMARK_CATALOG: List[BenchmarkApp] = [
    # --- Tier 1: Standalone Single Container Images ---
    BenchmarkApp(
        name="Vaultwarden (Bitwarden Server)",
        slug="vaultwarden",
        tier=1,
        source_type="image",
        target="vaultwarden/server:latest",
        expected_port=80,
        expected_database="sqlite",
        is_multi_container=False,
        description="Lightweight Bitwarden password manager server",
    ),
    BenchmarkApp(
        name="Ghost CMS",
        slug="ghost",
        tier=1,
        source_type="image",
        target="ghost:5-alpine",
        expected_port=2368,
        expected_database="sqlite",
        is_multi_container=False,
        description="Headless Node.js publishing platform",
    ),
    BenchmarkApp(
        name="Adminer Database Manager",
        slug="adminer",
        tier=1,
        source_type="image",
        target="adminer:latest",
        expected_port=8080,
        expected_database="none",
        is_multi_container=False,
        description="Single-file PHP database management tool",
    ),
    BenchmarkApp(
        name="Uptime Kuma",
        slug="uptime-kuma",
        tier=1,
        source_type="image",
        target="louislam/uptime-kuma:1",
        expected_port=3001,
        expected_database="sqlite",
        is_multi_container=False,
        description="Self-hosted monitoring tool",
    ),
    BenchmarkApp(
        name="Nginx Web Server",
        slug="nginx",
        tier=1,
        source_type="image",
        target="nginx:alpine",
        expected_port=80,
        expected_database="none",
        is_multi_container=False,
        description="Standard lightweight reverse proxy / static server",
    ),

    # --- Tier 2: Single Container with Database Attachment ---
    BenchmarkApp(
        name="Umami Analytics",
        slug="umami",
        tier=2,
        source_type="image",
        target="umami-software/umami:postgresql-latest",
        expected_port=3000,
        expected_database="postgresql",
        is_multi_container=False,
        description="Privacy-focused web analytics backed by PostgreSQL",
    ),
    BenchmarkApp(
        name="WordPress",
        slug="wordpress",
        tier=2,
        source_type="image",
        target="wordpress:latest",
        expected_port=80,
        expected_database="mariadb",
        is_multi_container=False,
        description="World's most popular CMS requiring MySQL/MariaDB",
    ),
    BenchmarkApp(
        name="Directus Headless CMS",
        slug="directus",
        tier=2,
        source_type="image",
        target="directus/directus:latest",
        expected_port=8055,
        expected_database="postgresql",
        is_multi_container=False,
        description="Instant real-time REST/GraphQL API on SQL databases",
    ),
    BenchmarkApp(
        name="Plausible Analytics",
        slug="plausible",
        tier=2,
        source_type="image",
        target="plausible/analytics:latest",
        expected_port=8000,
        expected_database="postgresql",
        is_multi_container=False,
        description="Fast web analytics requiring PostgreSQL and ClickHouse",
    ),
    BenchmarkApp(
        name="Gitea Git Service",
        slug="gitea",
        tier=2,
        source_type="image",
        target="gitea/gitea:latest",
        expected_port=3000,
        expected_database="postgresql",
        is_multi_container=False,
        description="Painless self-hosted Git service in Go",
    ),

    # --- Tier 3: Multi-Container Compose Stacks ---
    BenchmarkApp(
        name="Shynet Analytics Stack",
        slug="shynet",
        tier=3,
        source_type="git",
        target="https://github.com/milesmcc/shynet",
        expected_port=8080,
        expected_database="postgresql",
        is_multi_container=True,
        description="Django web + Celery worker + PostgreSQL Compose stack",
    ),
    BenchmarkApp(
        name="Tianji All-in-One",
        slug="tianji",
        tier=3,
        source_type="git",
        target="https://github.com/msgbyte/tianji",
        expected_port=12345,
        expected_database="postgresql",
        is_multi_container=True,
        description="Website analytics + uptime monitor + server status",
    ),
    BenchmarkApp(
        name="NocoDB Smart Spreadsheet",
        slug="nocodb",
        tier=3,
        source_type="git",
        target="https://github.com/nocodb/nocodb",
        expected_port=8080,
        expected_database="postgresql",
        is_multi_container=True,
        description="Open source Airtable alternative with PostgreSQL backend",
    ),
    BenchmarkApp(
        name="Nextcloud All-in-One",
        slug="nextcloud",
        tier=3,
        source_type="git",
        target="https://github.com/nextcloud/all-in-one",
        expected_port=8080,
        expected_database="postgresql",
        is_multi_container=True,
        description="Self-hosted productivity suite with Redis and PostgreSQL",
    ),

    # --- Tier 4: Git Source / Railpack Apps ---
    BenchmarkApp(
        name="FastAPI Starter Source",
        slug="fastapi-app",
        tier=4,
        source_type="git",
        target="https://github.com/tiangolo/full-stack-fastapi-template",
        expected_port=8000,
        expected_database="postgresql",
        is_multi_container=True,
        description="Modern Python web API with PostgreSQL backend",
    ),
    BenchmarkApp(
        name="Next.js SSR Web App",
        slug="nextjs-app",
        tier=4,
        source_type="git",
        target="https://github.com/vercel/next.js",
        expected_port=3000,
        expected_database="none",
        is_multi_container=False,
        description="Node.js React full-stack application",
    ),
]


def get_catalog(tier: Optional[int] = None) -> List[BenchmarkApp]:
    """Returns benchmark apps, optionally filtered by tier (1-4)."""
    if tier is not None:
        return [app for app in _BENCHMARK_CATALOG if app.tier == tier]
    return list(_BENCHMARK_CATALOG)


def find_app_by_slug(slug: str) -> Optional[BenchmarkApp]:
    """Looks up an application by slug or name case-insensitively."""
    s = slug.strip().lower()
    for app in _BENCHMARK_CATALOG:
        if app.slug == s or app.name.lower() == s:
            return app
    return None


def resolve_app_target(target: str) -> BenchmarkApp:
    """
    Resolves an existing catalog app by slug/name, or constructs an ad-hoc
    BenchmarkApp instance from an arbitrary Git URL or Docker image reference.
    """
    existing = find_app_by_slug(target)
    if existing:
        return existing

    t = target.strip()
    is_git = bool(
        t.startswith("http://") or t.startswith("https://") or t.startswith("git@") or t.endswith(".git")
    )
    slug = re.sub(r"[^a-z0-9_-]+", "-", t.split("/")[-1].replace(".git", "").lower()).strip("-") or "custom-app"
    source_type = "git" if is_git else "image"

    return BenchmarkApp(
        name=f"Custom: {slug}",
        slug=slug,
        tier=3 if is_git else 1,
        source_type=source_type,
        target=t,
        expected_port=8080 if is_git else 80,
        expected_database="none",
        is_multi_container=is_git,
        description=f"Ad-hoc {source_type} test target",
    )
