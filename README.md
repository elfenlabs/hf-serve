# hf-serve

Lightweight self-hosted Hugging Face model directory and sync service for homelab clusters.

## Overview

`hf-serve` lets a homelab cluster with large storage act as the canonical local model repository. Smaller inference machines can pull selected models on demand.

- **Sync** models from Hugging Face into a central, organized storage layout
- **List** available models and their sync status
- **Pull** models to local directories for serving (coming soon)

## Quick Start

```bash
# Install
uv sync

# Create a config file
cat > config.yaml << 'EOF'
storage:
  root: /data/hf-serve

entries:
  tiny-bert:
    repository: hf-internal-testing/tiny-random-BertModel
EOF

# Sync a model
hf-serve --config config.yaml sync tiny-bert

# List all entries
hf-serve --config config.yaml list

# Check status
hf-serve --config config.yaml status tiny-bert

# View manifest
hf-serve --config config.yaml manifest tiny-bert
```

## Configuration

```yaml
storage:
  root: /data/hf-serve           # Root storage directory
  hf_cache: /data/hf-serve/.hf-cache  # HF cache (defaults to root/.hf-cache)

sync:
  interval_seconds: 3600         # (future use)
  default_revision: main

huggingface:
  token_env: HF_TOKEN            # Env var name for HF token

entries:
  qwen3-32b-awq:
    repository: Qwen/Qwen3-32B-AWQ
    repo_type: model
    revision: main
    allow_patterns:
      - "*.json"
      - "*.safetensors"
      - "tokenizer.*"
    ignore_patterns:
      - "*.bin"
      - "*.gguf"
```

## License

MIT
