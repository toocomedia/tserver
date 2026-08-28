"""Shared canonical AppSpec fixture for focused engine tests."""


def canonical_app_spec(*, pinned: bool = False) -> dict:
    digest = "example.test/web@sha256:" + ("a" * 64) if pinned else None
    return {
        "name": "evidence_app",
        "display_name": "Evidence App",
        "web_service_name": "web",
        "web_port": 8080,
        "services": {
            "db": {
                "name": "db",
                "image_reference": "postgres:16-alpine",
                "pinned_digest": ("postgres@sha256:" + ("b" * 64)) if pinned else None,
                "internal_ports": [5432],
                "volumes": [{"name_suffix": "db-data", "container_mount_path": "/var/lib/postgresql/data"}],
                "depends_on": [],
                "environment_defaults": {"POSTGRES_USER": "app"},
                "command": None,
                "cpu_limit": "0.5",
                "memory_limit_mb": 256,
                "health_check": {"probe_type": "command", "command": ["pg_isready", "-U", "app"]},
            },
            "web": {
                "name": "web",
                "image_reference": "example.test/web:v1",
                "pinned_digest": digest,
                "internal_ports": [8080],
                "volumes": [],
                "depends_on": ["db"],
                "environment_defaults": {"NODE_ENV": "production"},
                "command": None,
                "cpu_limit": "1.0",
                "memory_limit_mb": 512,
                "health_check": {"probe_type": "http", "http_path": "/ready", "http_port": 8080},
            },
        },
        "required_secrets": [{
            "key": "DB_PASSWORD",
            "purpose": "Database password",
            "generator": "password",
            "rotate": False,
            "service_name": "db",
            "environment_key": "POSTGRES_PASSWORD",
        }],
        "default_environment": {"BASE_URL": "https://app.example.test"},
        "url_templates": {"DATABASE_URL": "postgresql://app:{DB_PASSWORD}@{db}:5432/app"},
    }

