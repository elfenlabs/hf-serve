"""Tests for manifest model serialization."""

from __future__ import annotations

from datetime import datetime, timezone

from hf_serve.models import FileInfo, Manifest


class TestManifest:
    def test_total_size(self) -> None:
        m = Manifest(
            entry="test",
            repository="org/model",
            repo_type="model",
            revision="main",
            commit_hash="abc123",
            synced_at=datetime.now(timezone.utc),
            files=[
                FileInfo(path="a.json", size=100),
                FileInfo(path="b.safetensors", size=5000),
            ],
            total_size=5100,
        )
        assert m.total_size == 5100

    def test_json_round_trip(self) -> None:
        now = datetime.now(timezone.utc)
        m = Manifest(
            entry="test",
            repository="org/model",
            repo_type="model",
            revision="main",
            commit_hash="abc123",
            synced_at=now,
            files=[FileInfo(path="f.txt", size=42)],
            total_size=42,
        )

        json_str = m.model_dump_json()
        loaded = Manifest.model_validate_json(json_str)

        assert loaded.entry == "test"
        assert loaded.commit_hash == "abc123"
        assert len(loaded.files) == 1
        assert loaded.files[0].size == 42
        assert loaded.total_size == 42

    def test_empty_files_list(self) -> None:
        m = Manifest(
            entry="empty",
            repository="org/empty",
            repo_type="model",
            revision="main",
            commit_hash="000",
            synced_at=datetime.now(timezone.utc),
            files=[],
            total_size=0,
        )
        assert m.total_size == 0
        assert m.files == []


class TestFileInfo:
    def test_basic(self) -> None:
        f = FileInfo(path="config.json", size=1234)
        assert f.path == "config.json"
        assert f.size == 1234
