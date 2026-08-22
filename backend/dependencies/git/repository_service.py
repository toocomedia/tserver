"""Shared, provider-neutral Git repository operations for panel features."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time

from fastapi import HTTPException

import config

GIT_URL_RE = re.compile(
    r"^(https://[A-Za-z0-9.-]+/[A-Za-z0-9._~/-]+|"
    r"git@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+|"
    r"ssh://git@[A-Za-z0-9.-]+:[0-9]*/[A-Za-z0-9._~/-]+)$"
)
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

DEFAULT_KNOWN_HOSTS = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5A9okWi06BkX47997\n"
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV00/+hrVCbfc+GH5PfY4=\n"
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr97vFe55vo25nU+LoYNtxSp3+5KbCfVDn46DXMUeNufMtNrBiUJEQPMQrPXdTFHLitjnYTBuTIYTAtWFsUjVuBRzsVoBAzEI092ebTKWKD3aFBZZalClNV7SRBUU=\n"
    "gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAfuCHKVTjquxvt6CM6tdG4SLp1Btn/nOeHHE5UOzRdf\n"
    "gitlab.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBFSMqzJeV9rUzU4kWitGjeR4PWSa29SPqJ1fVkhtj3Hw9xjLVXVYrU9QlYGhXCnMUMhkWmF5rUCXCA3aRblqd2k=\n"
    "gitlab.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCsj2bNKTBSpIYRghAtNmAj6BpUET6FrDVAYMTQTKA3smF74uTWUssVNkDURVMWznKZz614PEUiClcaYkhNLgrCGgw7bihUfhyNHqFxWPzsP79eVKsBQuhRpQfDarls3AjkEFOz5LkuBlodUmL7d6GL5ugSmA9N2Z+zgGYqJ2wZs=\n"
    "bitbucket.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIazEu89wgQZ4bqs3d63QSMzYVa0QuenxIrlltnsCgNF\n"
    "bitbucket.org ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBK+6+D9oW4+VlHlJ4jO/H70d37hFkZc2g0bU5iO465hL2oW1zL0+F3tO6e38Z8t2bV0Wd1VjE/l8=\n"
    "bitbucket.org ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDQeJ07v4KhqRhnSTPp/2yg94FYvaB7W9E7uk1p04MoS559/314j0Yj1T6871h1d996K/D+n4NkW6Z8hT9+y0t2m7k1g52/68v8L5k1\n"
)


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


def ensure_known_hosts() -> Path:
    """Create and seed the known_hosts file with trusted public host keys."""
    path = Path(config.KNOWN_HOSTS_PATH)
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_KNOWN_HOSTS, encoding="utf-8")
        try:
            path.chmod(0o644)
        except OSError:
            pass
    return path


DRAFT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_and_resolve_draft_dir(draft_id: str) -> Path:
    """Validate draft ID format and verify containment inside the drafts root directory."""
    if not isinstance(draft_id, str) or not DRAFT_ID_RE.fullmatch(draft_id.strip()):
        raise HTTPException(400, "Invalid draft deploy key identifier.")
    drafts_root = (Path(config.DEPLOY_KEY_ROOT) / "drafts").resolve()
    draft_dir = (drafts_root / draft_id.strip()).resolve()
    try:
        if draft_dir.parent != drafts_root or not draft_dir.is_relative_to(drafts_root):
            raise HTTPException(400, "Invalid draft deploy key directory path.")
    except AttributeError:
        # Fallback for Python < 3.9 if applicable
        if str(draft_dir).startswith(str(drafts_root)) and draft_dir.parent == drafts_root:
            pass
        else:
            raise HTTPException(400, "Invalid draft deploy key directory path.")
    return draft_dir


def cleanup_expired_drafts(max_age_seconds: int = 3600) -> None:
    """Purge draft keys older than max_age_seconds."""
    drafts_root = (Path(config.DEPLOY_KEY_ROOT) / "drafts").resolve()
    if not drafts_root.exists():
        return
    now = time.time()
    for entry in drafts_root.iterdir():
        if entry.is_dir() and DRAFT_ID_RE.fullmatch(entry.name):
            try:
                if now - entry.stat().st_mtime > max_age_seconds:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass


def create_draft_deploy_key() -> tuple[str, str]:
    """Generate a temporary draft SSH ed25519 deploy key."""
    cleanup_expired_drafts()
    draft_id = secrets.token_hex(16)
    draft_dir = validate_and_resolve_draft_dir(draft_id)
    draft_dir.mkdir(parents=True, exist_ok=True)
    try:
        draft_dir.chmod(0o700)
    except OSError:
        pass
    key_file = draft_dir / "id_ed25519"
    pub_file = draft_dir / "id_ed25519.pub"

    result = _run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_file), "-C", "srv-panel-deploy-key"],
        timeout=30,
    )
    if result.returncode != 0 or not pub_file.exists():
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise HTTPException(500, f"Could not generate SSH deploy key: {_safe_error(result)}")

    try:
        key_file.chmod(0o600)
        pub_file.chmod(0o644)
    except OSError:
        pass

    public_key = pub_file.read_text(encoding="utf-8").strip()
    return draft_id, public_key


def get_draft_deploy_key_path(draft_id: str | None) -> Path | None:
    """Resolve and return validated path to the private key of an active draft."""
    if not draft_id or not draft_id.strip():
        return None
    draft_dir = validate_and_resolve_draft_dir(draft_id.strip())
    key_file = draft_dir / "id_ed25519"
    if not key_file.exists():
        raise HTTPException(400, "Draft deploy key not found or expired.")
    return key_file


def attach_deploy_key(draft_id: str, app_id: int) -> tuple[str, Path]:
    """Move a draft deploy key to app ownership and return (public_key_text, key_path)."""
    cleanup_expired_drafts()
    draft_dir = validate_and_resolve_draft_dir(draft_id)
    draft_key = draft_dir / "id_ed25519"
    draft_pub = draft_dir / "id_ed25519.pub"
    if not draft_key.exists() or not draft_pub.exists():
        raise HTTPException(400, "Draft deploy key not found or expired.")

    apps_root = Path(config.DEPLOY_KEY_ROOT).resolve()
    target_dir = (apps_root / str(app_id)).resolve()
    try:
        if target_dir.parent != apps_root or not target_dir.is_relative_to(apps_root):
            raise HTTPException(400, "Invalid target deploy key path.")
    except AttributeError:
        if target_dir.parent != apps_root:
            raise HTTPException(400, "Invalid target deploy key path.")

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        target_dir.chmod(0o700)
    except OSError:
        pass
    target_key = target_dir / "id_ed25519"
    target_pub = target_dir / "id_ed25519.pub"

    shutil.copy2(draft_key, target_key)
    shutil.copy2(draft_pub, target_pub)
    try:
        target_key.chmod(0o600)
        target_pub.chmod(0o644)
    except OSError:
        pass

    shutil.rmtree(draft_dir, ignore_errors=True)
    public_key = target_pub.read_text(encoding="utf-8").strip()
    return public_key, target_key


def delete_deploy_key(app_id: int) -> None:
    """Permanently delete the deploy key for an app."""
    apps_root = Path(config.DEPLOY_KEY_ROOT).resolve()
    target_dir = (apps_root / str(app_id)).resolve()
    try:
        if target_dir.parent == apps_root and target_dir.is_relative_to(apps_root):
            shutil.rmtree(target_dir, ignore_errors=True)
    except AttributeError:
        if target_dir.parent == apps_root:
            shutil.rmtree(target_dir, ignore_errors=True)


def delete_draft_deploy_key(draft_id: str) -> None:
    """Delete a draft deploy key directory."""
    try:
        draft_dir = validate_and_resolve_draft_dir(draft_id)
        shutil.rmtree(draft_dir, ignore_errors=True)
    except HTTPException:
        pass


def _git_env(ssh_key_path: str | Path | None = None) -> dict[str, str] | None:
    if not ssh_key_path:
        return None
    known_hosts = ensure_known_hosts()
    key_path_str = str(ssh_key_path).replace("\\", "/")
    known_hosts_str = str(known_hosts).replace("\\", "/")
    env = dict(os.environ)
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key_path_str} -o UserKnownHostsFile={known_hosts_str} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
    )
    return env


def validate_source(repository_url: str, branch: str, git_ref_type: str = "branch") -> None:
    _validate_repository_url(repository_url)
    if git_ref_type not in {"branch", "tag", "commit"}:
        raise HTTPException(400, "Git reference type must be branch, tag, or commit.")
    if git_ref_type == "commit":
        if not COMMIT_SHA_RE.fullmatch(branch):
            raise HTTPException(400, "Enter a valid Git commit SHA.")
    else:
        if not BRANCH_RE.fullmatch(branch):
            raise HTTPException(400, "Enter a valid branch or tag name.")


def list_branches(repository_url: str, *, ssh_key_path: str | Path | None = None) -> GitBranches:
    """List remote branches without cloning or executing repository code."""
    _validate_repository_url(repository_url)
    env = _git_env(ssh_key_path)
    source_url, head = _remote_with_fallback(
        ["git", "ls-remote", "--symref", repository_url, "HEAD"],
        repository_url,
        45,
        env=env,
    )
    default_branch = _default_branch(head.stdout)
    source_url, refs = _remote_with_fallback(
        ["git", "ls-remote", "--heads", source_url], source_url, 45, env=env,
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
    git_ref_type: str = "branch",
    ssh_key_path: str | Path | None = None,
    allow_default_branch: bool = False,
) -> GitCheckout:
    validate_source(repository_url, branch, git_ref_type)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_url = repository_url
    env = _git_env(ssh_key_path)

    if git_ref_type == "commit":
        result = _run(["git", "clone", "--depth", "1", source_url, str(target)], 180, env=env)
        if result.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Repository check failed: {_safe_error(result)}")
        fetched = _run(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin", branch],
            180,
            env=env,
        )
        if fetched.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Git commit fetch failed: {_safe_error(fetched)}")
        checked = _run(["git", "-C", str(target), "checkout", "--detach", branch], 30, env=env)
        if checked.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Git checkout failed: {_safe_error(checked)}")
        return GitCheckout(target, source_url, branch, _revision(target, env=env))

    result = _clone_branch(source_url, branch, target, env=env)
    fallback = _github_https_url(repository_url)
    if result.returncode and fallback and not ssh_key_path:
        shutil.rmtree(target, ignore_errors=True)
        source_url = fallback
    if result.returncode and allow_default_branch:
        shutil.rmtree(target, ignore_errors=True)
        result = _run(["git", "clone", "--depth", "1", source_url, str(target)], 180, env=env)
        if not result.returncode:
            branch = _run(
                ["git", "-C", str(target), "branch", "--show-current"], 10, env=env
            ).stdout.strip() or branch
    if result.returncode:
        raise HTTPException(400, f"Repository check failed: {_safe_error(result)}")
    if revision:
        fetched = _run(
            ["git", "-C", str(target), "fetch", "--depth", "1", "origin", revision],
            180,
            env=env,
        )
        if fetched.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Git revision is unavailable: {_safe_error(fetched)}")
        checked = _run(["git", "-C", str(target), "checkout", "--detach", revision], 30, env=env)
        if checked.returncode:
            shutil.rmtree(target, ignore_errors=True)
            raise HTTPException(400, f"Git checkout failed: {_safe_error(checked)}")
    return GitCheckout(target, source_url, branch, _revision(target, env=env))


@contextmanager
def temporary_clone(repository_url: str, branch: str, *, git_ref_type: str = "branch", ssh_key_path: str | Path | None = None, allow_default_branch=False):
    with tempfile.TemporaryDirectory(prefix="srv-panel-git-") as directory:
        yield clone(
            repository_url,
            branch,
            Path(directory) / "source",
            git_ref_type=git_ref_type,
            ssh_key_path=ssh_key_path,
            allow_default_branch=allow_default_branch,
        )


def remote_revision(repository_url: str, branch: str, *, git_ref_type: str = "branch", ssh_key_path: str | Path | None = None) -> GitRevision:
    validate_source(repository_url, branch, git_ref_type)
    with temporary_clone(repository_url, branch, git_ref_type=git_ref_type, ssh_key_path=ssh_key_path) as checkout:
        return checkout.revision


def _clone_branch(url: str, branch: str, target: Path, *, env: dict[str, str] | None = None):
    return _run(
        ["git", "clone", "--depth", "1", "--branch", branch, url, str(target)],
        180,
        env=env,
    )


def _validate_repository_url(repository_url: str) -> None:
    if not GIT_URL_RE.fullmatch(repository_url):
        raise HTTPException(400, "Enter a valid HTTPS or SSH Git repository URL.")


def _remote_with_fallback(args: list[str], repository_url: str, timeout: int, *, env: dict[str, str] | None = None):
    result = _run(args, timeout, env=env)
    source_url = repository_url
    fallback = _github_https_url(repository_url)
    if result.returncode and fallback and not env:
        source_url = fallback
        fallback_args = [fallback if item == repository_url else item for item in args]
        result = _run(fallback_args, timeout, env=env)
    if result.returncode:
        raise HTTPException(400, f"Repository check failed: {_safe_error(result)}")
    return source_url, result


def _default_branch(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    return None


def _revision(source: Path, *, env: dict[str, str] | None = None) -> GitRevision:
    result = _run(
        ["git", "-C", str(source), "log", "-1", "--format=%H%x1f%ct%x1f%s"],
        15,
        env=env,
    )
    if result.returncode or "\x1f" not in result.stdout:
        raise HTTPException(400, f"Could not read Git revision: {_safe_error(result)}")
    sha, timestamp, message = result.stdout.strip().split("\x1f", 2)
    committed = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    return GitRevision(sha=sha, message=message[:512], committed_at=committed)


def _run(args: list[str], timeout: int, *, env: dict[str, str] | None = None):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False, env=env
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
