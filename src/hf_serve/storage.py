"""Storage layout management and file materialization."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from hf_serve.models import FileInfo, Manifest

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "hf-serve-manifest.json"


def get_entries_dir(root: Path) -> Path:
    """Return the top-level entries directory."""
    return root / "entries"


def get_entry_dir(root: Path, entry: str) -> Path:
    """Return the directory for a specific entry."""
    return get_entries_dir(root) / entry


def get_revision_dir(root: Path, entry: str, commit_hash: str) -> Path:
    """Return the directory for a specific revision."""
    return get_entry_dir(root, entry) / "revisions" / commit_hash


def get_partial_dir(root: Path, entry: str, commit_hash: str) -> Path:
    """Return the partial (in-progress) directory for a revision."""
    return get_entry_dir(root, entry) / "revisions" / f"{commit_hash}.partial"


def get_current_link(root: Path, entry: str) -> Path:
    """Return the path to the 'current' symlink for an entry."""
    return get_entry_dir(root, entry) / "current"


def ensure_directories(root: Path) -> None:
    """Create the top-level storage directories if needed."""
    get_entries_dir(root).mkdir(parents=True, exist_ok=True)


def materialize_revision(
    snapshot_path: Path,
    target_dir: Path,
) -> list[FileInfo]:
    """Hardlink all files from a HF snapshot into the target directory.

    Args:
        snapshot_path: Path to the HF cache snapshot directory.
        target_dir: Destination directory for hardlinked files.

    Returns:
        List of FileInfo for all materialized files.

    Raises:
        OSError: If hardlinking fails (e.g., cross-filesystem).
    """
    files: list[FileInfo] = []
    target_dir.mkdir(parents=True, exist_ok=True)

    for src_file in sorted(snapshot_path.rglob("*")):
        if not src_file.is_file():
            continue

        rel = src_file.relative_to(snapshot_path)
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        # HF cache uses symlinks (snapshot → blob). Resolve to the real
        # file so os.link() gets the actual inode, not the symlink.
        real_src = src_file.resolve()
        os.link(real_src, dst)
        size = dst.stat().st_size
        files.append(FileInfo(path=str(rel), size=size))
        logger.debug("Hardlinked: %s (%d bytes)", rel, size)

    return files


def write_manifest(
    target_dir: Path,
    *,
    entry: str,
    repository: str,
    repo_type: str,
    revision: str,
    commit_hash: str,
    files: list[FileInfo],
) -> Manifest:
    """Write the hf-serve-manifest.json into a materialized revision directory.

    Returns:
        The written Manifest object.
    """
    total_size = sum(f.size for f in files)
    now = datetime.now(timezone.utc)

    manifest = Manifest(
        entry=entry,
        repository=repository,
        repo_type=repo_type,
        revision=revision,
        commit_hash=commit_hash,
        synced_at=now,
        files=files,
        total_size=total_size,
    )

    manifest_path = target_dir / MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2))
    logger.info("Wrote manifest: %s", manifest_path)

    return manifest


def read_manifest(root: Path, entry: str, commit_hash: str | None = None) -> Manifest | None:
    """Read a manifest from a specific revision or the current symlink.

    Args:
        root: Storage root directory.
        entry: Entry name.
        commit_hash: Specific revision hash. If None, reads from 'current'.

    Returns:
        Parsed Manifest, or None if not found.
    """
    if commit_hash:
        manifest_path = get_revision_dir(root, entry, commit_hash) / MANIFEST_FILENAME
    else:
        current = get_current_link(root, entry)
        if not current.exists():
            return None
        manifest_path = current / MANIFEST_FILENAME

    if not manifest_path.exists():
        return None

    data = json.loads(manifest_path.read_text())
    return Manifest(**data)


def atomic_update_current(entry_dir: Path, commit_hash: str) -> None:
    """Atomically update the 'current' symlink to point at a revision.

    Uses a temporary symlink + os.replace() for atomicity on Linux.

    Args:
        entry_dir: The entry's directory (e.g., entries/qwen3-32b-awq/).
        commit_hash: The revision hash to point 'current' at.
    """
    current = entry_dir / "current"
    current_tmp = entry_dir / "current.tmp"

    target = Path("revisions") / commit_hash

    # Clean up any stale tmp link
    if current_tmp.is_symlink() or current_tmp.exists():
        current_tmp.unlink()

    os.symlink(target, current_tmp)
    os.replace(current_tmp, current)
    logger.info("Updated current -> %s", target)


def cleanup_partial(root: Path, entry: str, commit_hash: str) -> None:
    """Remove a partial revision directory if it exists."""
    import shutil

    partial = get_partial_dir(root, entry, commit_hash)
    if partial.exists():
        shutil.rmtree(partial)
        logger.info("Cleaned up partial: %s", partial)
