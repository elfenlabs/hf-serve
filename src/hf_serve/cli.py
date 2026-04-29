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

    config: AppConfig
    state: StateStore


ctx = _Ctx()


@app.callback()
def main(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Path to config.yaml")
    ],
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable verbose output")
    ] = False,
) -> None:
    """Lightweight self-hosted Hugging Face model directory."""
    setup_logging(verbose)
    try:
        ctx.config = load_config(config)
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)
    ctx.state = StateStore(ctx.config.storage.root / "state.db")


@app.command(name="list")
def list_() -> None:
    """List all configured entries and their sync status."""
    cfg, state = ctx.config, ctx.state

    table = Table(title="hf-serve entries")
    table.add_column("ENTRY", style="cyan", no_wrap=True)
    table.add_column("REPOSITORY", style="white")
    table.add_column("REVISION", style="dim")
    table.add_column("STATUS", no_wrap=True)
    table.add_column("SIZE", justify="right")

    status_map = {row.entry: row for row in state.list_statuses()}

    for name, entry in cfg.entries.items():
        row = status_map.get(name)
        status = row.status if row else EntryStatus.UNKNOWN

        status_style = {
            EntryStatus.READY: "[green]ready[/]",
            EntryStatus.SYNCING: "[yellow]syncing[/]",
            EntryStatus.FAILED: "[red]failed[/]",
            EntryStatus.UNKNOWN: "[dim]unknown[/]",
        }.get(status, str(status.value))

        size = human_size(row.total_size) if row and row.total_size else "-"
        revision = cfg.resolve_revision(entry)

        table.add_row(name, entry.repository, revision, status_style, size)

    console.print(table)
    state.close()


@app.command()
def status(
    entry: Annotated[Optional[str], typer.Argument(help="Entry name")] = None,
) -> None:
    """Show detailed status for an entry, or list all if no entry given."""
    cfg, state = ctx.config, ctx.state

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
    cfg, state = ctx.config, ctx.state

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
    cfg, state = ctx.config, ctx.state

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
    cfg, state = ctx.config, ctx.state

    if entry not in cfg.entries:
        err_console.print(f"[bold red]Error:[/] Unknown entry: {entry}")
        raise typer.Exit(1)

    delete = not no_delete

    if server:
        # Rsync mode
        storage_root = source or cfg.storage.root
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
        storage_root = source or cfg.storage.root
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

    state.close()
