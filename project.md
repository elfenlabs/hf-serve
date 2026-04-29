````markdown
# Project Brief: `hf-serve` — Hugging Face Directory for Homelab Model Management

## Summary

`hf-serve` is a lightweight self-hosted Hugging Face model directory and sync service.

It lets a homelab cluster with large storage act as the canonical local model repository, while smaller inference machines can pull selected models into temporary local directories on demand.

Primary use case:

- Raptor / Kubernetes cluster has large storage, e.g. 24TB.
- DGX Spark machines have limited local storage, e.g. 1TB.
- Models are synced once from Hugging Face into the cluster.
- Spark machines can pull/swap models using a simple CLI.

Example user workflow:

```bash
hf-serve pull qwen3-32b-awq ./model
vllm serve ./model
````

The goal is **not** to implement a full Hugging Face-compatible proxy. The goal is to provide a curated, named, operational model directory.

---

## Goals

Build a small service and CLI that can:

1. Read a YAML config defining named Hugging Face repositories.
2. Sync selected repositories from Hugging Face into a central storage location.
3. Support file filtering per entry.
4. Materialize each entry into a stable local directory layout.
5. Expose a small HTTP API for listing entries and checking sync status.
6. Provide a CLI for pulling a named entry into a local directory.
7. Prefer boring, robust implementation over clever distributed caching.

---

## Non-goals for v1

Do not implement these in v1:

* Full Hugging Face API compatibility.
* Transparent proxying of `huggingface.co`.
* Multi-tenant permissions.
* Web UI.
* P2P transfer.
* Complex deduplication beyond what `huggingface_hub` already provides.
* Automatic model serving.
* Kubernetes operator behavior.
* Dataset support unless trivial to include.

---

## Proposed Name

Project name:

```text
hf-serve
```

Meaning:

```text
Hugging Face Directory
```

CLI binary:

```bash
hf-serve
```

Optional daemon/server binary:

```bash
hf-serve-server
```

---

## Example Configuration

```yaml
storage:
  root: /data/hf-serve
  hf_cache: /data/hf-serve/.hf-cache

server:
  host: 0.0.0.0
  port: 8080

sync:
  interval_seconds: 3600
  default_revision: main

entries:
  qwen3-32b-awq:
    repository: Qwen/Qwen3-32B-AWQ
    repo_type: model
    revision: main
    allow_patterns:
      - "*.json"
      - "*.safetensors"
      - "tokenizer.*"
      - "*.model"
    ignore_patterns:
      - "*.bin"
      - "*.gguf"
      - "*.h5"
      - "*.msgpack"

  llama-3.1-8b-instruct:
    repository: meta-llama/Llama-3.1-8B-Instruct
    repo_type: model
    revision: main
    allow_patterns:
      - "*.json"
      - "*.safetensors"
      - "tokenizer.*"
```

Notes:

* `repository` maps to Hugging Face `repo_id`.
* `repo_type` defaults to `model`.
* `revision` defaults to `main`.
* Use Hugging Face-compatible glob patterns for filtering, not regex.
* Regex filtering can be added later if truly needed.

---

## Storage Layout

Use a stable layout independent of Hugging Face’s internal cache format.

```text
/data/hf-serve/
  config.yaml

  .hf-cache/
    hub/
      ...

  entries/
    qwen3-32b-awq/
      current -> revisions/<commit_hash>
      revisions/
        <commit_hash>/
          config.json
          tokenizer.json
          model-00001-of-00008.safetensors
          model-00002-of-00008.safetensors
          ...
          hf-serve-manifest.json

    llama-3.1-8b-instruct/
      current -> revisions/<commit_hash>
      revisions/
        <commit_hash>/
          ...
          hf-serve-manifest.json
```

Each synced revision should be immutable once materialized.

`current` should be updated atomically.

---

## Manifest Format

Each materialized revision should include:

```json
{
  "entry": "qwen3-32b-awq",
  "repository": "Qwen/Qwen3-32B-AWQ",
  "repo_type": "model",
  "revision": "main",
  "commit_hash": "abc123...",
  "synced_at": "2026-04-29T00:00:00Z",
  "files": [
    {
      "path": "config.json",
      "size": 1234
    },
    {
      "path": "model-00001-of-00008.safetensors",
      "size": 5368709120
    }
  ],
  "total_size": 42949672960
}
```

Optional later fields:

```json
{
  "etag": "...",
  "sha256": "...",
  "source_url": "..."
}
```

For v1, file path and size are enough.

---

## Core Behavior

### Sync One Entry

Command:

```bash
hf-serve sync qwen3-32b-awq
```

Behavior:

1. Read config.
2. Resolve entry.
3. Use `huggingface_hub.snapshot_download()`.
4. Store HF cache under configured `hf_cache`.
5. Detect the resolved commit hash.
6. Copy or hardlink snapshot files into:

```text
entries/<entry>/revisions/<commit_hash>/
```

7. Write `hf-serve-manifest.json`.
8. Atomically update:

```text
entries/<entry>/current
```

9. Report status.

### Sync All Entries

Command:

```bash
hf-serve sync
```

Behavior:

* Iterate all entries.
* Sync each entry.
* Continue on individual entry failure.
* Return non-zero if any entry failed.

### Pull Entry to Local Directory

Command:

```bash
hf-serve pull qwen3-32b-awq ./model
```

Behavior:

1. Ask server for entry metadata.
2. Pull from server storage to target directory.
3. Replace target directory atomically if possible.
4. Use `rsync` by default for efficient local-network transfer.
5. Support `--delete` behavior to remove stale files.
6. Verify manifest exists after pull.

Suggested implementation:

```bash
rsync -a --delete <server>:/data/hf-serve/entries/qwen3-32b-awq/current/ ./model/
```

The CLI can wrap this initially instead of implementing custom transfer.

---

## CLI UX

Primary commands:

```bash
hf-serve list
hf-serve status
hf-serve status qwen3-32b-awq
hf-serve sync
hf-serve sync qwen3-32b-awq
hf-serve pull qwen3-32b-awq ./model
hf-serve manifest qwen3-32b-awq
hf-serve gc
```

Example output:

```text
$ hf-serve list

ENTRY                 REPOSITORY                     REVISION  STATUS   SIZE
qwen3-32b-awq          Qwen/Qwen3-32B-AWQ              main      ready    42.1GB
llama-3.1-8b-instruct  meta-llama/Llama-3.1-8B-Instruct main     ready    16.4GB
```

```text
$ hf-serve status qwen3-32b-awq

Entry:        qwen3-32b-awq
Repository:   Qwen/Qwen3-32B-AWQ
Revision:     main
Commit:       abc123...
Status:       ready
Synced at:    2026-04-29T00:00:00Z
Size:         42.1GB
Files:        18
Path:         /data/hf-serve/entries/qwen3-32b-awq/current
```

---

## HTTP API

Minimal server API:

```http
GET /healthz
GET /v1/entries
GET /v1/entries/{entry}
GET /v1/entries/{entry}/manifest
POST /v1/entries/{entry}/sync
POST /v1/sync
```

Optional later:

```http
GET /metrics
POST /v1/gc
GET /v1/entries/{entry}/archive.tar.zst
```

### `GET /v1/entries`

Response:

```json
{
  "entries": [
    {
      "name": "qwen3-32b-awq",
      "repository": "Qwen/Qwen3-32B-AWQ",
      "revision": "main",
      "status": "ready",
      "commit_hash": "abc123",
      "total_size": 42100000000,
      "synced_at": "2026-04-29T00:00:00Z"
    }
  ]
}
```

### Status Values

Use these status values:

```text
unknown
syncing
ready
failed
```

Persist current status in SQLite or a JSON state file.

SQLite is preferred.

---

## Implementation Recommendation

Use Python for v1.

Suggested dependencies:

```text
fastapi
uvicorn
typer
pydantic
pyyaml
huggingface_hub
rich
```

Optional:

```text
apscheduler
prometheus-client
```

Project structure:

```text
hf-serve/
  pyproject.toml
  README.md

  src/hf_dir/
    __init__.py
    config.py
    models.py
    sync.py
    storage.py
    state.py
    server.py
    cli.py
    rsync.py
    util.py

  tests/
    test_config.py
    test_storage.py
    test_manifest.py
```

---

## Key Implementation Details

### Use Hugging Face as the download engine

Use:

```python
from huggingface_hub import snapshot_download

snapshot_path = snapshot_download(
    repo_id=entry.repository,
    repo_type=entry.repo_type,
    revision=entry.revision,
    allow_patterns=entry.allow_patterns,
    ignore_patterns=entry.ignore_patterns,
    cache_dir=config.storage.hf_cache,
)
```

Do not manually parse Hugging Face URLs in v1.

### Commit Hash Detection

`snapshot_download()` usually returns a path pointing at the resolved snapshot.

The implementation should extract the commit hash from the snapshot path when possible.

Example path shape:

```text
/data/hf-serve/.hf-cache/models--Qwen--Qwen3-32B-AWQ/snapshots/<commit_hash>
```

If commit extraction fails, generate a deterministic revision ID from:

```text
repository + repo_type + revision + current timestamp
```

But prefer real commit hash.

### Atomic Current Update

Never update `current` before the revision is completely materialized.

Use:

```text
current.tmp -> revisions/<commit_hash>
rename current.tmp to current
```

On Linux:

* remove old `current.tmp` if present
* create symlink
* `os.replace()`

### Avoid Half-Written Revisions

Write into:

```text
revisions/<commit_hash>.partial/
```

Then rename to:

```text
revisions/<commit_hash>/
```

Only after manifest creation succeeds.

### Transfer Strategy

For v1, support two modes:

```text
local
rsync
```

`local`:

```bash
hf-serve pull qwen3-32b-awq ./model --source /data/hf-serve
```

`rsync`:

```bash
hf-serve pull qwen3-32b-awq ./model --server raptor
```

Internally:

```bash
rsync -a --delete raptor:/data/hf-serve/entries/qwen3-32b-awq/current/ ./model/
```

Custom HTTP file transfer can come later.

---

## Kubernetes Deployment

Recommended deployment:

```text
Namespace: model-cache or hf-serve
Workload: Deployment
Storage: PVC backed by large HDD storage
Service: ClusterIP
Ingress: optional internal-only ingress
```

Example values needed later:

```yaml
persistence:
  enabled: true
  size: 20Ti
  storageClassName: hdd-large

config:
  existingConfigMap: hf-serve-config

server:
  service:
    type: ClusterIP
    port: 8080
```

For sync behavior, either:

1. Run server with a scheduler loop, or
2. Run a Kubernetes CronJob that calls:

```bash
hf-serve sync
```

For v1, prefer **manual sync command** plus optional CronJob.

---

## Authentication

For v1:

* No auth if internal-only.
* Bind only to cluster-internal network.
* Access over NetBird or internal DNS.

Later:

* API key support.
* OIDC support.
* Per-entry access control.

Hugging Face token support should be included early because gated models may need it.

Config:

```yaml
huggingface:
  token_env: HF_TOKEN
```

The service should read `HF_TOKEN` from environment and pass it implicitly through `huggingface_hub`.

---

## Error Handling

Handle:

* Invalid config.
* Unknown entry name.
* Hugging Face auth failure.
* Gated repo access denied.
* Disk full.
* Partial sync failure.
* Rsync missing.
* Target directory not writable.
* Current symlink missing.
* Manifest missing or corrupt.

Error messages should include:

```text
entry name
repository
revision
operation
underlying exception
```

---

## Garbage Collection

V1 GC command:

```bash
hf-serve gc --keep-revisions 2
```

Behavior:

* For each entry, list revision directories.
* Keep current revision.
* Keep newest N revisions.
* Delete older revisions.
* Never delete `.hf-cache` by default.

Later:

```bash
hf-serve gc --include-hf-cache
hf-serve gc --max-size 10Ti
```

---

## Observability

Add logs:

```text
sync_started
sync_completed
sync_failed
entry_ready
manifest_written
current_updated
pull_started
pull_completed
```

Use structured logs if easy.

Optional Prometheus metrics:

```text
hf_dir_entries_total
hf_dir_entry_size_bytes
hf_dir_sync_success_total
hf_dir_sync_failure_total
hf_dir_last_sync_timestamp_seconds
hf_dir_sync_duration_seconds
```

---

## Testing Plan

Unit tests:

1. Parse config.
2. Validate duplicate entry names.
3. Validate required repository field.
4. Manifest generation.
5. Atomic symlink update.
6. Partial revision cleanup.
7. GC keeps current revision.
8. CLI command parsing.

Integration tests:

1. Sync a tiny public Hugging Face repo.
2. Pull synced entry into temp dir.
3. Verify manifest and files exist.
4. Re-sync same revision is idempotent.
5. Failed sync does not change `current`.

Use a small test repo to avoid huge downloads.

---

## MVP Milestones

### Milestone 1: Local sync

Deliver:

```bash
hf-serve sync <entry>
hf-serve list
hf-serve status <entry>
```

No server yet.

### Milestone 2: Pull command

Deliver:

```bash
hf-serve pull <entry> <target>
```

Support local path and rsync mode.

### Milestone 3: HTTP server

Deliver:

```bash
hf-serve-server --config config.yaml
```

Endpoints:

```text
/healthz
/v1/entries
/v1/entries/{entry}
/v1/entries/{entry}/manifest
```

### Milestone 4: Kubernetes packaging

Deliver:

* Dockerfile
* Helm chart or plain manifests
* PVC mount
* ConfigMap config
* optional CronJob

### Milestone 5: GC and metrics

Deliver:

```bash
hf-serve gc
```

Optional:

```http
GET /metrics
```

---

## Suggested Initial Codex Task

Implement the MVP Python project for `hf-serve`.

Requirements:

1. Create a Python package using `pyproject.toml`.
2. Add a Typer CLI named `hf-serve`.
3. Support config loading from YAML.
4. Implement:

   * `hf-serve list`
   * `hf-serve sync <entry>`
   * `hf-serve status <entry>`
   * `hf-serve manifest <entry>`
5. Use `huggingface_hub.snapshot_download()` for syncing.
6. Store materialized snapshots under:

```text
<storage.root>/entries/<entry>/revisions/<commit_hash>/
```

7. Maintain:

```text
<storage.root>/entries/<entry>/current
```

as an atomic symlink to the active revision.

8. Write `hf-serve-manifest.json` into every materialized revision.
9. Use a JSON or SQLite state store.
10. Include basic tests for config parsing, manifest generation, and storage layout.

Do not implement HTTP server or Kubernetes deployment in the first task unless the MVP is already complete.

---

## Design Principle

`hf-serve` should behave like a small model package manager for a homelab.

It should answer:

```text
What models are available?
Which revision is synced?
How much disk does each model use?
Can this Spark pull model X into ./model right now?
```

It should not try to become:

```text
a Hugging Face clone
a transparent proxy
a model serving framework
a general artifact repository
```

```
```
