"""Sync engine — downloads from Hugging Face and materializes entries."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from huggingface_hub import snapshot_download

from hf_serve.config import AppConfig, EntryConfig
from hf_serve.models import EntryStatus
from hf_serve.state import StateStore
from hf_serve.storage import (
    atomic_update_current,
    cleanup_partial,
    ensure_directories,
    get_entry_dir,
    get_partial_dir,
    get_revision_dir,
    materialize_revision,
    write_manifest,
)
from hf_serve.util import now_utc

logger = logging.getLogger(__name__)

# Pattern to extract commit hash from HF cache snapshot paths.
# e.g., .../snapshots/abc123def456/
_SNAPSHOT_HASH_RE = re.compile(r"/snapshots/([0-9a-f]+)/?")


@dataclass
class SyncResult:
    """Result of syncing a single entry."""

    entry: str
    success: bool
    commit_hash: str | None = None
    total_size: int | None = None
    error: str | None = None
    skipped: bool = False


def _extract_commit_hash(snapshot_path: Path) -> str | None:
    """Try to extract the commit hash from an HF snapshot path.

    Hugging Face stores snapshots at:
        <cache>/models--<org>--<name>/snapshots/<commit_hash>/
    """
    match = _SNAPSHOT_HASH_RE.search(str(snapshot_path))
    return match.group(1) if match else None


def _generate_fallback_hash(entry_name: str, repository: str, revision: str) -> str:
    """Generate a deterministic fallback hash when real commit hash is unavailable."""
    import hashlib

    timestamp = now_utc().isoformat()
    content = f"{entry_name}:{repository}:{revision}:{timestamp}"
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def sync_entry(config: AppConfig, entry_name: str, state: StateStore) -> SyncResult:
    """Sync a single entry from Hugging Face.

    Steps:
        1. Resolve entry from config
        2. Set status → syncing
        3. snapshot_download() with HF token
        4. Extract commit hash
        5. Skip if already materialized
        6. Materialize into .partial dir
        7. Write manifest
        8. Rename .partial → final
        9. Atomic symlink update
        10. Set status → ready

    On failure: set status → failed, clean up .partial.
    """
    entry_config = config.entries.get(entry_name)
    if entry_config is None:
        return SyncResult(
            entry=entry_name,
            success=False,
            error=f"Unknown entry: {entry_name}",
        )

    revision = config.resolve_revision(entry_config)
    root = config.storage.root

    logger.info(
        "sync_started: entry=%s repository=%s revision=%s",
        entry_name,
        entry_config.repository,
        revision,
    )

    # Mark as syncing
    state.set_status(entry_name, EntryStatus.SYNCING)
    commit_hash: str | None = None

    try:
        ensure_directories(root)

        # Resolve HF token from environment
        hf_token = os.environ.get(config.huggingface.token_env)

        # Download snapshot
        snapshot_path = Path(
            snapshot_download(
                repo_id=entry_config.repository,
                repo_type=entry_config.repo_type,
                revision=revision,
                allow_patterns=entry_config.allow_patterns,
                ignore_patterns=entry_config.ignore_patterns,
                cache_dir=str(config.storage.hf_cache),
                token=hf_token,
                max_workers=config.sync.max_workers,
            )
        )


        # Extract commit hash
        commit_hash = _extract_commit_hash(snapshot_path)
        if not commit_hash:
            commit_hash = _generate_fallback_hash(
                entry_name, entry_config.repository, revision
            )
            logger.warning(
                "Could not extract commit hash from path %s, using fallback: %s",
                snapshot_path,
                commit_hash,
            )

        # Check if revision already exists
        revision_dir = get_revision_dir(root, entry_name, commit_hash)
        if revision_dir.exists():
            logger.info(
                "Revision already materialized, updating symlink only: %s",
                commit_hash,
            )
            entry_dir = get_entry_dir(root, entry_name)
            atomic_update_current(entry_dir, commit_hash)

            # Read existing manifest for size info
            from hf_serve.storage import read_manifest

            manifest = read_manifest(root, entry_name, commit_hash)
            total_size = manifest.total_size if manifest else None

            state.set_status(
                entry_name,
                EntryStatus.READY,
                commit_hash=commit_hash,
                synced_at=now_utc(),
                total_size=total_size,
                error_message=None,
            )
            logger.info("sync_completed: entry=%s (already up-to-date)", entry_name)
            return SyncResult(
                entry=entry_name,
                success=True,
                commit_hash=commit_hash,
                total_size=total_size,
                skipped=True,
            )

        # Materialize into .partial directory
        partial_dir = get_partial_dir(root, entry_name, commit_hash)
        cleanup_partial(root, entry_name, commit_hash)  # remove stale partial

        files = materialize_revision(snapshot_path, partial_dir)

        # Write manifest
        manifest = write_manifest(
            partial_dir,
            entry=entry_name,
            repository=entry_config.repository,
            repo_type=entry_config.repo_type,
            revision=revision,
            commit_hash=commit_hash,
            files=files,
        )

        # Rename .partial → final
        os.rename(partial_dir, revision_dir)
        logger.info("entry_ready: %s at revision %s", entry_name, commit_hash)

        # Atomic symlink update
        entry_dir = get_entry_dir(root, entry_name)
        atomic_update_current(entry_dir, commit_hash)

        # Update state
        state.set_status(
            entry_name,
            EntryStatus.READY,
            commit_hash=commit_hash,
            synced_at=now_utc(),
            total_size=manifest.total_size,
            error_message=None,
        )

        logger.info(
            "sync_completed: entry=%s commit=%s size=%d",
            entry_name,
            commit_hash,
            manifest.total_size,
        )

        return SyncResult(
            entry=entry_name,
            success=True,
            commit_hash=commit_hash,
            total_size=manifest.total_size,
        )

    except Exception as e:
        error_msg = (
            f"Sync failed for {entry_name} "
            f"(repository={entry_config.repository}, revision={revision}): {e}"
        )
        logger.error("sync_failed: %s", error_msg)

        state.set_status(
            entry_name,
            EntryStatus.FAILED,
            error_message=str(e),
        )

        # Clean up partial if we know the commit hash
        if commit_hash:
            cleanup_partial(root, entry_name, commit_hash)

        return SyncResult(
            entry=entry_name,
            success=False,
            commit_hash=commit_hash,
            error=error_msg,
        )


@dataclass
class SyncAllResult:
    """Aggregated result of syncing all entries."""

    results: dict[str, SyncResult] = field(default_factory=dict)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results.values() if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results.values() if not r.success)

    @property
    def all_succeeded(self) -> bool:
        return self.failure_count == 0


def sync_all(config: AppConfig, state: StateStore) -> SyncAllResult:
    """Sync all entries defined in the configuration.

    Continues on individual entry failure.
    """
    result = SyncAllResult()
    for entry_name in config.entries:
        entry_result = sync_entry(config, entry_name, state)
        result.results[entry_name] = entry_result
    return result
