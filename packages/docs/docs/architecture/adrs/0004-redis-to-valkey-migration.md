---
title: "ADR-0004: Migrate from Redis to Valkey"
description: Decision record switching Chaos Cypher's queue backend from Redis (non-OSI license from v7.4+) to Valkey, an Apache-2.0 fork.
---

# 0004. Migrate from Redis to Valkey

Date: 2026-03-15

## Status

Accepted

## Context

A dependency audit on 2026-03-15 revealed that Redis versions 7.4+ (released March 2024) changed from BSD-3-Clause to a dual RSALv2/SSPLv1 license. Neither license is OSI-approved. Our `redis:7-alpine` Docker tag was resolving to Redis 7.4+, meaning we were unknowingly using non-permissive software.

This conflicts with ADR-0002 (Dependency License Policy) which requires permissive licenses for all dependencies.

### Options Considered

| Option | License | Pros | Cons |
|--------|---------|------|------|
| Pin `redis:7.2-alpine` | BSD-3-Clause | No migration needed | EOL Feb 2026 (already past) |
| Upgrade to Redis 8 (AGPLv3) | AGPLv3 | Latest features | Copyleft — network use triggers source disclosure |
| **Valkey 8 (chosen)** | BSD-3-Clause | Drop-in compatible, actively maintained, Linux Foundation backed | Different Docker image name |
| Dragonfly | BSL 1.1 | High performance | Not OSI-approved, different protocol edge cases |

## Decision

1. Migrate from `redis:7-alpine` to `valkey/valkey:8-alpine`.
2. Rename all `REDIS_*` environment variables to `QUEUE_*` (technology-agnostic).
3. Rename all `redis_queue_*` config fields to `queue_*`.

Valkey is the Linux Foundation's BSD-licensed fork of Redis 7.2.4, backed by AWS, Google Cloud, Oracle, Ericsson, and Snap. It is fully protocol-compatible with Redis — the valkey-py client is API-compatible with redis-py for the operations we use, so call sites needed no code changes; we standardized on `from valkey import ...` for clarity.

### Migration scope

- Docker Compose: swap image, rename service, update CLI binaries, rename env vars
- Python config: `redis_queue_*` fields → `queue_*`
- Python healthcheck: `REDIS_*` env reads → `QUEUE_*`
- Frontend types: field name alignment
- Documentation: Redis → Valkey references

## Consequences

### Positive

- **License-clean** — BSD-3-Clause, consistent with ADR-0002
- **Actively maintained** — Linux Foundation governance, regular releases
- **Performance gains** — Valkey 8 has 3x throughput improvements via redesigned I/O threading
- **Technology-agnostic naming** — `QUEUE_*` env vars don't couple to any specific implementation

### Negative

- **Breaking change for existing deployments** — Users with custom `.env` files must rename `REDIS_*` to `QUEUE_*`
- **settings.yaml migration** — Existing `redis_queue_*` keys in settings.yaml become unrecognized (Pydantic ignores extras by default, so no crash — just reverts to defaults)

### Neutral

- **Switched to `valkey-py`** — Native Valkey client with `valkey://` URL scheme

## References

- [Redis License Change (March 2024)](https://redis.io/legal/licenses/)
- [Valkey Project](https://valkey.io/)
- [ADR-0002: Dependency License Policy](0002-dependency-license-policy.md)
- [Valkey Docker Hub](https://hub.docker.com/r/valkey/valkey/)
