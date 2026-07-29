"""Shared, provider-neutral Git repository operations for panel features."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from fastapi import HTTPException

GIT_URL_RE = re.compile(
    r"^(https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/-]+|"
    r"git@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+)$"
)
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


@dataclass(frozen=True)
class GitRevision:
    sha: str
    message: str
    committed_at: datetime | None


@dataclass(frozen=True)
class GitCheckout:
    path: Path
    repository_url: str
    branch: str
    revision: GitRevision


@dataclass(frozen=True)
class GitBranches:
    repository_url: str
    default_branch: str | None
    branches: list[str]


def validate_source(repository_url: str, branch: str) -> None:
    _validate_repository_url(repository_url)
    if not BRANCH_RE.fullmatch(branch):
        raise HTTPException(400, "Enter a valid branch name.")


def list_branches(repository_url: str) -> GitBranches:
    """List remote branches without cloning or executing repository code."""
    _validate_repository_url(repository_url)
    source_url, head = _remote_with_fallback(
        ["git", "ls-remote", "--symref", repository_url, "HEAD"],
        repository_url,
        45,
    )
    default_branch = _default_branch(head.stdout)
    source_url, refs = _remote_with_fallback(
        ["git", "ls-remote", "--heads", source_url], source_url, 45
    )
    branches = sorted({
        line.split("refs/heads/", 1)[1]
        for line in refs.stdout.splitlines()
        if "refs/heads/" in line
    })
    if not branches:
        raise HTTPException(400, "Repository has no selectable branches.")
    if default_branch not in branches:
        default_branch = branches[0]
    return GitBranches(source_url, default_branch, branches)


def clone(
    repository_url: str,
    branch: str,
    target: Path,
    *,
    revision: str | None = None,
    allow_default_branch: bool = False,
) -> GitCheckout:
    validate_source(repository_url, branch)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_url = repository_url
    result = _clone_branch(source_url, branch, target)
    fallback = _github_https_url(repository_url)
    if result.returncode and fallback:
        shutil.rmtree(target, ignore_errors=True)
        source_url = fallback
        result = _clone_branch(source_url, branch, target)
    if result.returncode and allow_default_branch and branch == "main":
        shutil.rmtree(target, ignore_errors=True)
        result = _run(["git", "clone", "--depth", "1", source_url, str(target)], 180)
        if not result.returncode:
            branch = _run(
                ["git", "-C", str(target), "branch", "--show-current"], 10
            ).stdout.strip() or branch
    if result.returncode:
        raise HTTPException(400, f"Repository check failed: {_safe_error(result)}")
    if revision:
        fetched = _run(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin", revision],
            180,
        )
        if fetched.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Git revision is unavailable: {_safe_error(fetched)}")
        checked = _run(["git", "-C", str(target), "checkout", "--detach", revision], 30)
        if checked.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Git checkout failed: {_safe_error(checked)}")
    return GitCheckout(target, source_url, branch, _revision(target))


@contextmanager
def temporary_clone(repository_url: str, branch: str, *, allow_default_branch=False):
    with tempfile.TemporaryDirectory(prefix="srv-panel-git-") as directory:
        yield clone(
            repository_url,
            branch,
            Path(directory) / "source",
            allow_default_branch=allow_default_branch,
        )


def remote_revision(repository_url: str, branch: str) -> GitRevision:
    validate_source(repository_url, branch)
    with temporary_clone(repository_url, branch) as checkout:
        return checkout.revision


def _clone_branch(url: str, branch: str, target: Path):
    return _run(
        ["git", "clone", "--depth", "1", "--branch", branch, url, str(target)],
        180,
    )


def _validate_repository_url(repository_url: str) -> None:
    if not GIT_URL_RE.fullmatch(repository_url):
        raise HTTPException(400, "Enter a valid HTTPS or SSH Git repository URL.")


def _remote_with_fallback(args: list[str], repository_url: str, timeout: int):
    result = _run(args, timeout)
    source_url = repository_url
    fallback = _github_https_url(repository_url)
    if result.returncode and fallback:
        source_url = fallback
        fallback_args = [fallback if item == repository_url else item for item in args]
        result = _run(fallback_args, timeout)
    if result.returncode:
        raise HTTPException(400, f"Repository check failed: {_safe_error(result)}")
    return source_url, result


def _default_branch(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    return None


def _revision(source: Path) -> GitRevision:
    result = _run(
        ["git", "-C", str(source), "log", "-1", "--format=%H%x1f%ct%x1f%s"],
        15,
    )
    if result.returncode or "\x1f" not in result.stdout:
        raise HTTPException(400, f"Could not read Git revision: {_safe_error(result)}")
    sha, timestamp, message = result.stdout.strip().split("\x1f", 2)
    committed = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    return GitRevision(sha=sha, message=message[:512], committed_at=committed)


def _run(args: list[str], timeout: int):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(400, f"Git operation failed: {str(exc)[:300]}") from exc


def _github_https_url(repository_url: str) -> str | None:
    match = re.fullmatch(r"git@github\.com:([A-Za-z0-9._~/-]+)", repository_url)
    return f"https://github.com/{match.group(1)}" if match else None


def _safe_error(result) -> str:
    output = (result.stderr or result.stdout or "Git operation failed.").strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return " ".join(lines[-3:])[:500]
