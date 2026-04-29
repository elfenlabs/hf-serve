"""Tests for pull operations."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hf_serve.models import FileInfo
from hf_serve.pull import pull_local, pull_rsync
from hf_serve.storage import (
    MANIFEST_FILENAME,
    atomic_update_current,
    write_manifest,
)


@pytest.fixture
def synced_entry(tmp_path: Path) -> Path:
    """Create a fake synced entry with a current symlink and manifest."""
    root = tmp_path / "storage"
    entry_dir = root / "entries" / "test-model"
    rev_dir = entry_dir / "revisions" / "abc123"
    rev_dir.mkdir(parents=True)

    # Create some files
    (rev_dir / "config.json").write_text('{"key": "value"}')
    (rev_dir / "model.bin").write_bytes(b"\x00" * 100)

    # Write manifest
    write_manifest(
        rev_dir,
        entry="test-model",
        repository="org/model",
        repo_type="model",
        revision="main",
        commit_hash="abc123",
        files=[
            FileInfo(path="config.json", size=16),
            FileInfo(path="model.bin", size=100),
        ],
    )

    # Create current symlink
    atomic_update_current(entry_dir, "abc123")

    return root


class TestPullLocal:
    def test_pulls_files(self, synced_entry: Path, tmp_path: Path) -> None:
        target = tmp_path / "model"
        result = pull_local("test-model", target, synced_entry)

        assert result.success
        assert result.file_count == 3  # config.json, model.bin, manifest
        assert (target / "config.json").exists()
        assert (target / "model.bin").exists()
        assert (target / MANIFEST_FILENAME).exists()

    def test_reports_size(self, synced_entry: Path, tmp_path: Path) -> None:
        target = tmp_path / "model"
        result = pull_local("test-model", target, synced_entry)

        assert result.success
        assert result.total_size is not None
        assert result.total_size > 0

    def test_delete_mode_cleans_target(
        self, synced_entry: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "model"
        target.mkdir()
        (target / "stale.txt").write_text("should be removed")

        result = pull_local("test-model", target, synced_entry, delete=True)

        assert result.success
        assert not (target / "stale.txt").exists()
        assert (target / "config.json").exists()

    def test_no_delete_preserves_stale(
        self, synced_entry: Path, tmp_path: Path
    ) -> None:
        target = tmp_path / "model"
        target.mkdir()
        (target / "stale.txt").write_text("should remain")

        result = pull_local("test-model", target, synced_entry, delete=False)

        assert result.success
        assert (target / "stale.txt").exists()
        assert (target / "config.json").exists()

    def test_missing_entry(self, synced_entry: Path, tmp_path: Path) -> None:
        target = tmp_path / "model"
        result = pull_local("nonexistent", target, synced_entry)

        assert not result.success
        assert "no synced revision" in result.error.lower()

    def test_creates_target_dir(self, synced_entry: Path, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "model"
        result = pull_local("test-model", target, synced_entry)

        assert result.success
        assert target.is_dir()


class TestPullRsync:
    def test_rsync_not_installed(self, tmp_path: Path) -> None:
        with patch("hf_serve.pull.shutil.which", return_value=None):
            result = pull_rsync(
                "test-model",
                tmp_path / "model",
                server="raptor",
                source=Path("/data/hf-serve"),
            )

        assert not result.success
        assert "rsync" in result.error.lower()
        assert "not installed" in result.error.lower()

    def test_rsync_command_shape(self, synced_entry: Path, tmp_path: Path) -> None:
        """Verify the rsync command is constructed correctly."""
        target = tmp_path / "model"
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)

            class FakeResult:
                returncode = 0
                stderr = ""

            return FakeResult()

        with (
            patch("hf_serve.pull.shutil.which", return_value="/usr/bin/rsync"),
            patch("hf_serve.pull.subprocess.run", side_effect=mock_run),
        ):
            # Will fail manifest check, but we can inspect the command
            result = pull_rsync(
                "my-model",
                target,
                server="raptor",
                source=Path("/data/hf-serve"),
            )

        assert len(calls) == 1
        cmd = calls[0]
        assert cmd[0] == "rsync"
        assert "-a" in cmd
        assert "--delete" in cmd
        assert "--info=progress2" in cmd
        assert "raptor:/data/hf-serve/entries/my-model/current/" in cmd
        assert str(target) + "/" in cmd

    def test_rsync_no_delete_flag(self, tmp_path: Path) -> None:
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)

            class FakeResult:
                returncode = 0
                stderr = ""

            return FakeResult()

        with (
            patch("hf_serve.pull.shutil.which", return_value="/usr/bin/rsync"),
            patch("hf_serve.pull.subprocess.run", side_effect=mock_run),
        ):
            pull_rsync(
                "my-model",
                tmp_path / "model",
                server="raptor",
                source=Path("/data/hf-serve"),
                delete=False,
            )

        assert "--delete" not in calls[0]
