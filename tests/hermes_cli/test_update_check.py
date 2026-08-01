"""Tests for the update check mechanism in hermes_cli.banner."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _mock_git_update_run(cmd, **kwargs):
    joined = " ".join(str(c) for c in cmd)

    if "main@{upstream}" in joined:
        return MagicMock(returncode=0, stdout="fork/main\n", stderr="")
    if joined.endswith("git remote"):
        return MagicMock(returncode=0, stdout="fork\norigin\n", stderr="")
    if "remote get-url fork" in joined:
        return MagicMock(returncode=0, stdout="git@github.com:user/hermes-agent.git\n", stderr="")
    if "remote get-url origin" in joined:
        return MagicMock(returncode=0, stdout="https://github.com/NousResearch/hermes-agent.git\n", stderr="")
    if "--is-shallow-repository" in joined:
        return MagicMock(returncode=0, stdout="false\n", stderr="")
    if "fetch fork main" in joined:
        return MagicMock(returncode=0, stdout="", stderr="")
    if "rev-list --count HEAD..fork/main" in joined:
        return MagicMock(returncode=0, stdout="5\n", stderr="")

    raise AssertionError(f"Unexpected git command: {joined}")


def test_version_string_no_v_prefix():
    """__version__ should be bare semver without a 'v' prefix."""
    from hermes_cli import __version__
    assert not __version__.startswith("v"), f"__version__ should not start with 'v', got {__version__!r}"


def test_check_for_updates_uses_cache(tmp_path, monkeypatch):
    """When cache is fresh, check_for_updates should return cached value without calling git."""
    from hermes_cli.banner import check_for_updates
    from hermes_cli import __version__

    # Create a fake git repo and fresh cache
    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    cache_file = tmp_path / ".update_check"
    cache_file.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "behind": 3,
                "repo": str(Path(__file__).resolve().parents[2]),
                "ver": __version__,
            }
        )
    )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run") as mock_run:
        result = check_for_updates()

    assert result == 3
    mock_run.assert_not_called()




def test_check_for_updates_expired_cache(tmp_path, monkeypatch):
    """When cache is expired, check_for_updates should refresh against main's upstream."""
    from hermes_cli.banner import check_for_updates

    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    # Write an expired cache (timestamp far in the past)
    cache_file = tmp_path / ".update_check"
    cache_file.write_text(json.dumps({"ts": 0, "behind": 1}))

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run", side_effect=_mock_git_update_run) as mock_run:
        result = check_for_updates()

    assert result == 5
    commands = [" ".join(str(a) for a in c.args[0]) for c in mock_run.call_args_list]
    assert any("fetch fork main --quiet" in cmd for cmd in commands)
    assert any("rev-list --count HEAD..fork/main" in cmd for cmd in commands)


def test_check_for_updates_uses_main_tracking_remote_instead_of_origin(tmp_path, monkeypatch):
    """Version/update checks should follow main's upstream, not assume origin/main."""
    from hermes_cli.banner import check_for_updates

    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    cache_file = tmp_path / ".update_check"
    cache_file.write_text(json.dumps({"ts": 0, "behind": 1}))

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run", side_effect=_mock_git_update_run) as mock_run:
        result = check_for_updates()

    assert result == 5
    commands = [" ".join(str(a) for a in c.args[0]) for c in mock_run.call_args_list]
    assert any("main@{upstream}" in cmd for cmd in commands)
    assert any("fetch fork main --quiet" in cmd for cmd in commands)
    assert any("HEAD..fork/main" in cmd for cmd in commands)
    assert all("HEAD..origin/main" not in cmd for cmd in commands)




def test_prefetch_non_blocking():
    """prefetch_update_check() should return immediately without blocking."""
    import hermes_cli.banner as banner

    # Reset module state
    banner._update_result = None
    banner._update_check_done = threading.Event()

    with patch.object(banner, "check_for_updates", return_value=5):
        start = time.monotonic()
        banner.prefetch_update_check()
        elapsed = time.monotonic() - start

        # Should return almost immediately (well under 1 second)
        assert elapsed < 1.0

        # Wait for the background thread to finish
        banner._update_check_done.wait(timeout=5)
        assert banner._update_result == 5




