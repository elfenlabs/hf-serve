"""Garbage collection for old revisions."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from hf_serve.config import AppConfig
from hf_serve.storage import get_current_link, get_entries_dir, get_entry_dir

logger = logging.getLogger(__name__)


@dataclass
class GCEntryResult:
    """Result of garbage-collecting a single entry."""

    entry: str
    kept: list[str]
    removed: list[str]
    freed_bytes: int = 0


@dataclass
class GCResult:
    """Aggregated result of garbage collection."""

    entries: dict[str, GCEntryResult] = field(default_factory=dict)

    @property
    def total_removed(self) -> int:
        return sum(len(e.removed) for e in self.entries.values())

    @property
    def total_freed_bytes(self) -> int:
        return sum(e.freed_bytes for e in self.entries.values())


def _dir_size(path: Path) -> int:
    """Calculate total size of all files in a directory tree."""
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def _resolve_current_hash(entry_dir: Path) -> str | None:
    """Resolve the commit hash that 'current' points to."""
    current = entry_dir / "current"
    if not current.is_symlink():
        return None
    target = current.resolve()
    if target.exists():
        return target.name
    # Fallback: read the symlink target directly
    link_target = current.readlink()
    return link_target.name


def gc_entry(
    root: Path,
    entry_name: str,
    *,
    keep_revisions: int = 2,
    dry_run: bool = False,
) -> GCEntryResult:
    """Garbage-collect old revisions for a single entry.

    Keeps the current revision and the newest N revisions (by mtime).
    Never deletes 'current' or the revision it points to.

    Args:
        root: Storage root directory.
        entry_name: Entry name.
        keep_revisions: Number of revisions to keep (including current).
        dry_run: If True, don't actually delete anything.

    Returns:
        GCEntryResult with details of what was kept/removed.
    """
    entry_dir = get_entry_dir(root, entry_name)
    revisions_dir = entry_dir / "revisions"

    result = GCEntryResult(entry=entry_name, kept=[], removed=[])

    if not revisions_dir.exists():
        logger.debug("No revisions directory for %s, skipping", entry_name)
        return result

    # Find all revision directories (skip .partial dirs)
    rev_dirs = sorted(
        [
            d
            for d in revisions_dir.iterdir()
            if d.is_dir() and not d.name.endswith(".partial")
        ],
        key=lambda d: d.stat().st_mtime,
        reverse=True,  # newest first
    )

    if not rev_dirs:
        return result

    # Identify which revision 'current' points to — always keep it
    current_hash = _resolve_current_hash(entry_dir)

    # Build the keep set: current + newest N
    keep_set: set[str] = set()
    if current_hash:
        keep_set.add(current_hash)

    for d in rev_dirs:
        if len(keep_set) >= keep_revisions:
            break
        keep_set.add(d.name)

    # Process each revision
    for rev_dir in rev_dirs:
        if rev_dir.name in keep_set:
            result.kept.append(rev_dir.name)
        else:
            size = _dir_size(rev_dir)
            result.freed_bytes += size
            result.removed.append(rev_dir.name)

            if dry_run:
                logger.info(
                    "gc: would remove %s/%s (%d bytes)",
                    entry_name,
                    rev_dir.name,
                    size,
                )
            else:
                shutil.rmtree(rev_dir)
                logger.info(
                    "gc: removed %s/%s (%d bytes)",
                    entry_name,
                    rev_dir.name,
                    size,
                )

    # Also clean up any stale .partial directories
    for d in revisions_dir.iterdir():
        if d.is_dir() and d.name.endswith(".partial"):
            size = _dir_size(d)
            result.freed_bytes += size
            result.removed.append(d.name)
            if not dry_run:
                shutil.rmtree(d)
                logger.info("gc: removed stale partial %s/%s", entry_name, d.name)

    return result


def gc_all(
    config: AppConfig,
    *,
    keep_revisions: int = 2,
    dry_run: bool = False,
) -> GCResult:
    """Run garbage collection for all configured entries.

    Args:
        config: Application configuration.
        keep_revisions: Number of revisions to keep per entry.
        dry_run: If True, don't actually delete anything.

    Returns:
        GCResult with per-entry details.
    """
    result = GCResult()
    entries_dir = get_entries_dir(config.storage.root)

    if not entries_dir.exists():
        return result

    for entry_name in config.entries:
        entry_result = gc_entry(
            config.storage.root,
            entry_name,
            keep_revisions=keep_revisions,
            dry_run=dry_run,
        )
        result.entries[entry_name] = entry_result

    return result
