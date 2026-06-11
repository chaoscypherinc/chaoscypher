---
title: Health API
description: System health endpoint that checks all Chaos Cypher subsystems — database, queue, LLM, embeddings, and search — and returns aggregated status.
---

# Health

System health monitoring endpoint that aggregates status from all subsystems.

## Check System Health

```
GET /api/v1/health
```

Returns the overall health status and individual subsystem checks.

### Example Request

```bash
curl http://localhost/api/v1/health
```

### Response

**Status:** `200 OK`

```json
{
  "healthy": true,
  "status": "ok",
  "checks": {
    "ollama": {
      "status": "ok",
      "message": "Connected (v0.9.0)",
      "details": {
        "base_url": "http://localhost:11434",
        "version": "0.9.0"
      }
    },
    "chat_model": {
      "status": "ok",
      "message": "qwen3:30b-instruct installed"
    },
    "extraction_model": {
      "status": "warning",
      "message": "Not configured (using chat model)"
    },
    "embeddings": {
      "status": "ok",
      "message": "Qwen3-Embedding-0.6B ready (ollama)",
      "details": {
        "provider": "ollama",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "dimensions": 1024
      }
    },
    "queue": {
      "status": "ok",
      "message": "Valkey connected"
    },
    "llm_worker": {
      "status": "ok",
      "message": "Running (idle)"
    },
    "ops_worker": {
      "status": "ok",
      "message": "Running (idle)"
    },
    "search_index": {
      "status": "ok",
      "message": "1,500 docs / 1,500 vectors",
      "details": {
        "fulltext_count": 1500,
        "vector_count": 1500,
        "vector_dimension": 1024
      }
    },
    "graph": {
      "status": "ok",
      "message": "1,500 entities / 3,200 relationships"
    },
    "disk_space": {
      "status": "ok",
      "message": "Disk space OK: 412.5 GB free"
    },
    "error_rate": {
      "status": "ok",
      "message": "Error rate 2% (1/50 tasks)"
    },
    "database": {
      "status": "ok",
      "message": "Database accessible and writable"
    }
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `healthy` | bool | `true` when no **critical** check reports `error` (see below) |
| `status` | string | Overall status: `"ok"` (healthy) or `"degraded"` (a critical check is failing) |
| `checks` | object | Per-subsystem check results — **omitted for unauthenticated callers** |
| `checks.ollama` | object | Ollama connectivity, version, and installed models (Ollama provider only) |
| `checks.provider` | object | Cloud provider API-key configuration (OpenAI / Anthropic / Gemini providers — replaces `ollama`) |
| `checks.chat_model` | object | Whether the configured chat model is installed (Ollama only) |
| `checks.extraction_model` | object | Whether the configured extraction model is installed (Ollama only; `warning` when unconfigured) |
| `checks.vision_model` | object | Whether the configured vision model is installed (Ollama only; present only when configured) |
| `checks.embeddings` | object | Embedding provider status (`provider`, `model`, `dimensions`) |
| `checks.queue` | object | Valkey queue connection status |
| `checks.llm_worker` | object | LLM queue worker heartbeat |
| `checks.ops_worker` | object | Operations queue worker heartbeat |
| `checks.search_index` | object | Search index status (details: `fulltext_count`, `vector_count`, `vector_dimension`) |
| `checks.graph` | object | Knowledge graph entity / relationship counts |
| `checks.disk_space` | object | Free disk space on the data directory vs. warn/error thresholds |
| `checks.error_rate` | object | Recent task failure rate across the worker queues |
| `checks.database` | object | Database connectivity and writability |

When Ollama itself is unreachable, the model checks (`chat_model`, `extraction_model`, `vision_model`) are omitted — only the `ollama` error is reported.

Each subsystem check includes:

- `status` — `"ok"`, `"warning"`, or `"error"`
- `message` — human-readable description
- `details` — provider-specific key-value pairs (optional)
- `category` — `"resource"`, `"service"`, or `"operational"` (optional)
- `auto_recoverable` — `true` when the check expects self-healing (optional)

### What Drives `healthy`

Only the **critical** checks flip the overall flag: `ollama` (or `provider` on cloud providers), `chat_model` (Ollama only), `queue`, `llm_worker`, and `ops_worker`. Any other check (`search_index`, `graph`, `disk_space`, `error_rate`, `database`, `embeddings`, ...) can report `error` without changing `healthy` — inspect `checks` directly if you need to alert on those.

### Response Caching

Detailed health responses are cached in-process for **5 seconds** — polling faster than that returns the cached snapshot without re-running the probes.

:::note[Unauthenticated access]

The `checks` field is omitted when the request bypasses the auth gate (e.g. Docker `HEALTHCHECK` probes hitting the internal port directly). This prevents fingerprinting the deployed LLM stack. Unauthenticated callers see only `{"healthy": true, "status": "ok"}`.

:::

## Check Auth Diagnostic

```
GET /api/v1/health/auth
```

Diagnostic endpoint for detecting nginx `auth_request` misconfiguration. **No authentication required** — the endpoint is intentionally public so it remains usable when auth itself is broken.

When nginx's `auth_request` directive is misconfigured, `X-Auth-User` may not arrive at Cortex, producing silent 401 storms. Poll this endpoint to detect and diagnose the problem without needing a working auth session.

### Example Request

```bash
curl http://localhost/api/v1/health/auth
```

### Response

**Status:** `200 OK`

**Healthy (nginx forwarding correctly):**

```json
{
  "x_auth_user_present": true,
  "recent_failed_attempts": 0,
  "last_failure_at": null
}
```

**Broken nginx (auth header not being forwarded):**

```json
{
  "x_auth_user_present": false,
  "recent_failed_attempts": 142,
  "last_failure_at": "2026-05-06T18:32:11.000000+00:00"
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `x_auth_user_present` | bool | Whether `X-Auth-User` is present in this specific request — reflects only the current call |
| `recent_failed_attempts` | int | Count of 401 responses issued in the last 5-minute sliding window |
| `last_failure_at` | string \| null | ISO-8601 UTC timestamp of the most recent 401, or `null` if none has occurred in this process lifetime |

When `recent_failed_attempts` is non-zero on requests that should be authenticated, the nginx `auth_request` forward is not reaching Cortex. Check nginx logs and verify the `auth_request` directive points to the correct upstream.

See [Diagnosing auth misconfiguration](../../security/self-hosted-threat-model.md#diagnosing-auth-misconfiguration) for troubleshooting steps.
