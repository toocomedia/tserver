"""Closed registry of dependency drivers shipped with SRV Panel core."""
from dependencies.docker.service import DockerDependencyService
from dependencies.git.service import GitDependencyService
from dependencies.mariadb.service import MariaDBDependencyService
from dependencies.php.service import PHPDependencyService
from dependencies.python.service import PythonDependencyService
from dependencies.postgresql.service import PostgreSQLDependencyService

DEPENDENCY_REGISTRY = {
    "docker": DockerDependencyService,
    "git": GitDependencyService,
    "mariadb": MariaDBDependencyService,
    "php": PHPDependencyService,
    "python": PythonDependencyService,
    "postgresql": PostgreSQLDependencyService,
}

CORE_DEPENDENCY_IDS = frozenset(DEPENDENCY_REGISTRY)
