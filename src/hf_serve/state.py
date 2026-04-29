"""SQLite-backed state store for entry sync status."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from hf_serve.models import EntryStatus

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS entry_status (
    entry TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'unknown',
    commit_hash TEXT,
    synced_at TEXT,
    total_size INTEGER,
    error_message TEXT,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class EntryStatusRow:
    """A row from the entry_status table."""

    entry: str
    status: EntryStatus
    commit_hash: str | None
    synced_at: datetime | None
    total_size: int | None
    error_message: str | None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> EntryStatusRow:
        return cls(
            entry=row["entry"],
            status=EntryStatus(row["status"]),
            commit_hash=row["commit_hash"],
            synced_at=(
                datetime.fromisoformat(row["synced_at"]) if row["synced_at"] else None
            ),
            total_size=row["total_size"],
            error_message=row["error_message"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class StateStore:
    """SQLite state store for tracking entry sync status."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def get_status(self, entry: str) -> EntryStatusRow | None:
        """Get the status of a single entry."""
        row = self._conn.execute(
            "SELECT * FROM entry_status WHERE entry = ?", (entry,)
        ).fetchone()
        return EntryStatusRow.from_row(row) if row else None

    def set_status(
        self,
        entry: str,
        status: EntryStatus,
        *,
        commit_hash: str | None = None,
        synced_at: datetime | None = None,
        total_size: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Create or update the status for an entry."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """\
            INSERT INTO entry_status (entry, status, commit_hash, synced_at, total_size, error_message, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry) DO UPDATE SET
                status = excluded.status,
                commit_hash = COALESCE(excluded.commit_hash, entry_status.commit_hash),
                synced_at = COALESCE(excluded.synced_at, entry_status.synced_at),
                total_size = COALESCE(excluded.total_size, entry_status.total_size),
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (
                entry,
                status.value,
                commit_hash,
                synced_at.isoformat() if synced_at else None,
                total_size,
                error_message,
                now,
            ),
        )
        self._conn.commit()

    def list_statuses(self) -> list[EntryStatusRow]:
        """List all entry statuses."""
        rows = self._conn.execute(
            "SELECT * FROM entry_status ORDER BY entry"
        ).fetchall()
        return [EntryStatusRow.from_row(r) for r in rows]
