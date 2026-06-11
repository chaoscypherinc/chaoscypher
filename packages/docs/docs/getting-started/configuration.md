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
- **Docker (multi-container):** `/data/settings.yaml` inside the `app-data` named volume (same path as all-in-one) — edit via `docker exec -it <cortex-container> vi /data/settings.yaml`, or locate the volume on the host with `docker volume inspect`
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

Ollama is configured exclusively through the `ollama_instances` list. On
first boot the backend seeds a single default instance pointed at
`http://localhost:11434`; the `CHAOSCYPHER_OLLAMA_URL` environment variable
overrides the seeded URL, and the Docker deployments set it to
`http://host.docker.internal:11434` so the container reaches an Ollama
running on the host. The seed happens on first boot only — an existing
`settings.yaml` is never rewritten. A minimal config only needs the model
name:

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
  ai_temperature: 0.3           # Chat temperature (0.0-2.0)
  extraction_temperature: 0.1   # Extraction temperature (lower = more deterministic)
  ai_max_tokens: 65536          # Max output tokens
  ai_context_window: 8192       # Context window size
```

### Spend Caps

Two opt-in token caps protect cloud-provider operators against runaway extraction spend. Both default to `null` (disabled):

```yaml
llm:
  max_tokens_per_source: null   # Max total tokens (input + output) one source may consume during extraction
  max_tokens_per_day: null      # Max total tokens per UTC day, per database
```

Once a cap is reached, the next LLM call fails permanently with `LLMSpendCapExceededError` — the source is marked failed instead of continuing to bill. The per-source counter is tracked in memory per worker; the daily counter is persisted in the database (`llm_daily_spend` table), so worker restarts cannot reset it. The daily window rolls over at UTC midnight.

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

## Chat

Tool-calling limits and the tool-approval gate for chat, under the `chat` settings group. These apply to every chat surface — web UI, CLI, and the REST API — via the shared chat tool loop.

```yaml
chat:
  max_tool_iterations: 10             # Rounds of tool calling before forcing a final answer
  max_total_tool_calls: 25            # Total tool calls across all iterations
  max_tools: 14                       # Tools offered to the model per chat
  tool_approval: never-ask            # always-ask | ask-on-write | never-ask
  tool_approval_timeout_seconds: 120  # Unanswered approval requests are denied after this
```

| Setting | Default | Description |
|---------|---------|-------------|
| `max_tool_iterations` | `10` | Maximum rounds of tool calling before the model is forced to produce a final response. |
| `max_total_tool_calls` | `25` | Maximum total tool calls across all iterations of one message. |
| `max_tools` | `14` | Maximum tools included per chat (prevents context overflow). |
| `tool_approval` | `never-ask` | `always-ask` requires confirmation for every tool call; `ask-on-write` only for mutating tools (the `chat.mutating_tools` list — create/update/delete node, edge, and template operations, add/remove document, extraction finalization); `never-ask` runs tools automatically. |
| `tool_approval_timeout_seconds` | `120` | How long a pending tool call waits for an approval decision before being denied (fail-closed). Applies to `always-ask` and `ask-on-write`. |

In the CLI, approval surfaces as a `y/N` prompt — pressing Enter (or EOF) denies, fail-closed. While a tool call waits for a decision, the loop polls every `intervals.chat_approval_poll_ms` milliseconds (default `500`, minimum `50`) — one of several polling knobs in the `intervals` settings group. See [Chat — Tool Approval](../user-guide/chat.md#tool-approval) for the workflow.

## Source Processing

```yaml
source_processing:
  auto_extract_entities: true                    # Auto-start extraction after indexing
  source_processing_analysis_depth: full         # full | quick
  entity_deduplication_mode: semantic            # exact | semantic
  entity_deduplication_similarity_threshold: 0.90
  relationship_confidence_threshold: 0.5
```

:::note[Deprecated: `source_processing_max_file_size_gb`]

`source_processing.source_processing_max_file_size_gb` was the legacy file-upload cap. As of 2026-05-06 it is **deprecated and no longer honored** — the upload pipeline (file uploads, URL fetches, MCP) now reads `batching.max_upload_bytes` exclusively (see below). Remove the deprecated key from your `settings.yaml` to silence the startup warning; the cap is now uniform across entry paths.

:::

## Extraction

Extraction quality knobs — loop detection, semantic dedup thresholds, and relationship caps such as `max_relationship_ratio` — live in the separate `extraction` settings group (not under `source_processing`; writing them there fails strict validation):

```yaml
extraction:
  loop_max_entity_count: 50        # Abort a chunk stream past this many entities
  semantic_dedup_threshold: 0.95   # Cosine-similarity bar for merging entities
  minimum_alias_length: 2          # Drop aliases shorter than this
```

### Filtering-mode knobs

The [filtering mode](../reference/filtering-modes.md) you pick at
upload time selects a preset bundle. The three `extraction` knobs that
actually move with the slider are wired through to the extraction pipeline:

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

## Upload Limits

Upload routes (`POST /sources`, `POST /sources/batch`, `POST /lexicon/upload`, `POST /exports/import`) are exempted from the per-route body cap and gated by `max_upload_bytes` only. Everything else is gated by `max_request_body_mb`.

- `batching.max_upload_bytes` (default **5 GB**) — application-layer cap enforced during streaming for file uploads (`POST /sources`, `POST /sources/batch`), URL fetches (`POST /sources/url`), package uploads (`POST /lexicon/upload`), and CCX imports (`POST /exports/import`). This is the single source of truth for "how big can one upload be."
- `batching.max_request_body_mb` (default **128 MB**) — outer HTTP body limit for **non-upload** routes. The body-size middleware skips this check on the four upload paths above.
- nginx `client_max_body_size` — rendered from `max_upload_bytes` (with a tight `1m` default at the server level and `{{ max_upload_bytes }}` on the upload locations).

To raise the upload size, increase **`max_upload_bytes`** in `settings.yaml`:

```yaml
batching:
  max_upload_bytes: 5368709120  # 5 GB (in bytes) — unified file + URL cap
  max_request_body_mb: 128      # 128 MB (in MB) — outer body limit for non-upload routes
  max_upload_files: 20          # Max files per batch upload
```

The nginx `client_max_body_size` limits are rendered automatically from `batching.max_upload_bytes` at container start — restart the container after changing the setting. Manual edits to `nginx-http.conf`/`nginx-https.conf` are overwritten on the next start, so the setting is the single source of truth for both layers.

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

**Deployment (compose):**

These knobs are read by `packages/docker/docker-compose.yml` when starting the all-in-one container. Set them in a `.env` file next to the compose file (see `packages/docker/.env.example`) or in your shell:

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAOSCYPHER_BIND` | `0.0.0.0` | Host interface the published ports bind to. Set `127.0.0.1` for loopback-only access. |
| `CHAOSCYPHER_ALLOWED_HOSTS` | (empty) | Comma-separated Host-header allow-list for LAN/proxy deployments. Needed when clients reach the app via a LAN hostname/IP so they aren't rejected with `421`. |
| `HOST_PORT_HTTP` | `80` | Host port mapped to the container's HTTP port. |
| `HOST_PORT_HTTPS` | `443` | Host port mapped to the container's HTTPS port. |
| `CHAOSCYPHER_DATA_DIR` | `/data` | Data directory inside the container. Fixed to `/data` by the compose file; must match the volume mount path. |
| `MEM_LIMIT` | `4g` | Container memory limit. Raise to `8g`–`16g` for heavy ingest workloads. |
| `CPU_LIMIT` | `4` | Container CPU limit. |
| `CHAOSCYPHER_STOP_GRACE_PERIOD` | `60s` | How long Docker waits on shutdown before force-killing the container. Must cover the in-app shutdown grace periods (`shutdown.*` in `settings.yaml`). |

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
| Mutations | POST/PUT/PATCH/DELETE on `/api/*` | 10 r/s | 20 (nodelay) |
| General API | `/` (catch-all) | 100 r/s | 50 |
| Static assets | `/assets/` | No limit | — |

Rate limits are per client IP. These zones are also tunable under `rate_limit` in `settings.yaml` (e.g. `login_max_requests`, `api_general_max_requests`, `mutations_max_requests`, `mutations_burst`). Keep rate limiting on if you expose Chaos Cypher to the internet or untrusted networks.

## Worker Configuration

Workers can be configured separately via `workers.yaml` (optional, requires restart):

```yaml
llm_worker:
  max_concurrent: 1       # Concurrent LLM jobs
  max_tries: 5            # Max attempts for LLM jobs
  timeout: 3600           # Job timeout in seconds (default: 1 hour)

operations_worker:
  max_concurrent: 8       # Concurrent operation jobs
  max_tries: 5            # Max attempts for operations
  timeout: 3600           # Job timeout in seconds (default: 1 hour)
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

## Local Auth

Single-user authentication settings live under the `local_auth` group:

```yaml
local_auth:
  cookie_name: cc_session
  cookie_ttl_seconds: 2592000   # 30 days, sliding
  # cookie_secure: true         # Usually leave unset — auto-resolved at boot (see below)
```

| Setting | Default | Description |
|---------|---------|-------------|
| `cookie_name` | `cc_session` | Session cookie name. |
| `cookie_ttl_seconds` | `2592000` | Session cookie lifetime in seconds (30 days, sliding). |
| `cookie_secure` | auto | `Secure` flag on the session cookie. When not set explicitly, it is auto-resolved at boot: `true` if TLS certificate files are present in `tls.cert_dir`, `false` on plain-HTTP deployments. Set it to `true` explicitly when TLS terminates at a reverse proxy that doesn't expose certs to the container (see [Production Deployment](./production.md)). |

The credential files are derived from the data dir rather than configured directly: the operator password hash and hashed API keys live in `<data_dir>/credentials.json`, and the session HMAC secret in `<data_dir>/secrets/session_secret`. `local_auth.credentials_path` / `local_auth.session_secret_path` can override the locations but normally follow `CHAOSCYPHER_DATA_DIR`. None of this is inside `app.db` — see [Backup and Restore](./backup-restore.md#full-disaster-recovery).

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
| **extraction** | Extraction quality knobs — loop detection, dedup thresholds, relationship caps |
| **export** | Package metadata for CCX exports |
| **lexicon** | Lexicon Hub connection settings |
| **paths** | Data directory structure |
| **timeouts** | API, worker, and health check timeouts |
| **ports** | Service ports |
| **batching** | Upload limits, embedding batches, processing batches |
| **pagination** | Page sizes and limits |
| **cors** | Cross-origin request settings |
| **local_auth** | Single-user auth: cookie settings, credential file paths |
| **rate_limit** | Auth endpoint rate limits, nginx zone limits, and the master `enabled` toggle |
| **retries** | Retry counts for various operations |
| **backoff** | Exponential backoff configuration |
| **analysis** | Graph analysis settings |
| **chat_context** | Chat context window and history limits |
| **chat** | Chat tool-loop limits (`max_tool_iterations`, `max_total_tool_calls`), tool approval mode/timeout, mutating-tool list |
| **intervals** | Frontend/backend polling intervals, incl. `chat_approval_poll_ms` |
| **services** | External service URLs |
| **workers** | Worker concurrency and timeout defaults |

:::warning Security defaults

By default, Cortex binds to `0.0.0.0`. Read the [self-hosted threat model](../security/self-hosted-threat-model.md) before exposing the service beyond loopback.

:::

## Context window knobs explained

Three settings describe "the context window", each with a distinct job:

| Setting | What it controls |
|---------|------------------|
| `llm.ai_context_window` | The token budget chat and extraction use when fitting prompts (history compaction, truncation warnings). |
| `llm.ollama_num_ctx` | The `num_ctx` value actually sent to the Ollama API. **Keep this equal to `ai_context_window` when using Ollama** — if it is smaller, Ollama silently drops the head of the prompt. The VRAM presets set both together. |
| `llm.openai_context_window` / `anthropic_context_window` / `gemini_context_window` | The cloud models' context limits, used for budget math when a cloud provider is selected. |

Separately, `llm.ai_max_tokens` limits the **output** length, bounded per cloud provider by `openai_max_output_tokens` / `anthropic_max_output_tokens` / `gemini_max_output_tokens` (the effective request limit is the smaller of the two).

## See also

- [API reference: Settings](../reference/api/settings.md) — read and update configuration at runtime via the REST API; VRAM presets, Ollama model management, and reset operations
