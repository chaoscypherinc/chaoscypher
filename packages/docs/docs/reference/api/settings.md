---
title: Settings API
description: Read and update Chaos Cypher configuration at runtime — LLM providers, Ollama instances, VRAM presets, embedding, and log level hot-reload.
---

# Settings API

Read and update application configuration at runtime. Manage VRAM presets, cloud model registries, Ollama connectivity, logging levels, and database reset operations.

**Base path:** `/api/v1/settings`

:::tip[Related pages]

- [Getting started: Configuration](../../getting-started/configuration.md) — `settings.yaml` reference, environment variables, and all settings groups explained

:::

---

## Get Settings

Returns the complete application settings object with all configuration groups.

```
GET /api/v1/settings
```

### Example Request

```bash
curl http://localhost:8080/api/v1/settings
```

### Response

**Status:** `200 OK`

:::note[Trimmed for readability]

Response includes all configuration sections. Only key fields shown per section -- see `settings.yaml` reference for the complete schema.

:::

```json
{
  "app_name": "Chaos Cypher",
  "current_database": "default",
  "data_dir": "/data",
  "dark_mode": true,
  "auto_enable": true,
  "local_auth": {
    "cookie_name": "cc_session",
    "cookie_ttl_seconds": 2592000,
    "cookie_secure": false
  },
  "llm": {
    "chat_provider": "ollama",
    "ollama_instances": [
      {
        "id": "default",
        "name": "Default",
        "base_url": "http://host.docker.internal:11434",
        "enabled": true,
        "healthy": true
      }
    ],
    "ollama_load_balancing": "round_robin",
    "ollama_chat_model": "qwen3:30b-instruct",
    "ollama_num_ctx": 32768,
    "openai_api_key": null,
    "openai_chat_model": "gpt-4.1",
    "anthropic_api_key": null,
    "anthropic_chat_model": "claude-sonnet-4-5",
    "gemini_api_key": null,
    "gemini_chat_model": "gemini-2.5-pro",
    "ai_max_tokens": 65536,
    "ai_temperature": 0.3,
    "thinking_for_chat": true,
    "enable_llm_queueing": true
    // + extraction models, context windows, streaming, cost tracking, etc.
  },
  "queue": {
    "queue_host": "valkey",
    "queue_port": 6379,
    "queue_database": 0
    // + queue_password, queue_ssl
  },
  "chunking": {
    "small_chunk_size": 900,
    "small_chunk_overlap": 150,
    "group_size": 4
    // + min/max chunk sizes, extraction density, normalization
  },
  "embedding": {
    "provider": "local",
    "model": "Qwen/Qwen3-Embedding-0.6B",
    "default_ollama_model": "qwen3-embedding:0.6b"
    // + api_key, api_base, ollama_instance_id, max_text_length, allow_model_download
  },
  "search": {
    "enable_vector_search": true,
    "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
    "vector_dimensions": 1024,
    "min_similarity_threshold": 0.55,
    "enable_rerank": true
    // + rerank model, fulltext language, candidate multiplier, etc.
  },
  "source_processing": {
    "auto_extract_entities": true,
    "entity_deduplication_mode": "semantic",
    "relationship_confidence_threshold": 0.5
    // + chunking strategy, dedup thresholds, max ratios, etc.
  },
  "export": {
    "export_version": "1.0.0",
    "export_license": "CC-BY-SA-4.0"
    // + package name, author, description, tags
  },
  "lexicon": {
    "url": "https://lexicon.chaoscypher.com",
    "api_path": "/api/v1"
    // + timeout, token, credentials
  },
  "paths": {
    "data_dir": "/data",
    "databases_subdir": "databases",
    "app_db_filename": "app.db"
    // + settings paths, graphs, search, imports, static dirs
  },
  "priorities": { "interactive": 10, "background": 50, "default": 0 },
  "timeouts": {
    "llm_chat_wait": 120,
    "http_request": 30,
    "hot_reload_delay": 10
    // + embedding, operation, worker, health check, SQLite timeouts
  },
  "ports": { "web_ui_api": 8080, "valkey": 6379 },
  "batching": {
    "embedding_batch_size": 512,
    "embedding_concurrency": 4,
    "max_upload_files": 20
    // + PDF batching, discovery, export, graph analysis limits, etc.
  },
  "pagination": {
    "default_page_size": 50,
    "max_page_size": 1000,
    "canvas_max_nodes": 5000,
    "canvas_max_edges": 15000
    // + list limits, history limits, citation page size
  },
  "retries": {
    "llm_max_retries": 3,
    "llm_worker_max_tries": 5,
    "operations_worker_max_tries": 5
    // + HTTP, SQLite, extraction retries
  },
  "services": {
    "cortex_internal_url": "http://cortex:8080",
    "valkey_internal_url": "valkey://valkey:6379"
  },
  "backoff": {
    "retry_delays": [2.0, 4.0, 8.0, 16.0],
    "max_seconds": 30
    // + LLM/SQLite backoff multipliers
  },
  "analysis": { "quick_sample_size": 5, "extraction_max_input_chars": 8000 },
  "chat_context": {
    "default_context_window": 32768,
    "history_allocation_percent": 0.50
    // + token estimates, preview lengths, response validation
  },
  "workers": { "operations_max_concurrent": 8, "health_report_interval": 2 },
  "cors": {
    "allowed_origins": ["http://localhost:3000", "http://localhost:8080"]
    // + allow_credentials, allow_methods, allow_headers
  },
  "custom_settings": {}
}
```

---

## Update Settings

Partially update application settings. Changes are persisted to `settings.yaml`. When LLM or search settings change, workers are notified via Valkey pub/sub to hot-reload their providers without restart.

```
PATCH /api/v1/settings
```

### Request Body

Any valid settings fields to update. Supports nested updates by passing the top-level group key.

### Example Request

```bash
curl -X PATCH http://localhost:8080/api/v1/settings \
  -H 'Content-Type: application/json' \
  -d '{
    "llm": {
      "chat_provider": "openai",
      "openai_api_key": "sk-..."
    },
    "search": {
      "min_similarity_threshold": 0.6
    }
  }'
```

### Response

**Status:** `200 OK`

```json
{
  "settings": {
    "app_name": "Chaos Cypher",
    "current_database": "default",
    "llm": {
      "chat_provider": "openai",
      "openai_api_key": "sk-..."
    },
    "search": {
      "min_similarity_threshold": 0.6
    }
  },
  "warnings": [
    {
      "field": "search.vector_dimensions",
      "message": "Vector dimensions changed. Existing embeddings may be orphaned and should be regenerated.",
      "severity": "warning"
    }
  ]
}
```

:::info[Automatic trigger sync]

When `enable_auto_embedding` changes, system triggers for `node.created` and `node.updated` events are automatically updated. Only system workflows are affected -- user-created workflows remain unchanged.

:::

:::warning[Warnings]

The response may include warnings when a change has side effects. For example, changing `vector_dimensions` warns about orphaned embeddings that need regeneration.

:::

---

## Reset Settings

Reset all settings to their default values. The `settings.yaml` file is overwritten with defaults.

```
POST /api/v1/settings/reset
```

### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset
```

### Response

**Status:** `200 OK`

Returns the complete default `Settings` object (same schema as [Get Settings](#get-settings)).

---

## Get Logging Level

Get the current application logging level.

```
GET /api/v1/settings/logging/level
```

### Example Request

```bash
curl http://localhost:8080/api/v1/settings/logging/level
```

### Response

**Status:** `200 OK`

```json
{
  "level": "INFO",
  "numeric_level": 20,
  "available_levels": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
}
```

---

## Set Logging Level

Change the application logging level in real-time. No restart required.

```
POST /api/v1/settings/logging/level
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `level` | string | **Yes** | One of: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/logging/level \
  -H 'Content-Type: application/json' \
  -d '{"level": "WARNING"}'
```

### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "old_level": "INFO",
  "new_level": "WARNING",
  "message": "Logging level changed from INFO to WARNING"
}
```

---

## VRAM Presets

VRAM presets provide pre-configured LLM model and parameter selections optimized for different GPU memory sizes. Presets are loaded from built-in defaults and optionally from user plugins in `data/plugins/presets/`.

### List Presets

List all available VRAM presets, sorted by VRAM size (ascending).

```
GET /api/v1/settings/presets
```

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/presets
```

#### Response

**Status:** `200 OK`

```json
{
  "presets": [
    {
      "name": "vram_16gb",
      "display_name": "16GB VRAM",
      "description": "High-end consumer configuration for 8B parameter models with larger context. Great for complex documents and multi-turn conversations.",
      "vram_gb": 16,
      "gpu_examples": ["RTX 4080 Super", "RTX 5080", "RTX 4080", "RX 7900 XT"],
      "version": "1.0.0",
      "author": "ChaosCypher Team",
      "builtin": true,
      "ollama_settings": {
        "ollama_chat_model": "phi4:14b",
        "ollama_extraction_model": "phi4:14b",
        "ollama_vision_model": "qwen3-vl:8b",
        "ollama_num_ctx": 16384,
        "ollama_num_batch": 2048
      },
      "llm_settings": {
        "ai_context_window": 16384,
        "ai_max_tokens": 32768,
        "extraction_max_tokens": 8192,
        "thinking_for_chat": false
      }
    },
    {
      "name": "vram_24gb",
      "display_name": "24GB VRAM",
      "description": "Enthusiast tier configuration for 30B parameter models with large context windows. Excellent for research and complex knowledge extraction.",
      "vram_gb": 24,
      "gpu_examples": ["RTX 4090", "RTX 3090", "RTX A5000", "RTX 3090 Ti"],
      "version": "1.0.0",
      "author": "ChaosCypher Team",
      "builtin": true,
      "ollama_settings": {
        "ollama_chat_model": "qwen3:30b",
        "ollama_extraction_model": "qwen3:30b-instruct",
        "ollama_vision_model": "qwen3-vl:30b",
        "ollama_num_ctx": 16384,
        "ollama_num_batch": 2048
      },
      "llm_settings": {
        "ai_context_window": 16384,
        "ai_max_tokens": 65536,
        "extraction_max_tokens": 16384,
        "thinking_for_chat": false
      }
    },
    { "...": "... 5 more presets (vram_20gb, vram_32gb, vram_48gb, vram_96gb, vram_128gb) ..." }
  ],
  "count": 7
}
```

---

### Get Preset

Get a specific VRAM preset by ID.

```
GET /api/v1/settings/presets/{preset_id}
```

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `preset_id` | string | **Yes** | Preset identifier (e.g., `vram_24gb`) |

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/presets/vram_24gb
```

#### Response

**Status:** `200 OK`

```json
{
  "name": "vram_24gb",
  "display_name": "24 GB VRAM",
  "description": "Large models for high-end GPUs",
  "vram_gb": 24,
  "gpu_examples": ["RTX 3090", "RTX 4090"],
  "version": "1.0.0",
  "author": "Chaos Cypher",
  "builtin": true,
  "ollama_settings": {
    "ollama_chat_model": "qwen3:30b-instruct",
    "ollama_num_ctx": 32768,
    "ollama_num_batch": 1024
  },
  "llm_settings": {
    "ai_max_tokens": 65536,
    "thinking_for_chat": true,
    "thinking_for_tools": false,
    "thinking_for_extraction": false
  }
}
```

:::note[404 Not Found]

Returned when no preset exists with the given ID.

:::

---

### Apply Preset

Apply a VRAM preset to update LLM settings. Workers are notified via Valkey pub/sub to hot-reload their providers.

```
POST /api/v1/settings/presets/apply
```

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `preset_id` | string | **Yes** | Preset to apply (e.g., `vram_24gb`) |

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/presets/apply \
  -H 'Content-Type: application/json' \
  -d '{"preset_id": "vram_24gb"}'
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "preset_id": "vram_24gb",
  "preset_name": "24 GB VRAM",
  "settings_updated": {
    "ollama_chat_model": "qwen3:30b-instruct",
    "ollama_num_ctx": 32768,
    "ollama_num_batch": 1024,
    "ai_max_tokens": 65536,
    "thinking_for_chat": true,
    "thinking_for_tools": false,
    "thinking_for_extraction": false
  },
  "message": "Applied preset 'vram_24gb' successfully"
}
```

:::info[What gets updated]

Applying a preset updates: `ollama_chat_model`, `ollama_num_ctx`, `ollama_num_batch`, `ai_max_tokens`, `thinking_for_chat`, `thinking_for_tools`, and `thinking_for_extraction`. All other settings (API keys, URLs, instances, etc.) are preserved.

:::

:::note[404 Not Found]

Returned when no preset exists with the given ID.

:::

---

## Cloud Models

The cloud model registry provides metadata about available models for cloud LLM providers (OpenAI, Anthropic, Gemini). Use these endpoints to populate model selection dropdowns and display capabilities and pricing.

### List All Cloud Models

Get all available cloud LLM models grouped by provider.

```
GET /api/v1/settings/cloudmodels
```

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/cloudmodels
```

#### Response

**Status:** `200 OK`

```json
{
  "providers": {
    "openai": {
      "display_name": "OpenAI",
      "models": [
        {
          "id": "gpt-4.1",
          "display_name": "GPT-4.1",
          "context_window": 1047576,
          "max_output_tokens": 32768,
          "supports_vision": true,
          "supports_tools": true,
          "recommended": true,
          "pricing": {
            "input_per_million": 2.0,
            "output_per_million": 8.0
          },
          "notes": null
        }
      ]
    },
    "anthropic": {
      "display_name": "Anthropic",
      "models": [
        {
          "id": "claude-sonnet-4-5",
          "display_name": "Claude Sonnet 4.5",
          "context_window": 200000,
          "max_output_tokens": 64000,
          "supports_vision": true,
          "supports_tools": true,
          "recommended": true,
          "pricing": {
            "input_per_million": 3.0,
            "output_per_million": 15.0
          },
          "notes": null
        }
      ]
    },
    "gemini": {
      "display_name": "Google Gemini",
      "models": [
        {
          "id": "gemini-2.5-pro",
          "display_name": "Gemini 2.5 Pro",
          "context_window": 1048576,
          "max_output_tokens": 65536,
          "supports_vision": true,
          "supports_tools": true,
          "recommended": true,
          "pricing": {
            "input_per_million": 1.25,
            "output_per_million": 10.0
          },
          "notes": null
        }
      ]
    }
  }
}
```

---

### List Models by Provider

Get available models for a specific cloud provider.

```
GET /api/v1/settings/cloudmodels/{provider}
```

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | **Yes** | Provider ID: `openai`, `anthropic`, or `gemini` |

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/cloudmodels/anthropic
```

#### Response

**Status:** `200 OK`

```json
[
  {
    "id": "claude-sonnet-4-5",
    "display_name": "Claude Sonnet 4.5",
    "context_window": 200000,
    "max_output_tokens": 64000,
    "supports_vision": true,
    "supports_tools": true,
    "recommended": true,
    "pricing": {
      "input_per_million": 3.0,
      "output_per_million": 15.0
    },
    "notes": null
  }
]
```

:::note[404 Not Found]

Returned when no provider exists with the given ID.

:::

---

## Ollama Verification

### Verify Ollama URL

Verify that an Ollama instance is running and reachable at the given URL. Checks basic connectivity, retrieves the list of installed models, and reports the Ollama version.

```
POST /api/v1/settings/ollama/verify
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | **Yes** | Ollama base URL to verify (e.g., `http://localhost:11434`) |
| `timeout` | integer | No | Request timeout in seconds. Uses `timeouts.ollama_verify_timeout` from settings if not provided |

### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/ollama/verify \
  -H 'Content-Type: application/json' \
  -d '{"url": "http://localhost:11434", "timeout": 5}'
```

### Response (Success)

**Status:** `200 OK`

```json
{
  "success": true,
  "message": "Ollama is running and reachable",
  "version": "0.6.2",
  "models": ["qwen3:30b-instruct", "snowflake-arctic-embed2", "llama3:8b"],
  "model_count": 3,
  "response_time_ms": 42,
  "error_type": null
}
```

### Response (Failure)

**Status:** `200 OK`

```json
{
  "success": false,
  "message": "Connection refused: could not connect to http://localhost:11434",
  "version": null,
  "models": null,
  "model_count": null,
  "response_time_ms": null,
  "error_type": "connection_error"
}
```

:::info[Always returns 200]

This endpoint always returns `200 OK` regardless of Ollama reachability. Check the `success` field to determine connectivity status. The `error_type` field provides a machine-readable error classification when `success` is `false`.

:::

---

## Ollama Model Management

Manage Ollama models directly from the API -- list installed models, pull new ones, remove unused models, and inspect model details.

### List Installed Models

```
GET /api/v1/settings/ollama/models
```

List all models installed on the configured Ollama instance.

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/ollama/models
```

#### Response

**Status:** `200 OK`

```json
{
  "models": [
    {
      "name": "qwen3:30b-instruct",
      "size": 18200000000,
      "modified_at": "2026-03-01T12:00:00Z",
      "digest": "sha256:abc123..."
    },
    {
      "name": "snowflake-arctic-embed2",
      "size": 1200000000,
      "modified_at": "2026-02-15T08:00:00Z",
      "digest": "sha256:def456..."
    }
  ]
}
```

---

### Pull Model

```
POST /api/v1/settings/ollama/models/pull
```

Pull (download) a model from the Ollama registry. Returns a Server-Sent Events (SSE) stream with real-time download progress.

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | **Yes** | Model name to pull (e.g. `qwen3:30b-instruct`) |

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/ollama/models/pull \
  -H 'Content-Type: application/json' \
  -d '{"model": "qwen3:8b-instruct"}'
```

#### Response (SSE Stream)

**Status:** `200 OK` with `Content-Type: text/event-stream`

```
data: {"status": "pulling manifest"}
data: {"status": "downloading", "completed": 1048576, "total": 4800000000}
data: {"status": "downloading", "completed": 2097152, "total": 4800000000}
data: {"status": "verifying sha256 digest"}
data: {"status": "writing manifest"}
data: {"status": "success"}
```

---

### Remove Model

```
DELETE /api/v1/settings/ollama/models/remove
```

Remove an installed model from Ollama.

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | **Yes** | Model name to remove |

#### Example Request

```bash
curl -X DELETE http://localhost:8080/api/v1/settings/ollama/models/remove \
  -H 'Content-Type: application/json' \
  -d '{"model": "qwen3:8b-instruct"}'
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "message": "Model 'qwen3:8b-instruct' removed"
}
```

#### Errors

| Status | Reason |
|--------|--------|
| `404` | Model not found on Ollama instance |

---

### Get Model Details

```
GET /api/v1/settings/ollama/models/{model:path}/details
```

Get detailed information about a specific installed Ollama model, including parameter count, quantization, and capabilities. The `{model:path}` parameter accepts model names containing slashes and colons (e.g. `qwen3:30b-instruct`).

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | **Yes** | Model name (e.g. `qwen3:30b-instruct`). Colons and slashes are allowed. |

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/ollama/models/qwen3:30b-instruct/details
```

#### Response

**Status:** `200 OK`

```json
{
  "name": "qwen3:30b-instruct",
  "model_info": {
    "general.architecture": "qwen3",
    "general.parameter_count": 30000000000,
    "general.quantization_version": "Q4_K_M"
  },
  "details": {
    "format": "gguf",
    "family": "qwen3",
    "parameter_size": "30B",
    "quantization_level": "Q4_K_M"
  }
}
```

#### Errors

| Status | Reason |
|--------|--------|
| `404` | Model not found on Ollama instance |

---

## Reset Operations

Destructive operations that reset parts of the application database. All reset endpoints return a `ResetResponse` with success status and operation-specific statistics.

:::danger[Irreversible]

All reset operations permanently delete data and cannot be undone. Back up your database before proceeding.

:::

### Reset Workflows

Reset the workflow system (tools, workflows, triggers) to factory defaults.

```
POST /api/v1/settings/reset/workflows
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset/workflows
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "workflows_deleted": 5,
    "tools_deleted": 42,
    "triggers_deleted": 4,
    "workflows_created": 3,
    "tools_created": 40,
    "triggers_created": 2
  }
}
```

**Deletes:** All custom workflows, execution history, user tools, triggers, and trigger history.

**Recreates:** System tools (40+), default workflows (3), default triggers (2).

---

### Reset Chats

Delete all conversations and messages.

```
POST /api/v1/settings/reset/chats
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset/chats
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "chats_deleted": 12,
    "messages_deleted": 347
  }
}
```

---

### Reset Queue

Reset the queue system, cancelling all active jobs and clearing statistics.

```
POST /api/v1/settings/reset/queue
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset/queue
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "jobs_cancelled": 2,
    "tasks_cleared": 58,
    "stats_cleared": true
  }
}
```

**Deletes:** All active/queued jobs (cancelled), completed/failed/cancelled task records, token usage statistics, cost tracking data, task history.

**Preserves:** Queue configuration.

---

### Reset Source Processing

Reset source processing history (imports, chunks, extraction jobs) while preserving committed knowledge.

```
POST /api/v1/settings/reset/source_processing
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset/source_processing
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "source_files_deleted": 15,
    "chunks_deleted": 2340,
    "embeddings_deleted": 2340,
    "extraction_jobs_deleted": 15,
    "imports_dir_cleared": true
  }
}
```

**Deletes:** All source file records, staged document chunks, entity embeddings from source processing, chunk extraction jobs and tasks, uploaded import files directory.

**Preserves:** Committed sources and their chunks, knowledge graph (nodes, edges), workflows, tools, triggers, conversations.

---

### Reset Knowledge

Reset the entire knowledge base (combined reset of sources, graph, and search indices).

```
POST /api/v1/settings/reset/knowledge
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset/knowledge
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "import_history_deleted": 15,
    "graph_nodes_deleted": 450,
    "graph_edges_deleted": 1200,
    "graph_templates_deleted": 0,
    "sources_deleted": 8,
    "chunks_deleted": 4500,
    "search_indices_cleared": true
  }
}
```

**Deletes:** Import history and file records, discovery sessions and AI suggestions, knowledge graph (nodes, edges, templates), document sources (sources, chunks, citations, tags), search indices (full-text and vector).

**Preserves:** Workflows, tools, triggers, conversations, queue statistics.

---

### Reset All

Nuclear reset -- deletes everything and recreates the database with factory defaults.

```
POST /api/v1/settings/reset/all
```

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `confirmation` | string | **Yes** | Must be exactly `"CONFIRM"` to proceed |

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/reset/all \
  -H 'Content-Type: application/json' \
  -d '{"confirmation": "CONFIRM"}'
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "app_db_deleted": true,
    "graphs_deleted": true,
    "search_indices_deleted": true,
    "imports_deleted": true,
    "queue_cleared": true,
    "database_recreated": true,
    "system_tools_created": 40,
    "default_workflows_created": 3,
    "default_triggers_created": 2
  }
}
```

**Deletes:** Entire `app.db` file (including all knowledge graph nodes, edges, templates, search indices, queue history), and uploaded import files.

**Recreates:** Fresh database with system defaults, system tools (40+), default workflows (3), default triggers (2).

:::note[400 Bad Request]

Returned when `confirmation` is not set to `"CONFIRM"`.

:::

---

## Cleanup Operations

### Clean Up Orphaned Graph Items

Safe maintenance operation that removes graph items with invalid references. Primarily useful for cleaning up legacy data before FK constraints were in place.

```
POST /api/v1/settings/cleanup/orphans
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/cleanup/orphans
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "edges_scanned": 1200,
    "edges_removed": 3,
    "nodes_scanned": 450,
    "nodes_removed": 1,
    "templates_scanned": 25,
    "templates_removed": 0
  }
}
```

**Removes:** Edges pointing to non-existent nodes, nodes with `source_id` pointing to non-existent sources, templates with `source_id` pointing to non-existent sources (except system templates).

**Preserves:** Nodes/edges with `source_id=NULL` (intentionally unlinked: chat, workflows, manual), system templates, all valid nodes and edges with proper references.

---

## Seed Operations

### Re-seed Default Templates

Re-seed default system templates. This is a safe operation that only creates templates that do not already exist.

```
POST /api/v1/settings/seed/templates
```

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/seed/templates
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "templates_created": 5,
    "templates_skipped": 20,
    "total_templates": 25
  }
}
```

**Creates (if missing):** Default node templates (Note, Item, Person, Organization, etc.), default edge templates (link, works_at, located_in, etc.), system templates (Workflow, etc.).

:::info[Idempotent]

This endpoint is safe to call multiple times. Existing templates are not modified or duplicated.

:::

---

## TLS Configuration

Manage TLS certificates for HTTPS. All TLS endpoints require authentication.

### Get TLS Status

```
GET /api/v1/settings/tls/status
```

Returns the current TLS configuration state.

```bash
curl http://localhost:8080/api/v1/settings/tls/status
```

**Response** `200 OK`

```json
{
  "enabled": true
}
```

---

### Generate Self-Signed Certificate

```
POST /api/v1/settings/tls/selfsigned
```

Generate a self-signed TLS certificate and enable HTTPS. Suitable for local development and self-hosted deployments where certificate warnings are acceptable. Accepts an optional `hostname` query parameter to set the certificate's subject.

```bash
curl -X POST "http://localhost:8080/api/v1/settings/tls/selfsigned?hostname=localhost"
```

**Response** `200 OK`

```json
{
  "status": "enabled",
  "mode": "self-signed"
}
```

---

### Upload Custom Certificate

```
POST /api/v1/settings/tls/custom
```

Upload a custom TLS certificate and private key (e.g. from Let's Encrypt or a CA).

```bash
curl -X POST http://localhost:8080/api/v1/settings/tls/custom \
  -F "cert_file=@fullchain.pem" \
  -F "key_file=@privkey.pem"
```

**Response** `200 OK`

```json
{
  "status": "enabled",
  "mode": "custom"
}
```

---

### Disable TLS

```
DELETE /api/v1/settings/tls
```

Disable TLS and revert to plain HTTP.

```bash
curl -X DELETE http://localhost:8080/api/v1/settings/tls
```

**Response** `204 No Content`

No response body.

---

## Embedding Models

Manage local embedding models (HuggingFace Sentence Transformers downloaded to the data directory).

### List Curated Embedding Models

```
GET /api/v1/settings/embedding/models
```

Returns the curated list of supported embedding models with metadata. Used to populate the model selection UI.

```bash
curl http://localhost:8080/api/v1/settings/embedding/models
```

**Response** `200 OK`

`curated` is a list of vetted local/Ollama models; `cloud` is a dictionary keyed by provider id (`openai`, `gemini`, ...) whose values are lists of cloud models.

```json
{
  "curated": [
    {
      "name": "Qwen3 Embedding 0.6B",
      "local": "Qwen/Qwen3-Embedding-0.6B",
      "ollama": "qwen3-embedding:0.6b",
      "dimensions": 1024,
      "mrl": true,
      "default": true
    }
  ],
  "cloud": {
    "openai": [
      {
        "name": "Text Embedding 3 Large",
        "model": "text-embedding-3-large",
        "dimensions": 3072,
        "mrl": true,
        "current": true
      }
    ]
  }
}
```

---

### List Downloaded Local Models

```
GET /api/v1/settings/embedding/local/models
```

Returns embedding models already downloaded to the local data directory.

```bash
curl http://localhost:8080/api/v1/settings/embedding/local/models
```

**Response** `200 OK`

```json
{
  "models": [
    {
      "id": "Qwen/Qwen3-Embedding-0.6B",
      "name": "Qwen3-Embedding-0.6B",
      "path": "/data/models/embeddings/models--Qwen--Qwen3-Embedding-0.6B"
    }
  ]
}
```

---

### Download Local Embedding Model

```
POST /api/v1/settings/embedding/local/models
```

Download a HuggingFace embedding model to the local data directory. This is a **blocking** operation: the model is downloaded and validated before the response returns (which can take minutes for large models). There is no background queuing or polling.

```bash
curl -X POST http://localhost:8080/api/v1/settings/embedding/local/models \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen/Qwen3-Embedding-0.6B"}'
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | **Yes** | HuggingFace model ID to download |

**Response** `200 OK`

```json
{
  "model_name": "Qwen/Qwen3-Embedding-0.6B",
  "native_dimensions": 1024,
  "download_time_ms": 12345
}
```

---

### Delete Local Embedding Model

```
DELETE /api/v1/settings/embedding/local/models/{model_id:path}
```

Remove a downloaded embedding model from the local data directory. The `{model_id:path}` parameter accepts model IDs containing slashes (e.g. `Qwen/Qwen3-Embedding-0.6B`).

```bash
curl -X DELETE "http://localhost:8080/api/v1/settings/embedding/local/models/Qwen/Qwen3-Embedding-0.6B"
```

**Response** `200 OK`

```json
{
  "success": true,
  "message": "Model deleted"
}
```

| Status | Description |
|--------|-------------|
| `404` | Model not found locally |

## Public & Host Settings

### Get Public Settings

Returns the subset of `Settings` that the SPA needs to render the UI and make API calls with the correct defaults (page sizes, polling intervals, timeouts, etc.). Reachable without authentication.

```
GET /api/v1/settings/public
```

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/public
```

#### Response

**Status:** `200 OK`

```json
{
  "dark_mode": true,
  "auto_enable": true
}
```

---

### Get Host Access Hint

Returns the hostname the client used to reach this server and whether it is a loopback address. Auth-exempt, used by the setup wizard.

```
GET /api/v1/settings/host
```

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/host
```

#### Response

**Status:** `200 OK`

```json
{
  "request_host": "localhost",
  "is_loopback": true
}
```

---

## LLM Verification & Health

### Verify Cloud LLM Provider

Verify a cloud LLM provider's API key against its public endpoint.

```
POST /api/v1/settings/llm/verify
```

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | **Yes** | Cloud provider name (`openai`, `anthropic`, or `gemini`) |
| `api_key` | string | **Yes** | The API key to verify |

#### Example Request

```bash
curl -X POST http://localhost:8080/api/v1/settings/llm/verify \
  -H 'Content-Type: application/json' \
  -d '{"provider": "openai", "api_key": "sk-..."}'
```

#### Response

**Status:** `200 OK`

```json
{
  "success": true,
  "message": "Key is valid",
  "provider": "openai"
}
```

---

### Get LLM Health Status

Snapshot of the currently-selected LLM chat provider's health.

```
GET /api/v1/settings/llm/health
```

#### Example Request

```bash
curl http://localhost:8080/api/v1/settings/llm/health
```

#### Response

**Status:** `200 OK`

```json
{
  "provider": "ollama",
  "configured": true,
  "verified": true,
  "last_verified_at": "2026-03-09T14:30:00+00:00",
  "missing_models": []
}
```

---

## Response Schema Reference

### ResetResponse

Returned by all reset, cleanup, and seed endpoints.

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the operation completed successfully |
| `data` | object | Operation-specific statistics (varies by endpoint) |

### SettingsUpdateResponse

Returned by the [Update Settings](#update-settings) endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `settings` | object | The complete updated settings object |
| `warnings` | list[SettingsWarning] | Warnings about side effects of the changes (may be empty) |

### SettingsWarning

| Field | Type | Description |
|-------|------|-------------|
| `field` | string | The settings field that triggered the warning |
| `message` | string | Human-readable description of the side effect |
| `severity` | string | `"warning"` or `"info"` |

### VRAMPresetResponse

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Preset identifier |
| `display_name` | string | Human-readable preset name |
| `description` | string | What this preset is optimized for |
| `vram_gb` | integer | Target GPU VRAM in gigabytes |
| `gpu_examples` | list[string] | Example GPUs that match this VRAM tier |
| `version` | string | Preset version |
| `author` | string | Preset author |
| `builtin` | boolean | Whether this is a built-in preset or user-provided |
| `ollama_settings` | object | Ollama model and parameter overrides |
| `llm_settings` | object | LLM behavior overrides |

### ApplyPresetResponse

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the preset was applied successfully |
| `preset_id` | string | The ID of the applied preset |
| `preset_name` | string | Display name of the applied preset |
| `settings_updated` | object | Key-value pairs of all settings that were changed |
| `message` | string | Human-readable confirmation message |

### OllamaVerifyResponse

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether Ollama is reachable |
| `message` | string | Human-readable status message |
| `version` | string or null | Ollama version (when reachable) |
| `models` | list[string] or null | List of installed model names (when reachable) |
| `model_count` | integer or null | Number of installed models (when reachable) |
| `response_time_ms` | integer or null | Round-trip time in milliseconds (when reachable) |
| `error_type` | string or null | Machine-readable error classification (when unreachable) |

### CloudModelInfo

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Model identifier used in API calls |
| `display_name` | string | Human-readable model name |
| `context_window` | integer | Maximum input context window in tokens |
| `max_output_tokens` | integer | Maximum output tokens per request |
| `supports_vision` | boolean | Whether the model supports image inputs |
| `supports_tools` | boolean | Whether the model supports tool/function calling |
| `recommended` | boolean | Whether this model is recommended for use |
| `pricing` | object or null | Pricing with `input_per_million` and `output_per_million` (USD) |
| `notes` | string or null | Additional notes about the model |

### LoggingLevelResponse

| Field | Type | Description |
|-------|------|-------------|
| `level` | string | Current level name |
| `numeric_level` | integer | Numeric Python logging level |
| `available_levels` | list[string] | All valid level names |

### SetLoggingLevelResponse

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the level was changed |
| `old_level` | string | Previous logging level |
| `new_level` | string | New logging level |
| `message` | string | Human-readable confirmation |
