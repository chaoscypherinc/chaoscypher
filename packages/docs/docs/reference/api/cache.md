---
title: Cache API
description: REST API for the LLM completion and embedding caches at /api/v1/cache — their settings, size and hit history, and clearing one.
---

# Cache API

Read and change the completion and embedding caches, and clear one.

**Base path:** `/api/v1/cache`

:::note[Authentication required]
All endpoints require a valid session (via the nginx `auth_request` gate).
:::

---

## Endpoints

### Get the cache overview

```
GET /api/v1/cache
```

The completion and embedding caches: enabled flags, size against the cap, and
hits by call site.

**Response** `200 OK` -- [CacheOverviewResponse](#cacheoverviewresponse)

### Change the cache settings

```
PATCH /api/v1/cache
```

Change the caches' enabled flags and size caps; providers built afterwards use
them. The change is written to the `cache` section of `settings.yaml`. Turning
a cache off stops reads and writes for new providers and keeps its store and
stats. Unknown fields are rejected.

**Request body** -- [CacheUpdateRequest](#cacheupdaterequest)

```json
{
  "completions": { "enabled": true, "max_bytes": 268435456 },
  "embeddings": { "enabled": false }
}
```

**Response** `200 OK` -- [CacheOverviewResponse](#cacheoverviewresponse) (the overview after the change)

### Clear one cache

```
POST /api/v1/cache/{name}/clear
```

Delete every entry of cache `name` (`completions` or `embeddings`); its stats
history is kept.

**Response** `200 OK` -- [CacheClearResponse](#cacheclearresponse)

**Errors:** `404 Not Found` for any other name.

---

## Response Models

### CacheOverviewResponse

| Field | Type | Description |
|-------|------|-------------|
| `dir` | string | The directory holding the cache files |
| `caches` | [CacheStatsResponse](#cachestatsresponse)[] | Completions, then embeddings |

### CacheStatsResponse

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | The cache: `completions` or `embeddings` |
| `enabled` | bool | Whether the cache is read and written |
| `require_hit` | bool | Whether a miss is an error (replay mode) rather than a model call |
| `entries` | int | How many entries the cache holds now |
| `bytes` | int | Their total stored size in bytes |
| `max_bytes` | int | Size cap in bytes; least-recently-hit entries are evicted past it |
| `hits` | int | Answers served from the store |
| `misses` | int | Calls that went to the model and were offered to the store |
| `bypasses` | int | Calls the policy kept away from the store |
| `hit_rate` | float | `hits / (hits + misses)`; 0 before the first cacheable call |
| `latency_saved_ms` | int | First-call model time of every hit, summed |
| `by_call_site` | object | Per call site: `hits`, `misses`, `bypasses`, `latency_saved_ms` |

### CacheClearResponse

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | The cache that was cleared |
| `entries_removed` | int | How many entries were deleted |

## Request Models

### CacheUpdateRequest

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `completions` | object | No | Changes to the completion cache: `enabled` (bool) and/or `max_bytes` (int, at least the settings minimum) |
| `embeddings` | object | No | Changes to the embedding cache, same shape |

Omitted fields keep their value. See [Cache](../../getting-started/cache.md) for what the caches hold and when they are bypassed.
