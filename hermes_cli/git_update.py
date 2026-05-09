from __future__ import annotations

"""Git remote resolution helpers for Hermes update/version checks."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Optional

OFFICIAL_REPO_URLS = {
    "https://github.com/NousResearch/hermes-agent.git",
    "git@github.com:NousResearch/hermes-agent.git",
    "https://github.com/NousResearch/hermes-agent",
    "git@github.com:NousResearch/hermes-agent",
}
OFFICIAL_REPO_URL = "https://github.com/NousResearch/hermes-agent.git"


@dataclass(frozen=True)
class RemoteRef:
    remote: str
    branch: str
    ref: str
    source: str
    url: Optional[str] = None


def normalize_git_url(url: str) -> str:
    normalized = (url or "").strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def is_official_repo_url(url: Optional[str]) -> bool:
    if not url:
        return False
    normalized = normalize_git_url(url)
    return any(normalized == normalize_git_url(candidate) for candidate in OFFICIAL_REPO_URLS)


def get_remote_url(git_cmd: list[str], cwd: Path, remote: str) -> Optional[str]:
    try:
        result = subprocess.run(
            git_cmd + ["remote", "get-url", remote],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def get_remote_urls(git_cmd: list[str], cwd: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            git_cmd + ["remote"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except Exception:
        return {}

    if result.returncode != 0:
        return {}

    remotes: dict[str, str] = {}
    for name in result.stdout.splitlines():
        remote = name.strip()
        if not remote:
            continue
        url = get_remote_url(git_cmd, cwd, remote)
        if url:
            remotes[remote] = url
    return remotes


def find_official_remote(git_cmd: list[str], cwd: Path) -> tuple[Optional[str], Optional[str]]:
    for remote, url in get_remote_urls(git_cmd, cwd).items():
        if is_official_repo_url(url):
            return remote, url
    return None, None


def get_branch_upstream_ref(git_cmd: list[str], cwd: Path, branch: str = "HEAD") -> Optional[str]:
    try:
        result = subprocess.run(
            git_cmd + ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{upstream}}"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ref = result.stdout.strip()
            return ref or None
    except Exception:
        pass
    return None


def split_remote_ref(ref: str) -> Optional[tuple[str, str]]:
    if not ref or "/" not in ref:
        return None
    remote, branch = ref.split("/", 1)
    if not remote or not branch:
        return None
    return remote, branch


def remote_branch_exists(git_cmd: list[str], cwd: Path, remote: str, branch: str) -> bool:
    try:
        result = subprocess.run(
            git_cmd + ["rev-parse", "--verify", f"refs/remotes/{remote}/{branch}"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def resolve_update_target(git_cmd: list[str], cwd: Path, branch: str = "main") -> Optional[RemoteRef]:
    """Resolve the remote branch Hermes should compare/pull against.

    Priority:
    1. The configured upstream for the local branch (e.g. ``main@{upstream}``)
    2. Any remote that points at the official Hermes repository
    3. ``origin/<branch>``
    4. First remote that already has ``<branch>``
    """
    upstream_ref = get_branch_upstream_ref(git_cmd, cwd, branch)
    if upstream_ref:
        parsed = split_remote_ref(upstream_ref)
        if parsed is not None:
            remote, upstream_branch = parsed
            return RemoteRef(
                remote=remote,
                branch=upstream_branch,
                ref=upstream_ref,
                source="tracking",
                url=get_remote_url(git_cmd, cwd, remote),
            )

    official_remote, official_url = find_official_remote(git_cmd, cwd)
    if official_remote and remote_branch_exists(git_cmd, cwd, official_remote, branch):
        return RemoteRef(
            remote=official_remote,
            branch=branch,
            ref=f"{official_remote}/{branch}",
            source="official",
            url=official_url,
        )

    if remote_branch_exists(git_cmd, cwd, "origin", branch):
        return RemoteRef(
            remote="origin",
            branch=branch,
            ref=f"origin/{branch}",
            source="fallback",
            url=get_remote_url(git_cmd, cwd, "origin"),
        )

    for remote, url in get_remote_urls(git_cmd, cwd).items():
        if remote_branch_exists(git_cmd, cwd, remote, branch):
            return RemoteRef(
                remote=remote,
                branch=branch,
                ref=f"{remote}/{branch}",
                source="fallback",
                url=url,
            )

    return None
