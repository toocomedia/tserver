"""Closed registry of dependency drivers shipped with SRV Panel core."""
from dependencies.docker.service import DockerDependencyService

DEPENDENCY_REGISTRY = {
    "docker": DockerDependencyService,
}

CORE_DEPENDENCY_IDS = frozenset(DEPENDENCY_REGISTRY)
