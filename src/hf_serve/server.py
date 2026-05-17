"""FastAPI HTTP server for hf-serve."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from hf_serve.config import AppConfig, load_config
from hf_serve.gc import gc_all
from hf_serve.metrics import generate_metrics
from hf_serve.models import EntryStatus
from hf_serve.state import StateStore
from hf_serve.storage import get_current_link, read_manifest
from hf_serve.sync import sync_all as do_sync_all
from hf_serve.sync import sync_entry as do_sync_entry

logger = logging.getLogger(__name__)


# ── Response models ──────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"


class EntrySummary(BaseModel):
    name: str
    repository: str
    revision: str
    status: str
    commit_hash: str | None = None
    total_size: int | None = None
    synced_at: datetime | None = None


class EntriesResponse(BaseModel):
    entries: list[EntrySummary]


class EntryDetail(BaseModel):
    name: str
    repository: str
    repo_type: str
    revision: str
    status: str
    commit_hash: str | None = None
    total_size: int | None = None
    synced_at: datetime | None = None
    file_count: int | None = None
    path: str | None = None


class SyncResponse(BaseModel):
    entry: str
    success: bool
    commit_hash: str | None = None
    total_size: int | None = None
    error: str | None = None
    skipped: bool = False


class SyncAllResponse(BaseModel):
    results: list[SyncResponse]
    success_count: int
    failure_count: int


class GCEntryDetail(BaseModel):
    entry: str
    kept: list[str]
    removed: list[str]
    freed_bytes: int


class GCResponse(BaseModel):
    entries: list[GCEntryDetail]
    total_removed: int
    total_freed_bytes: int


# ── App state container ─────────────────────────────────────────────────────


class _AppState:
    """Holds config and state store references, set during lifespan."""

    config: AppConfig
    state: StateStore


app_state = _AppState()


def create_app(config_path: Path) -> FastAPI:
    """Create a FastAPI application with the given config.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Configured FastAPI application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup
        logger.info("Loading config from %s", config_path)
        app_state.config = load_config(config_path)
        app_state.state = StateStore(app_state.config.storage.root / "state.db")
        logger.info("hf-serve server started")
        yield
        # Shutdown
        app_state.state.close()
        logger.info("hf-serve server stopped")

    app = FastAPI(
        title="hf-serve",
        description="Lightweight self-hosted Hugging Face model directory.",
        version="0.1.0",
        lifespan=lifespan,
    )

    _register_routes(app)
    return app


def create_app_from_state() -> FastAPI:
    """Create a FastAPI application using pre-populated app_state.

    Used by the CLI 'serve' command where config is already loaded.
    The lifespan only handles shutdown (closing the state store).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("hf-serve server started (pre-configured)")
        yield
        app_state.state.close()
        logger.info("hf-serve server stopped")

    app = FastAPI(
        title="hf-serve",
        description="Lightweight self-hosted Hugging Face model directory.",
        version="0.1.0",
        lifespan=lifespan,
    )

    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    """Register all API routes on the app."""

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.get("/v1/entries", response_model=EntriesResponse)
    async def list_entries() -> EntriesResponse:
        cfg = app_state.config
        state = app_state.state

        status_map = {row.entry: row for row in state.list_statuses()}
        summaries: list[EntrySummary] = []

        for name, entry in cfg.entries.items():
            row = status_map.get(name)
            summaries.append(
                EntrySummary(
                    name=name,
                    repository=entry.repository,
                    revision=cfg.resolve_revision(entry),
                    status=row.status.value if row else EntryStatus.UNKNOWN.value,
                    commit_hash=row.commit_hash if row else None,
                    total_size=row.total_size if row else None,
                    synced_at=row.synced_at if row else None,
                )
            )

        return EntriesResponse(entries=summaries)

    @app.get("/v1/entries/{entry}", response_model=EntryDetail)
    async def get_entry(entry: str) -> EntryDetail:
        cfg = app_state.config
        state = app_state.state

        if entry not in cfg.entries:
            raise HTTPException(status_code=404, detail=f"Unknown entry: {entry}")

        entry_config = cfg.entries[entry]
        row = state.get_status(entry)
        manifest = read_manifest(cfg.storage.root, entry)
        current_link = get_current_link(cfg.storage.root, entry)

        return EntryDetail(
            name=entry,
            repository=entry_config.repository,
            repo_type=entry_config.repo_type,
            revision=cfg.resolve_revision(entry_config),
            status=row.status.value if row else EntryStatus.UNKNOWN.value,
            commit_hash=row.commit_hash if row else None,
            total_size=row.total_size if row else None,
            synced_at=row.synced_at if row else None,
            file_count=len(manifest.files) if manifest else None,
            path=str(current_link) if current_link.exists() else None,
        )

    @app.get("/v1/entries/{entry}/manifest")
    async def get_manifest(entry: str) -> dict:
        cfg = app_state.config

        if entry not in cfg.entries:
            raise HTTPException(status_code=404, detail=f"Unknown entry: {entry}")

        manifest = read_manifest(cfg.storage.root, entry)
        if manifest is None:
            raise HTTPException(
                status_code=404,
                detail=f"No manifest found for {entry}. Has it been synced?",
            )

        return manifest.model_dump(mode="json")

    @app.post("/v1/entries/{entry}/sync", response_model=SyncResponse)
    async def sync_entry(entry: str) -> SyncResponse:
        cfg = app_state.config
        state = app_state.state

        if entry not in cfg.entries:
            raise HTTPException(status_code=404, detail=f"Unknown entry: {entry}")

        result = do_sync_entry(cfg, entry, state)

        return SyncResponse(
            entry=result.entry,
            success=result.success,
            commit_hash=result.commit_hash,
            total_size=result.total_size,
            error=result.error,
            skipped=result.skipped,
        )

    @app.post("/v1/sync", response_model=SyncAllResponse)
    async def sync_all() -> SyncAllResponse:
        cfg = app_state.config
        state = app_state.state

        results = do_sync_all(cfg, state)

        return SyncAllResponse(
            results=[
                SyncResponse(
                    entry=r.entry,
                    success=r.success,
                    commit_hash=r.commit_hash,
                    total_size=r.total_size,
                    error=r.error,
                    skipped=r.skipped,
                )
                for r in results.results.values()
            ],
            success_count=results.success_count,
            failure_count=results.failure_count,
        )

    @app.post("/v1/gc", response_model=GCResponse)
    async def run_gc(
        keep_revisions: int = 2,
        dry_run: bool = False,
    ) -> GCResponse:
        cfg = app_state.config

        result = gc_all(cfg, keep_revisions=keep_revisions, dry_run=dry_run)

        return GCResponse(
            entries=[
                GCEntryDetail(
                    entry=er.entry,
                    kept=er.kept,
                    removed=er.removed,
                    freed_bytes=er.freed_bytes,
                )
                for er in result.entries.values()
            ],
            total_removed=result.total_removed,
            total_freed_bytes=result.total_freed_bytes,
        )

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        return generate_metrics(app_state.config, app_state.state)
