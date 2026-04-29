"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator


class StorageConfig(BaseModel):
    """Storage paths configuration."""

    root: Path
    hf_cache: Path | None = None

    @model_validator(mode="after")
    def _default_hf_cache(self) -> "StorageConfig":
        if self.hf_cache is None:
            self.hf_cache = self.root / ".hf-cache"
        return self


class SyncConfig(BaseModel):
    """Sync behaviour configuration."""

    interval_seconds: int = 3600
    default_revision: str = "main"


class EntryConfig(BaseModel):
    """A single model entry."""

    repository: str
    repo_type: str = "model"
    revision: str | None = None
    allow_patterns: list[str] | None = None
    ignore_patterns: list[str] | None = None

    @field_validator("repository")
    @classmethod
    def _repository_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repository must not be empty")
        return v


class HuggingFaceConfig(BaseModel):
    """Hugging Face authentication configuration."""

    token_env: str = "HF_TOKEN"


class AppConfig(BaseModel):
    """Top-level application configuration."""

    storage: StorageConfig
    sync: SyncConfig = SyncConfig()
    huggingface: HuggingFaceConfig = HuggingFaceConfig()
    entries: dict[str, EntryConfig]

    @field_validator("entries")
    @classmethod
    def _entries_not_empty(cls, v: dict[str, EntryConfig]) -> dict[str, EntryConfig]:
        if not v:
            raise ValueError("entries must contain at least one entry")
        return v

    def resolve_revision(self, entry: EntryConfig) -> str:
        """Return the effective revision for an entry."""
        return entry.revision or self.sync.default_revision


def load_config(path: Path) -> AppConfig:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Validated AppConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping, got {type(raw).__name__}")

    return AppConfig(**raw)
