"""Tests for the SQLite state store."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from hf_serve.models import EntryStatus
from hf_serve.state import StateStore


@pytest.fixture
def state(tmp_path: Path) -> StateStore:
    """Create a fresh StateStore."""
    store = StateStore(tmp_path / "state.db")
    yield store
    store.close()


class TestStateStore:
    def test_get_nonexistent(self, state: StateStore) -> None:
        assert state.get_status("nonexistent") is None

    def test_set_and_get(self, state: StateStore) -> None:
        state.set_status("model-a", EntryStatus.SYNCING)
        row = state.get_status("model-a")
        assert row is not None
        assert row.entry == "model-a"
        assert row.status == EntryStatus.SYNCING

    def test_update_status(self, state: StateStore) -> None:
        state.set_status("model-a", EntryStatus.SYNCING)
        now = datetime.now(timezone.utc)
        state.set_status(
            "model-a",
            EntryStatus.READY,
            commit_hash="abc123",
            synced_at=now,
            total_size=42000,
        )

        row = state.get_status("model-a")
        assert row is not None
        assert row.status == EntryStatus.READY
        assert row.commit_hash == "abc123"
        assert row.total_size == 42000

    def test_preserves_values_on_partial_update(self, state: StateStore) -> None:
        """COALESCE should preserve existing commit_hash when not provided."""
        now = datetime.now(timezone.utc)
        state.set_status(
            "model-a",
            EntryStatus.READY,
            commit_hash="abc123",
            synced_at=now,
            total_size=1000,
        )

        # Update only status, not commit_hash
        state.set_status("model-a", EntryStatus.SYNCING)

        row = state.get_status("model-a")
        assert row is not None
        assert row.status == EntryStatus.SYNCING
        assert row.commit_hash == "abc123"  # preserved

    def test_error_message_cleared_on_success(self, state: StateStore) -> None:
        state.set_status("model-a", EntryStatus.FAILED, error_message="download failed")
        state.set_status("model-a", EntryStatus.READY, error_message=None)

        row = state.get_status("model-a")
        assert row is not None
        assert row.error_message is None

    def test_list_statuses(self, state: StateStore) -> None:
        state.set_status("alpha", EntryStatus.READY)
        state.set_status("beta", EntryStatus.SYNCING)

        rows = state.list_statuses()
        entries = [r.entry for r in rows]
        assert entries == ["alpha", "beta"]  # ordered by entry name

    def test_list_empty(self, state: StateStore) -> None:
        assert state.list_statuses() == []
