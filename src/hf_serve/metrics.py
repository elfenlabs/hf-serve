"""Prometheus metrics for hf-serve."""

from __future__ import annotations

import time
from pathlib import Path

from hf_serve.config import AppConfig
from hf_serve.models import EntryStatus
from hf_serve.state import StateStore


def generate_metrics(config: AppConfig, state: StateStore) -> str:
    """Generate Prometheus-format metrics text.

    Metrics exposed:
        hf_serve_entries_total          - Total number of configured entries
        hf_serve_entry_status           - Per-entry status (gauge, 1 for current status)
        hf_serve_entry_size_bytes       - Per-entry total size in bytes
        hf_serve_entry_last_sync_timestamp_seconds - Last sync time as Unix epoch
        hf_serve_sync_success_total     - Count of entries in ready state
        hf_serve_sync_failure_total     - Count of entries in failed state
    """
    lines: list[str] = []
    statuses = {row.entry: row for row in state.list_statuses()}

    # -- hf_serve_entries_total
    lines.append("# HELP hf_serve_entries_total Total number of configured entries.")
    lines.append("# TYPE hf_serve_entries_total gauge")
    lines.append(f"hf_serve_entries_total {len(config.entries)}")

    # -- hf_serve_entry_status (1 for active status, 0 otherwise)
    lines.append("")
    lines.append("# HELP hf_serve_entry_status Entry status (1 = active for this status label).")
    lines.append("# TYPE hf_serve_entry_status gauge")

    for name in config.entries:
        row = statuses.get(name)
        current_status = row.status if row else EntryStatus.UNKNOWN
        for status in EntryStatus:
            val = 1 if status == current_status else 0
            lines.append(
                f'hf_serve_entry_status{{entry="{name}",status="{status.value}"}} {val}'
            )

    # -- hf_serve_entry_size_bytes
    lines.append("")
    lines.append("# HELP hf_serve_entry_size_bytes Total size of entry in bytes.")
    lines.append("# TYPE hf_serve_entry_size_bytes gauge")

    for name in config.entries:
        row = statuses.get(name)
        size = row.total_size if row and row.total_size else 0
        lines.append(f'hf_serve_entry_size_bytes{{entry="{name}"}} {size}')

    # -- hf_serve_entry_last_sync_timestamp_seconds
    lines.append("")
    lines.append(
        "# HELP hf_serve_entry_last_sync_timestamp_seconds "
        "Unix timestamp of last successful sync."
    )
    lines.append("# TYPE hf_serve_entry_last_sync_timestamp_seconds gauge")

    for name in config.entries:
        row = statuses.get(name)
        if row and row.synced_at:
            ts = row.synced_at.timestamp()
        else:
            ts = 0
        lines.append(
            f'hf_serve_entry_last_sync_timestamp_seconds{{entry="{name}"}} {ts}'
        )

    # -- hf_serve_sync_success_total / hf_serve_sync_failure_total
    lines.append("")
    lines.append("# HELP hf_serve_sync_success_total Number of entries in ready state.")
    lines.append("# TYPE hf_serve_sync_success_total gauge")
    success = sum(1 for r in statuses.values() if r.status == EntryStatus.READY)
    lines.append(f"hf_serve_sync_success_total {success}")

    lines.append("")
    lines.append("# HELP hf_serve_sync_failure_total Number of entries in failed state.")
    lines.append("# TYPE hf_serve_sync_failure_total gauge")
    failures = sum(1 for r in statuses.values() if r.status == EntryStatus.FAILED)
    lines.append(f"hf_serve_sync_failure_total {failures}")

    lines.append("")
    return "\n".join(lines)
