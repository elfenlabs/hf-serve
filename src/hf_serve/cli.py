"""Typer CLI for hf-serve."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hf_serve.config import AppConfig, load_config
from hf_serve.models import EntryStatus
from hf_serve.state import StateStore
from hf_serve.storage import get_current_link, read_manifest
from hf_serve.gc import gc_all
from hf_serve.pull import pull_local, pull_rsync
from hf_serve.sync import sync_all, sync_entry
from hf_serve.util import human_size, setup_logging

app = typer.Typer(
    name="hf-serve",
    help="Lightweight self-hosted Hugging Face model directory.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


class _Ctx:
    """Shared context populated by the global callback."""

    config: AppConfig | None = None
    state: StateStore | None = None


ctx = _Ctx()


def _require_config() -> tuple[AppConfig, StateStore]:
    """Helper to enforce config exists for config-dependent commands."""
    if ctx.config is None or ctx.state is None:
        err_console.print(
            "[bold red]Error:[/] Configuration file not found. Please provide one using "
            "[cyan]--config / -c[/] or place it at [cyan]~/.config/hf-serve/config.yaml[/]"
        )
        raise typer.Exit(1)
    return ctx.config, ctx.state


@app.callback()
def main(
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Path to config.yaml")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable verbose output")
    ] = False,
) -> None:
    """Lightweight self-hosted Hugging Face model directory."""
    setup_logging(verbose)
    
    resolved_config = config
    if resolved_config is None:
        default_paths = [
            Path("~/.config/hf-serve/config.yaml").expanduser(),
            Path("~/.config/hf-serve.yaml").expanduser(),
            Path("./config.yaml"),
        ]
        for p in default_paths:
            if p.exists():
                resolved_config = p
                break
        pass

    if resolved_config is not None:
        try:
            ctx.config = load_config(resolved_config)
            ctx.state = StateStore(ctx.config.storage.root / "state.db")
        except (FileNotFoundError, ValueError) as e:
            err_console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(1)


@app.command(name="list")
def list_(
    server: Annotated[
        Optional[str],
        typer.Option("--server", help="Remote server hostname for config-less list"),
    ] = None,
    source: Annotated[
        Optional[Path],
        typer.Option("--source", "-s", help="Remote hf-serve storage root"),
    ] = None,
) -> None:
    """List all configured entries and their sync status."""
    import tempfile
    import json
    import subprocess
    import shutil
    from datetime import datetime, timezone

    has_config = ctx.config is not None

    if not has_config and not server:
        server = "hf-gateway"

    if server:
        # REMOTE CONFIG-LESS LIST
        storage_root = source or Path("/data/hf-serve")
        console.print(f"Fetching entries from remote server [yellow]{server}[/yellow]...")

        if not shutil.which("rsync"):
            err_console.print("[bold red]Error:[/] rsync is not installed or not in PATH")
            raise typer.Exit(1)

        with tempfile.TemporaryDirectory() as tmpdir:
            remote_path = f"{server}:{storage_root}/entries/"
            cmd = [
                "rsync",
                "-am",
                "--include=*/",
                "--include=hf-serve-manifest.json",
                "--exclude=*",
                remote_path,
                tmpdir,
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                err_console.print(f"[bold red]Error listing remote entries:[/] {result.stderr.strip()}")
                raise typer.Exit(1)

            tmp_path = Path(tmpdir)
            entries_data = []

            for manifest_path in tmp_path.rglob("hf-serve-manifest.json"):
                try:
                    rel = manifest_path.relative_to(tmp_path)
                    entry_name = rel.parts[0]
                    rev = rel.parts[2]

                    with open(manifest_path) as f:
                        manifest_data = json.load(f)
                    
                    total_size = manifest_data.get("total_size", 0)
                    repo = manifest_data.get("repository", "-")
                    
                    mtime = manifest_path.stat().st_mtime
                    mtime_dt = datetime.fromtimestamp(mtime, timezone.utc)
                    age_delta = datetime.now(timezone.utc) - mtime_dt
                    
                    if age_delta.days > 0:
                        age = f"{age_delta.days}d"
                    elif age_delta.seconds // 3600 > 0:
                        age = f"{age_delta.seconds // 3600}h"
                    else:
                        age = f"{age_delta.seconds // 60}m"

                    entries_data.append({
                        "name": entry_name,
                        "repository": repo,
                        "revision": rev[:12],
                        "status": "Ready",
                        "size": human_size(total_size),
                        "age": age
                    })
                except Exception:
                    continue

            if not entries_data:
                console.print("\nNo entries found on remote server.")
                return

            table = Table(box=None, padding=(0, 2), show_header=True, header_style="bold white")
            table.add_column("NAME")
            table.add_column("REPOSITORY")
            table.add_column("REVISION")
            table.add_column("STATUS")
            table.add_column("SIZE")
            table.add_column("AGE")

            for item in sorted(entries_data, key=lambda x: x["name"]):
                table.add_row(
                    item["name"],
                    item["repository"],
                    item["revision"],
                    "[green]" + item["status"] + "[/]",
                    item["size"],
                    item["age"]
                )

            console.print()
            console.print(table)

    else:
        # LOCAL CONFIG-BASED LIST
        cfg, state = _require_config()

        table = Table(box=None, padding=(0, 2), show_header=True, header_style="bold white")
        table.add_column("NAME")
        table.add_column("REPOSITORY")
        table.add_column("REVISION")
        table.add_column("STATUS")
        table.add_column("SIZE")

        status_map = {row.entry: row for row in state.list_statuses()}

        for name in sorted(cfg.entries.keys()):
            entry = cfg.entries[name]
            row = status_map.get(name)
            status = row.status if row else EntryStatus.UNKNOWN

            status_style = {
                EntryStatus.READY: "[green]Ready[/]",
                EntryStatus.SYNCING: "[yellow]Syncing[/]",
                EntryStatus.FAILED: "[red]Failed[/]",
                EntryStatus.UNKNOWN: "[dim]Unknown[/]",
            }.get(status, str(status.value))

            size = human_size(row.total_size) if row and row.total_size else "-"
            revision = cfg.resolve_revision(entry)
            if row and row.commit_hash:
                revision = row.commit_hash[:12]

            table.add_row(name, entry.repository, revision, status_style, size)

        console.print()
        console.print(table)
        state.close()


@app.command(name="ls")
def ls(
    server: Annotated[
        Optional[str],
        typer.Option("--server", help="Remote server hostname for config-less list"),
    ] = None,
    source: Annotated[
        Optional[Path],
        typer.Option("--source", "-s", help="Remote hf-serve storage root"),
    ] = None,
) -> None:
    """List all configured entries and their sync status."""
    list_(server=server, source=source)


@app.command()
def status(
    entry: Annotated[Optional[str], typer.Argument(help="Entry name")] = None,
) -> None:
    """Show detailed status for an entry, or list all if no entry given."""
    cfg, state = _require_config()

    if entry is None:
        list_()
        return

    if entry not in cfg.entries:
        err_console.print(f"[bold red]Error:[/] Unknown entry: {entry}")
        raise typer.Exit(1)

    entry_config = cfg.entries[entry]
    row = state.get_status(entry)
    revision = cfg.resolve_revision(entry_config)

    status_val = row.status if row else EntryStatus.UNKNOWN
    status_str = {
        EntryStatus.READY: "[green]ready[/green]",
        EntryStatus.SYNCING: "[yellow]syncing[/yellow]",
        EntryStatus.FAILED: "[red]failed[/red]",
        EntryStatus.UNKNOWN: "[dim]unknown[/dim]",
    }.get(status_val, str(status_val.value))

    manifest = read_manifest(cfg.storage.root, entry)
    file_count = str(len(manifest.files)) if manifest else "-"
    current_link = get_current_link(cfg.storage.root, entry)
    path_str = str(current_link) if current_link.exists() else "-"

    info = (
        f"[bold]Entry:[/]        {entry}\n"
        f"[bold]Repository:[/]   {entry_config.repository}\n"
        f"[bold]Revision:[/]     {revision}\n"
        f"[bold]Commit:[/]       {row.commit_hash or '-' if row else '-'}\n"
        f"[bold]Status:[/]       {status_str}\n"
        f"[bold]Synced at:[/]    {row.synced_at.isoformat() if row and row.synced_at else '-'}\n"
        f"[bold]Size:[/]         {human_size(row.total_size) if row and row.total_size else '-'}\n"
        f"[bold]Files:[/]        {file_count}\n"
        f"[bold]Path:[/]         {path_str}"
    )

    if row and row.error_message:
        info += f"\n[bold red]Error:[/]        {row.error_message}"

    console.print(Panel(info, title=f"[cyan]{entry}[/cyan]", border_style="blue"))
    state.close()


@app.command()
def sync(
    entry: Annotated[Optional[str], typer.Argument(help="Entry to sync (omit for all)")] = None,
) -> None:
    """Sync one or all entries from Hugging Face."""
    cfg, state = _require_config()

    if entry is not None:
        if entry not in cfg.entries:
            err_console.print(f"[bold red]Error:[/] Unknown entry: {entry}")
            raise typer.Exit(1)

        with console.status(f"Syncing [cyan]{entry}[/cyan]...", spinner="dots"):
            result = sync_entry(cfg, entry, state)

        if result.success:
            if result.skipped:
                console.print(
                    f"[green]✓[/green] {entry}: already up-to-date "
                    f"(commit {result.commit_hash})"
                )
            else:
                console.print(
                    f"[green]✓[/green] {entry}: synced "
                    f"(commit {result.commit_hash}, "
                    f"{human_size(result.total_size or 0)})"
                )
        else:
            err_console.print(f"[red]✗[/red] {entry}: {result.error}")
            raise typer.Exit(1)
    else:
        console.print(f"Syncing [cyan]{len(cfg.entries)}[/cyan] entries...")

        with console.status("Syncing...", spinner="dots"):
            results = sync_all(cfg, state)

        for name, result in results.results.items():
            if result.success:
                tag = "[dim]skip[/dim]" if result.skipped else "[green]✓[/green]"
                size = human_size(result.total_size or 0)
                console.print(f"  {tag} {name}: {result.commit_hash} ({size})")
            else:
                console.print(f"  [red]✗[/red] {name}: {result.error}")

        console.print()
        console.print(
            f"Done: [green]{results.success_count} succeeded[/green], "
            f"[red]{results.failure_count} failed[/red]"
        )

        if not results.all_succeeded:
            raise typer.Exit(1)

    state.close()


@app.command()
def manifest(
    entry: Annotated[str, typer.Argument(help="Entry name")],
) -> None:
    """Display the manifest for an entry's current revision."""
    cfg, state = _require_config()

    if entry not in cfg.entries:
        err_console.print(f"[bold red]Error:[/] Unknown entry: {entry}")
        raise typer.Exit(1)

    m = read_manifest(cfg.storage.root, entry)
    if m is None:
        err_console.print(
            f"[bold red]Error:[/] No manifest found for {entry}. "
            "Has it been synced?"
        )
        raise typer.Exit(1)

    console.print_json(m.model_dump_json(indent=2))
    state.close()


@app.command()
def pull(
    entry: Annotated[str, typer.Argument(help="Entry name")],
    target: Annotated[Path, typer.Argument(help="Target directory to pull into")],
    source: Annotated[
        Optional[Path],
        typer.Option("--source", "-s", help="Local hf-serve storage root"),
    ] = None,
    server: Annotated[
        Optional[str],
        typer.Option("--server", help="Remote server hostname for rsync"),
    ] = None,
    no_delete: Annotated[
        bool,
        typer.Option("--no-delete", help="Don't remove stale files in target"),
    ] = False,
) -> None:
    """Pull a synced entry into a local directory.

    Use --source for local pulls, --server for rsync pulls.
    If neither is given, --source defaults to the config's storage root.
    """
    delete = not no_delete

    # Resolve the source path (storage root on source machine)
    storage_root = source
    if storage_root is None:
        if ctx.config is not None:
            storage_root = ctx.config.storage.root
        else:
            if server:
                storage_root = Path("/data/hf-serve")
            else:
                err_console.print(
                    "[bold red]Error:[/] Local pull requires a configuration file or explicit --source path."
                )
                raise typer.Exit(1)

    if server:
        # Rsync mode
        console.print(
            f"Pulling [cyan]{entry}[/cyan] from [yellow]{server}[/yellow] "
            f"via rsync → {target}"
        )
        with console.status(f"Pulling [cyan]{entry}[/cyan]...", spinner="dots"):
            result = pull_rsync(
                entry=entry,
                target=target,
                server=server,
                source=storage_root,
                delete=delete,
            )
    else:
        # Local mode
        console.print(
            f"Pulling [cyan]{entry}[/cyan] from {storage_root} → {target}"
        )
        with console.status(f"Pulling [cyan]{entry}[/cyan]...", spinner="dots"):
            result = pull_local(
                entry=entry,
                target=target,
                source=storage_root,
                delete=delete,
            )

    if result.success:
        console.print(
            f"[green]✓[/green] {entry}: pulled "
            f"({result.file_count} files, {human_size(result.total_size or 0)})"
        )
    else:
        err_console.print(f"[red]✗[/red] {entry}: {result.error}")
        raise typer.Exit(1)

    if ctx.state is not None:
        ctx.state.close()


@app.command()
def gc(
    keep_revisions: Annotated[
        int,
        typer.Option("--keep-revisions", "-k", help="Number of revisions to keep per entry"),
    ] = 2,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would be removed without deleting"),
    ] = False,
) -> None:
    """Remove old revisions, keeping current and newest N per entry."""
    cfg, state = _require_config()

    mode = "[yellow]DRY RUN[/yellow] " if dry_run else ""
    console.print(f"{mode}Running garbage collection (keep={keep_revisions})...")

    result = gc_all(cfg, keep_revisions=keep_revisions, dry_run=dry_run)

    for name, entry_result in result.entries.items():
        if entry_result.removed:
            freed = human_size(entry_result.freed_bytes)
            verb = "would remove" if dry_run else "removed"
            console.print(
                f"  [cyan]{name}[/cyan]: {verb} {len(entry_result.removed)} revision(s) "
                f"({freed}), kept {len(entry_result.kept)}"
            )
            for rev in entry_result.removed:
                console.print(f"    [dim]- {rev}[/dim]")
        else:
            console.print(f"  [cyan]{name}[/cyan]: nothing to remove")

    console.print()
    total_freed = human_size(result.total_freed_bytes)
    verb = "Would free" if dry_run else "Freed"
    console.print(
        f"{verb} {total_freed} across {result.total_removed} revision(s)."
    )

    state.close()


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option("--host", "-H", help="Bind address"),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Bind port"),
    ] = 8080,
) -> None:
    """Start the HTTP API server."""
    import uvicorn

    from hf_serve.server import app_state as server_state
    from hf_serve.server import create_app_from_state

    cfg, state = _require_config()

    # Pre-populate server state from CLI context (config already loaded)
    server_state.config = cfg
    server_state.state = state

    server_app = create_app_from_state()

    console.print(
        f"Starting hf-serve server on [cyan]http://{host}:{port}[/cyan]"
    )

    uvicorn.run(server_app, host=host, port=port, log_level="info")
