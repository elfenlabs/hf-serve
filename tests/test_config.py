"""Tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hf_serve.config import AppConfig, EntryConfig, StorageConfig, load_config


class TestAppConfig:
    def test_parse_valid_config(self, sample_config_dict: dict) -> None:
        config = AppConfig(**sample_config_dict)
        assert "tiny-bert" in config.entries
        assert config.entries["tiny-bert"].repository == "hf-internal-testing/tiny-random-BertModel"

    def test_hf_cache_defaults_to_root(self, sample_config_dict: dict) -> None:
        config = AppConfig(**sample_config_dict)
        expected = Path(sample_config_dict["storage"]["root"]) / ".hf-cache"
        assert config.storage.hf_cache == expected

    def test_hf_cache_explicit(self, tmp_root: Path) -> None:
        config = AppConfig(
            storage={"root": str(tmp_root), "hf_cache": "/custom/cache"},
            entries={"m": {"repository": "org/model"}},
        )
        assert config.storage.hf_cache == Path("/custom/cache")

    def test_default_revision(self, sample_config: AppConfig) -> None:
        assert sample_config.sync.default_revision == "main"

    def test_resolve_revision_default(self, sample_config: AppConfig) -> None:
        entry = sample_config.entries["tiny-bert"]
        assert sample_config.resolve_revision(entry) == "main"

    def test_resolve_revision_explicit(self, sample_config: AppConfig) -> None:
        entry = sample_config.entries["another-model"]
        assert sample_config.resolve_revision(entry) == "v1.0"

    def test_reject_empty_entries(self, tmp_root: Path) -> None:
        with pytest.raises(ValueError, match="entries must contain at least one entry"):
            AppConfig(
                storage={"root": str(tmp_root)},
                entries={},
            )

    def test_reject_empty_repository(self, tmp_root: Path) -> None:
        with pytest.raises(ValueError, match="repository must not be empty"):
            AppConfig(
                storage={"root": str(tmp_root)},
                entries={"bad": {"repository": "  "}},
            )

    def test_default_repo_type(self, sample_config: AppConfig) -> None:
        assert sample_config.entries["tiny-bert"].repo_type == "model"

    def test_allow_ignore_patterns(self, sample_config: AppConfig) -> None:
        entry = sample_config.entries["another-model"]
        assert entry.allow_patterns == ["*.json", "*.safetensors"]
        assert entry.ignore_patterns == ["*.bin"]

    def test_huggingface_default_token_env(self, sample_config: AppConfig) -> None:
        assert sample_config.huggingface.token_env == "HF_TOKEN"


class TestLoadConfig:
    def test_load_valid_yaml(self, tmp_path: Path, sample_config_dict: dict) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(sample_config_dict))

        config = load_config(config_file)
        assert "tiny-bert" in config.entries

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_content(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("just a string")

        with pytest.raises(ValueError, match="YAML mapping"):
            load_config(config_file)
