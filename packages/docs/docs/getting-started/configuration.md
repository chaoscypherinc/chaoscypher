---
id: configuration
title: Configuration
description: Configure Chaos Cypher via settings.yaml and environment variables — LLM providers, embedding, auth, queue, and strict YAML validation with typo suggestions.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Configuration

Chaos Cypher is configured through a YAML settings file and environment variables. Most settings can also be changed from the web UI **Settings** page at runtime.

## Strict configuration validation

Unknown top-level keys in `settings.yaml` raise `ConfigError` at startup with a Levenshtein-based suggestion. For example, a typo like `embedding_settings:` instead of `embedding:` produces:

```
ConfigError: Unrecognized top-level setting(s) in /data/settings.yaml:
  - embedding_settings (did you mean 'embedding'?)
```

This prevents misconfigured deployments from silently falling back to defaults.

## Settings File

The primary configuration file is `settings.yaml`, located in the data directory. The location depends on how you run Chaos Cypher:

- **Docker (all-in-one):** `/data/settings.yaml` (inside the container, persisted via volume mount)
- **Docker (multi-container):** `packages/docker/data/settings.yaml` (created at first startup, gitignored)
- **Local / CLI:** Platform-specific data directory (e.g., `~/.local/share/chaoscypher/settings.yaml` on Linux, `%LOCALAPPDATA%\chaoscypher\settings.yaml` on Windows)

The file is auto-generated with sensible defaults on first startup. You can also configure settings from the web UI **Settings** page.

Settings follow a nested structure matching the settings groups below. Any setting not specified uses its default value.

```yaml
# Example settings.yaml
current_database: default
dark_mode: true

llm:
  chat_provider: ollama
  ollama_chat_model: qwen3:30b-instruct

embedding:
  provider: local
  model: Qwen/Qwen3-Embedding-0.6B

search:
  enable_vector_search: true
  min_similarity_threshold: 0.55

chunking:
  small_chunk_size: 900
  small_chunk_overlap: 150
```

## LLM Configuration

Controls which LLM provider is used for chat and extraction.

### Provider Selection

```yaml
llm:
  chat_provider: ollama       # ollama | openai | anthropic | gemini
```

### Ollama (Default)

Ollama is configured exclusively through the `ollama_instances` list. The
backend always seeds a single default instance pointed at the Docker host,
so a minimal config only needs the model name:

```yaml
llm:
  chat_provider: ollama
  ollama_chat_model: qwen3:30b-instruct
  ollama_extraction_model: null  # Uses chat model if null
```

To override the default URL (e.g. talking to an Ollama on another host),
edit the seeded instance:

```yaml
llm:
  chat_provider: ollama
  ollama_chat_model: qwen3:30b-instruct
  ollama_instances:
    - id: default
      name: Default
      base_url: http://my-ollama-host:11434
```

For multi-GPU setups, add additional instances and the load balancer will
distribute requests across them:

```yaml
llm:
  ollama_instances:
    - id: gpu-1
      name: Primary GPU
      base_url: http://192.168.1.10:11434
    - id: gpu-2
      name: Secondary GPU
      base_url: http://192.168.1.11:11434
  ollama_load_balancing: round_robin   # round_robin | least_loaded | random
```

Ollama URLs are configured via `ollama_instances`. Each instance is a separate
backend; the load balancer selects one per request.

### OpenAI

```yaml
llm:
  chat_provider: openai
  openai_api_key: sk-...
  openai_chat_model: gpt-4.1
```

### Anthropic

```yaml
llm:
  chat_provider: anthropic
  anthropic_api_key: sk-ant-...
  anthropic_chat_model: claude-sonnet-4-5
```

### Gemini

```yaml
llm:
  chat_provider: gemini
  gemini_api_key: ...
  gemini_chat_model: gemini-2.5-pro
```

### LLM Behavior

```yaml
llm:
  ai_temperature: 0.3           # Chat temperature (0.0-1.0)
  extraction_temperature: 0.1   # Extraction temperature (lower = more deterministic)
  ai_max_tokens: 65536          # Max output tokens
  ai_context_window: 8192       # Context window size
```

## Chunking

Controls how documents are split into chunks for indexing and extraction.

Three knobs cover most tuning needs:

```yaml
chunking:
  small_chunk_size: 900         # Target chunk size in characters (~225 tokens)
  small_chunk_overlap: 150      # Overlap between consecutive chunks (~16%)
  group_size: 4                 # Chunks per extraction group sent to the LLM
```

| Setting | Default | Description |
|---------|---------|-------------|
| `small_chunk_size` | `900` | Target size of each chunk in characters. Larger chunks give the LLM more context per call; smaller chunks improve RAG retrieval precision. |
| `small_chunk_overlap` | `150` | Characters of overlap between consecutive chunks (~16%). Prevents entities from being split across chunk boundaries. |
| `group_size` | `4` | How many small chunks are grouped together for a single LLM extraction call. Higher = more context per call (better relationship discovery); lower = faster and cheaper. |

<details>
<summary>Advanced chunking knobs</summary>

```yaml
chunking:
  min_chunk_size: 100           # Don't create chunks smaller than this
  max_chunk_size: 1100          # Hard upper limit per chunk
  respect_boundaries: true      # Break at sentence/paragraph boundaries
  group_overlap: 1              # Overlap between consecutive groups (sliding window)
```

| Setting | Default | Description |
|---------|---------|-------------|
| `min_chunk_size` | `100` | Minimum chunk size. Chunks smaller than this are merged with the next chunk. Prevents tiny trailing chunks that add noise. Set to `0` to disable coalescing. |
| `max_chunk_size` | `1100` | Hard upper limit. Chunks are split before this size even if it would break a sentence. |
| `respect_boundaries` | `true` | When True, the chunker tries to break at sentence or paragraph boundaries rather than mid-word. Recommended. |
| `group_overlap` | `1` | Groups use a sliding window with this many chunks of overlap. 0 = non-overlapping groups; 1 (default) = each group shares one chunk with the previous. |

</details>

## Embeddings

By default, embeddings are generated locally on the CPU using [sentence-transformers](https://www.sbert.net/), requiring no API keys or network access. Alternative providers (Ollama, OpenAI, Gemini) can be configured for cloud-based embedding.

```yaml
embedding:
  provider: local              # local | ollama | openai | gemini
  model: Qwen/Qwen3-Embedding-0.6B  # HuggingFace model ID (local) or provider model name
  api_key: null                # For cloud providers
  api_base: null               # Custom endpoint override
  ollama_instance_id: default  # Ollama instance for embedding
  max_text_length: 16000       # Max characters before truncation
```

| Setting | Default | Description |
|---------|---------|-------------|
| `provider` | `local` | Embedding provider: `local`, `ollama`, `openai`, or `gemini` |
| `model` | `Qwen/Qwen3-Embedding-0.6B` | HuggingFace model ID (local) or provider model name |
| `api_key` | `null` | API key for cloud providers |
| `api_base` | `null` | Custom endpoint override |
| `ollama_instance_id` | `default` | Ollama instance to use for embedding |
| `max_text_length` | `16000` | Max characters before truncation |

The model downloads automatically on first real use (after setup) and is cached at `data/models/embeddings/`. Subsequent runs load from cache. A fresh install makes no HuggingFace calls until then; set `embedding.allow_model_download: true` to enable eager warmup at startup instead.

## Search

```yaml
search:
  enable_vector_search: true          # Enable semantic search
  vector_dimensions: 1024             # Embedding dimensions
  min_similarity_threshold: 0.55      # Minimum similarity for results
  max_search_results: 100             # Maximum results returned
  enable_rerank: true                 # Re-rank results for relevance
  rerank_model_name: Alibaba-NLP/gte-reranker-modernbert-base
```

## Source Processing

```yaml
source_processing:
  auto_extract_entities: true                    # Auto-start extraction after indexing
  source_processing_analysis_depth: full         # full | quick
  entity_deduplication_mode: semantic            # exact | semantic
  entity_deduplication_similarity_threshold: 0.90
  relationship_confidence_threshold: 0.5
  # Per-source extraction quality overrides (max_relationship_ratio, etc.)
```

### Filtering-mode knobs

The [filtering mode](../reference/filtering-modes.md) you pick at
upload time selects a preset bundle. The three knobs that actually
move with the slider are wired through to the extraction pipeline:

| Knob | Range | Effect |
|------|-------|--------|
| `loop_max_entity_count` | 25–200 | Aborts a chunk whose LLM stream emits more entity lines than the cap. Catches degenerate loops earlier in stricter modes. |
| `semantic_dedup_threshold` | 0.85–0.99 | Cosine-similarity bar for merging two entities semantically. Lower = more aggressive merging. |
| `minimum_alias_length` | 1–3 | Drops short aliases (`AI`, `ML`) in stricter modes to keep the alias index focused on full names. |

These were defined on the settings model for some time but were silently
ignored pre-W4. As of May 2026 every preset's value reaches the
extraction pipeline, so changing the filtering mode produces
distinguishable extraction results.

You'll usually pick a filtering mode rather than override these knobs
individually — see the [Filtering Modes](../reference/filtering-modes.md) reference for the full
preset matrix.

:::note[Deprecated: `source_processing_max_file_size_gb`]

`source_processing.source_processing_max_file_size_gb` was the legacy file-upload cap. As of 2026-05-06 it is **deprecated and no longer honored** — the upload pipeline (file uploads, URL fetches, MCP) now reads `batching.max_upload_bytes` exclusively (see below). Remove the deprecated key from your `settings.yaml` to silence the startup warning; the cap is now uniform across entry paths.

:::

## Upload Limits

Upload routes (`POST /sources`, `POST /sources/batch`, `POST /lexicon/upload`, `POST /exports/import`) are exempted from the per-route body cap and gated by `max_upload_bytes` only. Everything else is gated by `max_request_body_mb`.

- `batching.max_upload_bytes` (default **5 GB**) — application-layer cap enforced during streaming for file uploads (`POST /sources`, `POST /sources/batch`), URL fetches (`POST /sources/url`), package uploads (`POST /lexicon/upload`), and CCX imports (`POST /exports/import`). This is the single source of truth for "how big can one upload be."
- `batching.max_request_body_mb` (default **128 MB**) — outer HTTP body limit for **non-upload** routes. The body-size middleware skips this check on the four upload paths above.
- nginx `client_max_body_size` — rendered from `max_upload_bytes` (with a tight `1m` default at the server level and `{{ max_upload_bytes }}` on the upload locations).

To raise the upload size, increase **`max_upload_bytes`** (the request is rejected by whichever layer has the lower limit):

**1. Application layer** (`settings.yaml`):

```yaml
batching:
  max_upload_bytes: 5368709120  # 5 GB (in bytes) — unified file + URL cap
  max_request_body_mb: 128      # 128 MB (in MB) — outer body limit for non-upload routes
  max_upload_files: 20          # Max files per batch upload
```

**2. Nginx layer** (Docker only — `nginx-http.conf` and `nginx-https.conf`):

```nginx
client_max_body_size 10g;
```

:::warning[Both layers must match]

The request is rejected by whichever layer has the lower limit. If you increase the application limit but not Nginx, uploads will still fail at the Nginx layer.

:::

## Queue

Valkey connection for the background job queue.

```yaml
queue:
  queue_host: valkey
  queue_port: 6379
  queue_database: 0
```

## Logging

### Runtime Log Level

The log level can be changed at runtime from the web UI or API — no restart required. The change propagates to all processes (Cortex and Neuron) via Valkey pub/sub.

<Tabs>
<TabItem value="web-ui" label="Web UI">


Open **Settings** > **General** and select a log level from the dropdown.

</TabItem>
<TabItem value="api" label="API">


```bash
# Get current level
curl http://localhost/api/v1/settings/logging/level

# Change level
curl -X POST http://localhost/api/v1/settings/logging/level \
  -H "Content-Type: application/json" \
  -d '{"level": "DEBUG"}'
```

</TabItem>
</Tabs>


Available levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

### Container Logs

In the all-in-one container, the **Logs** tab (Settings page) shows real-time merged logs from all services — Cortex, Neuron, Nginx, and Valkey — with color-coded rendering and service labels.

### Diagnostics Export

Export a diagnostic bundle for troubleshooting:

<Tabs>
<TabItem value="web-ui" label="Web UI">


Open **Settings** and click **Export Diagnostics**.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher diagnostics
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl http://localhost/api/v1/diagnostics/export -o diagnostics.zip
```

</TabItem>
</Tabs>


The ZIP file includes system info, database statistics, sanitized settings (secrets masked), log files, queue stats, and service status.

## Environment Variables

These environment variables are used by the Docker services:

**Application:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PYTHONUNBUFFERED` | `1` | Ensure proper log output |
| `PYTHONPATH` | `/app` | Module resolution |
| `QUEUE_HOST` | `valkey` | Valkey hostname |
| `QUEUE_PORT` | `6379` | Valkey port |
| `QUEUE_DB` | `0` | Valkey database number |
| `QUEUE_PASSWORD` | Auto-generated | Valkey password (auto-generated on first container start, stored in `/data/secrets/queue_password`) |
| `VITE_API_URL` | `http://cortex:8080` | Frontend API target |
| `USE_JSON_LOGGING` | `false` | JSON logs for production |
| `LOG_LEVEL` | `INFO` | Logging level |

**Infrastructure (all-in-one container only):**

| Variable | Default | Description |
|----------|---------|-------------|
| `NGINX_LOGLEVEL` | `warn` | Nginx error log level |
| `NGINX_ACCESS_LOG` | `off` | Nginx access log on/off |
| `VALKEY_LOGLEVEL` | `warning` | Valkey log level |
| `SUPERVISOR_LOGLEVEL` | `warn` | Supervisord log level |

### Rate Limiting

Rate limiting is **enabled by default**. To disable it (e.g., for local/single-user development), set `enabled: false` under the `rate_limit` key in your `settings.yaml`:

```yaml
rate_limit:
  enabled: false
```

When disabled, the orchestration renderer omits all `limit_req_zone` and `limit_req` directives from the rendered nginx config. A container restart is required after changing this setting.

When enabled, the following rate limits apply:

| Zone | Path | Rate | Burst |
|------|------|------|-------|
| Auth | `/api/v1/auth/` | 5 r/s | 3 |
| Uploads | `/api/v1/sources` | 10 r/s | 5 |
| General API | `/` (catch-all) | 100 r/s | 50 |
| Static assets | `/assets/` | No limit | — |

Rate limits are per client IP. These zones are also tunable under `rate_limit` in `settings.yaml` (e.g. `login_max_requests`, `api_general_max_requests`). Keep rate limiting on if you expose Chaos Cypher to the internet or untrusted networks.

## Worker Configuration

Workers can be configured separately via `workers.yaml` (optional, requires restart):

```yaml
llm_worker:
  max_concurrent: 1       # Concurrent LLM jobs
  max_tries: 5            # Max attempts for LLM jobs
  timeout: 600            # Job timeout in seconds

operations_worker:
  max_concurrent: 8       # Concurrent operation jobs
  max_tries: 5            # Max attempts for operations
  timeout: 7200           # Job timeout in seconds
```

## MCP Server

Settings for the built-in [Model Context Protocol](https://modelcontextprotocol.io/) server that enables AI assistant integration.

```yaml
MCP:
  mode: read              # "read" or "write"
  auto_extract: false     # Run entity extraction after document upload
```

| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | `read` | Tool access level. `read` exposes 19 read tools (search/query). `write` exposes all 31 tools — adding 12 write-only tools for create, update, delete, and document upload. |
| `auto_extract` | `false` | Automatically run entity extraction after indexing documents uploaded via MCP. |

See [MCP Server](../user-guide/mcp.md) for setup and usage details.

## HTTPS / TLS

The all-in-one container auto-detects TLS certificates and switches to HTTPS:

1. Place your certificate and key at `/data/secrets/tls/server.crt` and `/data/secrets/tls/server.key`
2. Restart the container
3. Nginx automatically enables HTTPS with HTTP→HTTPS redirect

The container checks for certificates on every startup — no configuration flag needed. Remove the cert files to revert to HTTP.

## All Settings Groups

For a complete reference of all available settings and their defaults, see the settings class definitions in `packages/core/src/chaoscypher_core/settings.py` (LLM, embedding, chunking, search, source processing) and `packages/core/src/chaoscypher_core/app_config/__init__.py` (top-level settings, queue, rate limiting, auth).

| Group | Key Settings |
|-------|-------------|
| **llm** | Provider selection, model names, API keys, temperature, token limits |
| **mcp** | MCP server mode and document processing behavior |
| **queue** | Valkey connection details |
| **chunking** | Chunk sizes, overlap, boundary handling |
| **search** | Vector search, re-ranking, similarity thresholds |
| **embedding** | Embedding provider, model, dimensions, max text length |
| **source_processing** | Extraction behavior, deduplication, quality controls |
| **export** | Package metadata for CCX exports |
| **lexicon** | Lexicon Hub connection settings |
| **paths** | Data directory structure |
| **timeouts** | API, worker, and health check timeouts |
| **ports** | Service ports |
| **batching** | Upload limits, embedding batches, processing batches |
| **pagination** | Page sizes and limits |
| **cors** | Cross-origin request settings |
| **auth** | Authentication (enabled by default) |
| **rate_limit** | Auth endpoint rate limits, nginx zone limits, and the master `enabled` toggle |
| **retries** | Retry counts for various operations |
| **backoff** | Exponential backoff configuration |
| **analysis** | Graph analysis settings |
| **chat_context** | Chat context window and history limits |
| **services** | External service URLs |
| **workers** | Worker concurrency and timeout defaults |

:::warning Security defaults

By default, Cortex binds to `0.0.0.0`. Read the [self-hosted threat model](../security/self-hosted-threat-model.md) before exposing the service beyond loopback.

:::

## See also

- [API reference: Settings](../reference/api/settings.md) — read and update configuration at runtime via the REST API; VRAM presets, Ollama model management, and reset operations
