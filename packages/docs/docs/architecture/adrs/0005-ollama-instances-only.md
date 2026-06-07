---
title: "ADR-0005: Ollama Configuration via Instances List Only"
description: Decision record consolidating Ollama configuration to a single instances list, removing the legacy ollama_base_url field.
---

# 0005. Ollama configuration via instances list only

Date: 2026-04-08

## Status

Accepted

## Context

Until this change, Ollama could be configured via two coexisting fields on
`LLMSettings`:

1. `ollama_base_url: str` — a single URL field, the original Ollama setting.
2. `ollama_instances: list[OllamaInstance]` — a list of instances added later
   to support load balancing across multiple Ollama servers.

Both were live, with code paths threading through every layer of the stack
(load balancer, embedding factory, LLM factory, health checks, CLI, frontend
settings UI). The "single URL" field was treated as a fallback that the load
balancer materialized into a synthetic default instance whenever
`ollama_instances` was empty. The frontend's "Ollama URL" form field wrote
to `ollama_base_url`; the multi-instance editor wrote to `ollama_instances`.
Result: two sources of truth, drift risk between them, and an explicit
"backwards compat" fallback in `OllamaLoadBalancer.reload_config`.

### Why we cared

`CLAUDE.md` is unambiguous about backwards-compat shims:

> NEVER add support for legacy/deprecated formats no longer in use.
> If you discover legacy support code, propose removing it.

This refactor was discovered during a CLAUDE.md compliance audit. The
reviewer flagged the "Backward compatibility: if no instances configured,
create one from legacy URL" branch in the load balancer as a textbook
violation. Chaos Cypher is pre-production, so no migration window was
required — we could rip and replace.

## Decision

Remove `ollama_base_url` from `LLMSettings` entirely. Make
`ollama_instances` the sole source of truth for Ollama URLs:

- `LLMSettings.ollama_instances` now defaults to a one-element list
  containing a seeded instance with `id="default"`,
  `name="Default"`, `base_url="http://host.docker.internal:11434"`.
- A new `LLMSettings.primary_ollama_url` computed property returns the
  first enabled instance's URL. Health checks, the CLI URL probe, the
  embedding factory fallback, and any other "give me an Ollama URL" caller
  use this property.
- The load balancer's "no instances → synthesize from legacy URL" branch is
  deleted. The default factory ensures `ollama_instances` is always
  populated, so the branch was unreachable in practice anyway.
- The frontend's "single Ollama URL" form (`OllamaUrlField`) now reads and
  writes `ollama_instances[0].base_url`. The multi-instance editor remains
  the only way to configure additional GPUs.
- The first-run setup wizard writes a single seeded instance instead of a
  top-level URL field.
- The CLI's `ollama_url` field (its own user-visible config name) is now
  materialized into a single instance dict when constructing `LLMSettings`,
  rather than being passed as `ollama_base_url`.

## Consequences

### Positive

- One source of truth for Ollama URLs.
- The load balancer's fallback branch is gone, removing dead-code rot risk.
- The frontend Settings UI is unchanged for single-URL users — the form
  field still exists, it just reads/writes a different backing field.
- New users who never touch the URL get a working default that matches what
  the all-in-one Docker container expects.

### Negative

- `extra="forbid"` on `LLMSettings` means any user with a stale
  `settings.yaml` containing `ollama_base_url` will get a Pydantic validation
  error on container startup. **Acceptable because we are pre-production
  and have no installed user base to migrate.** If we ever need to support
  this scenario in the future, the migration path is to add a `model_validator`
  on `LLMSettings` that reads a legacy `ollama_base_url` key out of the input
  dict and seeds the default instance from it before validation runs.

### Files touched

- `packages/core/src/chaoscypher_core/settings.py`
- `packages/core/src/chaoscypher_core/adapters/llm/load_balancer.py`
- `packages/core/src/chaoscypher_core/adapters/llm/factory.py`
- `packages/core/src/chaoscypher_core/adapters/llm/providers/ollama_provider.py`
- `packages/core/src/chaoscypher_core/adapters/embedding/factory.py`
- `packages/cortex/src/chaoscypher_cortex/features/health/service.py`
- `packages/cortex/src/chaoscypher_cortex/features/settings/ollama_models_api.py`
- `packages/cortex/src/chaoscypher_cortex/features/chats/streaming_utils.py`
- `packages/cli/src/chaoscypher_cli/context.py`
- `packages/cli/src/chaoscypher_cli/config.py`
- `packages/interface/src/types/settings.ts`
- `packages/interface/src/pages/settings/hooks/useProviderSettings.ts`
- `packages/interface/src/pages/settings/components/ProviderSelector.tsx`
- `packages/interface/src/pages/settings/LLMProviderTab.tsx`
- `packages/interface/src/pages/SetupPage.tsx`
- `packages/core/tests/unit/adapters/embedding/test_factory.py`
- `packages/cortex/tests/unit/features/health/test_health_service.py`
- `packages/docs/docs/getting-started/configuration.md`
- `packages/docs/docs/developer-guide/llm-providers.md`
- `packages/docs/docs/reference/api/settings.md`
- `packages/docs/blog/2026-03-12-local-ai-knowledge-graph.md`
