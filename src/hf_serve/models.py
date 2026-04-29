"""Shared data models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class FileInfo(BaseModel):
    """Metadata for a single file in a materialized revision."""

    path: str
    size: int


class Manifest(BaseModel):
    """Manifest written into every materialized revision directory."""

    entry: str
    repository: str
    repo_type: str
    revision: str
    commit_hash: str
    synced_at: datetime
    files: list[FileInfo]
    total_size: int


class EntryStatus(str, Enum):
    """Possible states for a synced entry."""

    UNKNOWN = "unknown"
    SYNCING = "syncing"
    READY = "ready"
    FAILED = "failed"
