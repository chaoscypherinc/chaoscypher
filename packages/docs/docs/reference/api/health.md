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
curl http://localhost:8080/api/v1/health
```

### Response

**Status:** `200 OK`

```json
{
  "healthy": true,
  "status": "ok",
  "checks": {
    "llm": {
      "status": "ok",
      "details": {
        "provider": "ollama",
        "model": "qwen3:30b-instruct"
      }
    },
    "queue": {
      "status": "ok",
      "details": {
        "host": "valkey",
        "port": 6379
      }
    },
    "search": {
      "status": "ok",
      "details": {
        "fulltext_doc_count": 1500,
        "vector_index_size": 1500,
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B"
      }
    },
    "embedding": {
      "status": "ok",
      "details": {
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "dimensions": 1024
      }
    },
    "database": {
      "status": "ok",
      "details": {
        "database_name": "default"
      }
    }
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `healthy` | bool | `true` when all critical subsystems are up |
| `status` | string | Overall status: `"ok"` (healthy) or `"degraded"` (one or more checks failing) |
| `checks` | object | Per-subsystem check results — **omitted for unauthenticated callers** |
| `checks.llm` | object | LLM provider connectivity and model availability |
| `checks.queue` | object | Valkey queue connection status |
| `checks.search` | object | Search index status and embedding model info |
| `checks.embedding` | object | Embedding service status |
| `checks.database` | object | Database connectivity |

Each subsystem check includes:

- `status` — `"ok"`, `"warning"`, or `"error"`
- `message` — human-readable description
- `details` — provider-specific key-value pairs (optional)
- `category` — `"resource"`, `"service"`, or `"operational"` (optional)
- `auto_recoverable` — `true` when the check expects self-healing (optional)

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
curl http://localhost:8080/api/v1/health/auth
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
