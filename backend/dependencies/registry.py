"""Closed registry of dependency drivers shipped with SRV Panel core."""
from dependencies.docker.service import DockerDependencyService
from dependencies.git.service import GitDependencyService
from dependencies.python.service import PythonDependencyService

DEPENDENCY_REGISTRY = {
    "docker": DockerDependencyService,
    "git": GitDependencyService,
    "python": PythonDependencyService,
}

CORE_DEPENDENCY_IDS = frozenset(DEPENDENCY_REGISTRY)
