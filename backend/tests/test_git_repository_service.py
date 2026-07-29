import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from dependencies.git import repository_service


class GitRepositoryValidationTests(unittest.TestCase):
    def test_accepts_https_and_ssh_without_dot_git(self):
        repository_service.validate_source(
            "https://github.com/example/project", "main"
        )
        repository_service.validate_source(
            "git@github.com:example/project", "release/v1"
        )

    def test_rejects_credentials_and_shell_syntax(self):
        for url in (
            "https://token@github.com/example/project",
            "file:///tmp/project",
            "https://github.com/example/project;touch-x",
        ):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                repository_service.validate_source(url, "main")

    def test_rejects_invalid_branch(self):
        with self.assertRaises(HTTPException):
            repository_service.validate_source(
                "https://github.com/example/project", "../main"
            )

    def test_lists_default_and_selectable_branches(self):
        results = [
            SimpleNamespace(returncode=0, stdout="ref: refs/heads/trunk\tHEAD\nabc\tHEAD\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="abc\trefs/heads/trunk\ndef\trefs/heads/release/v1\n", stderr=""),
        ]
        with patch.object(repository_service, "_run", side_effect=results):
            branches = repository_service.list_branches("https://github.com/example/project")
        self.assertEqual(branches.default_branch, "trunk")
        self.assertEqual(branches.branches, ["release/v1", "trunk"])
