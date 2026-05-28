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
    if "fetch fork --quiet" in joined:
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

    # Create a fake git repo and fresh cache
    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    cache_file = tmp_path / ".update_check"
    cache_file.write_text(
        json.dumps({"ts": time.time(), "behind": 3, "repo": str(Path(__file__).resolve().parents[2])})
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
    assert any("fetch fork --quiet" in cmd for cmd in commands)
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
    assert any("fetch fork --quiet" in cmd for cmd in commands)
    assert any("HEAD..fork/main" in cmd for cmd in commands)
    assert all("HEAD..origin/main" not in cmd for cmd in commands)


def test_check_for_updates_no_git_dir(tmp_path, monkeypatch):
    """Falls back to PyPI check when .git directory doesn't exist anywhere."""
    import hermes_cli.banner as banner

    # Create a fake banner.py so the fallback path also has no .git
    fake_banner = tmp_path / "hermes_cli" / "banner.py"
    fake_banner.parent.mkdir(parents=True, exist_ok=True)
    fake_banner.touch()

    monkeypatch.setattr(banner, "__file__", str(fake_banner))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run") as mock_run:
        with patch("hermes_cli.banner.check_via_pypi", return_value=0):
            result = banner.check_for_updates()
    assert result == 0
    mock_run.assert_not_called()


def test_check_for_updates_fallback_to_project_root(tmp_path, monkeypatch):
    """Dev install: falls back to Path(__file__).parent.parent when HERMES_HOME has no git repo."""
    import hermes_cli.banner as banner

    project_root = Path(banner.__file__).parent.parent.resolve()
    if not (project_root / ".git").exists():
        pytest.skip("Not running from a git checkout")

    # Point HERMES_HOME at a temp dir with no hermes-agent/.git
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.banner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="0\n")
        result = banner.check_for_updates()
    # Should have fallen back to project root and run git commands
    assert mock_run.call_count >= 1


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


def test_invalidate_update_cache_clears_all_profiles(tmp_path):
    """_invalidate_update_cache() should delete .update_check from ALL profiles."""
    from hermes_cli.main import _invalidate_update_cache

    # Build a fake ~/.hermes with default + two named profiles
    default_home = tmp_path / ".hermes"
    default_home.mkdir()
    (default_home / ".update_check").write_text('{"ts":1,"behind":50}')

    profiles_root = default_home / "profiles"
    for name in ("ops", "dev"):
        p = profiles_root / name
        p.mkdir(parents=True)
        (p / ".update_check").write_text('{"ts":1,"behind":50}')

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(default_home)}):
        _invalidate_update_cache()

    # All three caches should be gone
    assert not (default_home / ".update_check").exists(), "default profile cache not cleared"
    assert not (profiles_root / "ops" / ".update_check").exists(), "ops profile cache not cleared"
    assert not (profiles_root / "dev" / ".update_check").exists(), "dev profile cache not cleared"


def test_invalidate_update_cache_no_profiles_dir(tmp_path):
    """Works fine when no profiles directory exists (single-profile setup)."""
    from hermes_cli.main import _invalidate_update_cache

    default_home = tmp_path / ".hermes"
    default_home.mkdir()
    (default_home / ".update_check").write_text('{"ts":1,"behind":5}')

    with patch.object(Path, "home", return_value=tmp_path), \
         patch.dict(os.environ, {"HERMES_HOME": str(default_home)}):
        _invalidate_update_cache()

    assert not (default_home / ".update_check").exists()
