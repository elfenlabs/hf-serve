"""Pull command — transfer a synced entry to a local directory."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hf_serve.storage import (
    MANIFEST_FILENAME,
    get_current_link,
)

logger = logging.getLogger(__name__)


@dataclass
class PullResult:
    """Result of a pull operation."""

    entry: str
    success: bool
    target: Path
    total_size: int | None = None
    file_count: int | None = None
    error: str | None = None


def _hardlink_tree(src: Path, dst: Path) -> tuple[int, int]:
    """Hardlink all files from src into dst, preserving directory structure.

    Returns:
        Tuple of (file_count, total_size).
    """
    file_count = 0
    total_size = 0

    for src_file in sorted(src.rglob("*")):
        if not src_file.is_file():
            continue

        rel = src_file.relative_to(src)
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        real_src = src_file.resolve()

        try:
            os.link(real_src, dst_file)
        except OSError:
            # Cross-filesystem or already exists — fall back to copy
            shutil.copy2(str(real_src), str(dst_file))

        size = dst_file.stat().st_size
        total_size += size
        file_count += 1

    return file_count, total_size


def pull_local(
    entry: str,
    target: Path,
    source: Path,
    *,
    delete: bool = True,
) -> PullResult:
    """Pull an entry from a local hf-serve storage root.

    Uses hardlinks when possible (same filesystem), falls back to copy.
    When delete=True, removes the target directory first for a clean pull.

    Args:
        entry: Entry name.
        target: Destination directory.
        source: Local hf-serve storage root (e.g., /data/hf-serve).
        delete: Remove target before pulling (like rsync --delete).
    """
    logger.info("pull_started: entry=%s target=%s source=%s", entry, target, source)

    current = get_current_link(source, entry)
    if not current.exists():
        return PullResult(
            entry=entry,
            success=False,
            target=target,
            error=f"Entry '{entry}' has no synced revision at {current}",
        )

    # Resolve the symlink to get the actual revision directory
    resolved = current.resolve()
    if not resolved.is_dir():
        return PullResult(
            entry=entry,
            success=False,
            target=target,
            error=f"Current symlink does not point to a valid directory: {resolved}",
        )

    try:
        # Clean target if requested
        if delete and target.exists():
            shutil.rmtree(target)
            logger.debug("Removed existing target: %s", target)

        target.mkdir(parents=True, exist_ok=True)
        file_count, total_size = _hardlink_tree(resolved, target)

        # Verify manifest exists
        manifest_path = target / MANIFEST_FILENAME
        if not manifest_path.exists():
            return PullResult(
                entry=entry,
                success=False,
                target=target,
                error="Pull completed but manifest is missing",
            )

        logger.info(
            "pull_completed: entry=%s files=%d size=%d",
            entry,
            file_count,
            total_size,
        )

        return PullResult(
            entry=entry,
            success=True,
            target=target,
            total_size=total_size,
            file_count=file_count,
        )

    except Exception as e:
        error_msg = f"Pull failed for {entry}: {e}"
        logger.error("pull_failed: %s", error_msg)
        return PullResult(
            entry=entry,
            success=False,
            target=target,
            error=error_msg,
        )


def pull_rsync(
    entry: str,
    target: Path,
    server: str,
    source: Path,
    *,
    delete: bool = True,
) -> PullResult:
    """Pull an entry from a remote hf-serve server via rsync.

    Wraps: rsync -a [--delete] <server>:<source>/entries/<entry>/current/ <target>/

    Args:
        entry: Entry name.
        target: Local destination directory.
        server: Remote hostname or SSH alias.
        source: Remote hf-serve storage root path.
        delete: Pass --delete to rsync.
    """
    logger.info(
        "pull_started: entry=%s target=%s server=%s",
        entry,
        target,
        server,
    )

    # Check rsync is available
    if not shutil.which("rsync"):
        return PullResult(
            entry=entry,
            success=False,
            target=target,
            error="rsync is not installed or not in PATH",
        )

    remote_path = f"{server}:{source}/entries/{entry}/current/"
    target_str = f"{target}/"

    cmd = ["rsync", "-a", "--info=progress2"]
    if delete:
        cmd.append("--delete")
    cmd.extend([remote_path, target_str])

    logger.info("Running: %s", " ".join(cmd))

    try:
        target.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            return PullResult(
                entry=entry,
                success=False,
                target=target,
                error=f"rsync failed (exit {result.returncode}): {stderr}",
            )

        # Verify manifest
        manifest_path = target / MANIFEST_FILENAME
        if not manifest_path.exists():
            return PullResult(
                entry=entry,
                success=False,
                target=target,
                error="rsync completed but manifest is missing at target",
            )

        # Compute stats from actual files
        total_size = 0
        file_count = 0
        for f in target.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                file_count += 1

        logger.info(
            "pull_completed: entry=%s files=%d size=%d",
            entry,
            file_count,
            total_size,
        )

        return PullResult(
            entry=entry,
            success=True,
            target=target,
            total_size=total_size,
            file_count=file_count,
        )

    except Exception as e:
        error_msg = f"rsync pull failed for {entry}: {e}"
        logger.error("pull_failed: %s", error_msg)
        return PullResult(
            entry=entry,
            success=False,
            target=target,
            error=error_msg,
        )
