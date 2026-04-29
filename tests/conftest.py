"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from hf_serve.config import AppConfig, EntryConfig, StorageConfig


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Return a temporary storage root directory."""
    root = tmp_path / "hf-serve-data"
    root.mkdir()
    return root


@pytest.fixture
def sample_config_dict(tmp_root: Path) -> dict:
    """Return a raw config dictionary suitable for AppConfig."""
    return {
        "storage": {
            "root": str(tmp_root),
        },
        "entries": {
            "tiny-bert": {
                "repository": "hf-internal-testing/tiny-random-BertModel",
            },
            "another-model": {
                "repository": "some-org/some-model",
                "revision": "v1.0",
                "allow_patterns": ["*.json", "*.safetensors"],
                "ignore_patterns": ["*.bin"],
            },
        },
    }


@pytest.fixture
def sample_config(sample_config_dict: dict) -> AppConfig:
    """Return a parsed AppConfig from the sample dict."""
    return AppConfig(**sample_config_dict)
