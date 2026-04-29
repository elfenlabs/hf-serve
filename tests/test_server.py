"""Tests for the HTTP API server."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hf_serve.config import AppConfig, EntryConfig, StorageConfig
from hf_serve.models import EntryStatus, FileInfo
from hf_serve.server import app_state, create_app_from_state
from hf_serve.state import StateStore
from hf_serve.storage import (
    atomic_update_current,
    get_entry_dir,
    get_revision_dir,
    write_manifest,
)


@pytest.fixture
def server_root(tmp_path: Path) -> Path:
    """Create a storage root with a synced entry for testing."""
    root = tmp_path / "storage"

    # Create a synced entry
    entry_dir = root / "entries" / "test-model"
    rev_dir = entry_dir / "revisions" / "abc123"
    rev_dir.mkdir(parents=True)

    (rev_dir / "config.json").write_text('{"key": "value"}')
    (rev_dir / "weights.bin").write_bytes(b"\x00" * 200)

    write_manifest(
        rev_dir,
        entry="test-model",
        repository="org/test-model",
        repo_type="model",
        revision="main",
        commit_hash="abc123",
        files=[
            FileInfo(path="config.json", size=16),
            FileInfo(path="weights.bin", size=200),
        ],
    )

    atomic_update_current(entry_dir, "abc123")

    return root


@pytest.fixture
def client(server_root: Path) -> TestClient:
    """Create a test client with pre-configured server state."""
    config = AppConfig(
        storage=StorageConfig(root=server_root),
        entries={
            "test-model": EntryConfig(repository="org/test-model"),
            "unsynced-model": EntryConfig(repository="org/unsynced"),
        },
    )

    state = StateStore(server_root / "state.db")
    state.set_status(
        entry="test-model",
        status=EntryStatus.READY,
        commit_hash="abc123",
        total_size=216,
    )

    app_state.config = config
    app_state.state = state

    app = create_app_from_state()
    test_client = TestClient(app)

    yield test_client

    state.close()


class TestHealthz:
    def test_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestListEntries:
    def test_lists_all_entries(self, client: TestClient) -> None:
        resp = client.get("/v1/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2

    def test_synced_entry_has_status(self, client: TestClient) -> None:
        resp = client.get("/v1/entries")
        entries = {e["name"]: e for e in resp.json()["entries"]}

        synced = entries["test-model"]
        assert synced["status"] == "ready"
        assert synced["commit_hash"] == "abc123"
        assert synced["total_size"] == 216
        assert synced["repository"] == "org/test-model"

    def test_unsynced_entry_unknown(self, client: TestClient) -> None:
        resp = client.get("/v1/entries")
        entries = {e["name"]: e for e in resp.json()["entries"]}

        unsynced = entries["unsynced-model"]
        assert unsynced["status"] == "unknown"
        assert unsynced["commit_hash"] is None


class TestGetEntry:
    def test_returns_detail(self, client: TestClient) -> None:
        resp = client.get("/v1/entries/test-model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-model"
        assert data["repository"] == "org/test-model"
        assert data["status"] == "ready"
        assert data["file_count"] == 2
        assert data["path"] is not None

    def test_unknown_entry_404(self, client: TestClient) -> None:
        resp = client.get("/v1/entries/nonexistent")
        assert resp.status_code == 404


class TestGetManifest:
    def test_returns_manifest(self, client: TestClient) -> None:
        resp = client.get("/v1/entries/test-model/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entry"] == "test-model"
        assert data["commit_hash"] == "abc123"
        assert len(data["files"]) == 2

    def test_unknown_entry_404(self, client: TestClient) -> None:
        resp = client.get("/v1/entries/nonexistent/manifest")
        assert resp.status_code == 404

    def test_unsynced_entry_404(self, client: TestClient) -> None:
        resp = client.get("/v1/entries/unsynced-model/manifest")
        assert resp.status_code == 404
        assert "No manifest" in resp.json()["detail"]


class TestSyncEndpoints:
    def test_sync_unknown_entry_404(self, client: TestClient) -> None:
        resp = client.post("/v1/entries/nonexistent/sync")
        assert resp.status_code == 404
