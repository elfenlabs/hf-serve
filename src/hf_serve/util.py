"""Utility functions."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone


def human_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string.

    Examples:
        >>> human_size(0)
        '0 B'
        >>> human_size(1024)
        '1.0 KB'
        >>> human_size(42_100_000_000)
        '39.2 GB'
    """
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    for unit in units:
        if abs(size) < 1024.0:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} EB"


def now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger("hf_serve")
    root.setLevel(level)
    root.addHandler(handler)


def parse_bandwidth_limit(limit: str | int | None) -> int | None:
    """Parse bandwidth limit string (e.g. 500KB, 5MB, 5M, 1024) to bytes per second.

    Returns None if limit is None or empty.
    Raises ValueError if formatting is invalid.
    """
    if not limit:
        return None
    if isinstance(limit, int):
        return limit

    limit_str = str(limit).strip().upper()
    if not limit_str:
        return None

    import re

    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMG]?B?)$", limit_str)
    if not match:
        raise ValueError(f"Invalid bandwidth limit format: {limit}")

    value, unit = match.groups()
    val = float(value)

    multiplier = 1
    if unit in ("K", "KB"):
        multiplier = 1024
    elif unit in ("M", "MB"):
        multiplier = 1024 * 1024
    elif unit in ("G", "GB"):
        multiplier = 1024 * 1024 * 1024

    return int(val * multiplier)

