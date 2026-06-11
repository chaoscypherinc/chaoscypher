---
title: Plugin trust model
description: How user-installed plugins are trusted, how to disable them, and what they can do.
---

# Plugin trust model

User-installed plugins (loaders, tool plugins, domains) run **in-process with full Python privileges** — they can read your data dir, make outbound network calls, and load arbitrary code.

## Default policy

`CHAOSCYPHER_ALLOW_USER_PLUGINS=1` — user plugins are loaded.

To disable user plugins entirely:

```bash
CHAOSCYPHER_ALLOW_USER_PLUGINS=0 docker compose up -d
```

## What plugins can do

- Read/write `{data-dir}/`.
- Make HTTP requests.
- Import any installed Python package.
- Use the configured LLM providers via Chaos Cypher's wrappers (so they pay the rate limits the operator paid for).

## Where plugins live

- `{data-dir}/plugins/loaders/` — source loaders (run at `*_loader.py` files)
- `{data-dir}/plugins/tools/` — tool plugins (run at `*_plugin.py` files)
- `{data-dir}/plugins/domains/` — domain definitions (`.jsonld` data files)
- `{data-dir}/plugins/cleaners/` — data cleaners (run at `*.py` files)
- `{data-dir}/plugins/archive_handlers/` — archive handlers (run at `*.py` files)

Python files are executed at startup via `importlib.util.spec_from_file_location()` — any top-level code runs immediately.

## Audit trail

Every user plugin file loaded is logged at `WARNING` level with its absolute path and SHA-256 digest. Grep your logs for `user_plugin_loaded` to see what ran.

## Trust posture for self-hosters

You are the only operator. Treat plugins like Python packages: read the code before running it, prefer signed/well-known sources, and disable the loader if you've been handed a `.zip` from somewhere you don't trust.

For a more aggressive isolation story, see the [self-hosted threat model](./self-hosted-threat-model.md).

## Operator checklist for shared deployments

If you're running Chaos Cypher in a multi-user or untrusted environment:

- Run the Chaos Cypher service as a dedicated unprivileged user.
- Set `CHAOSCYPHER_ALLOW_USER_PLUGINS=0` in the service environment.
- Install trusted plugins via pip entry points instead (`chaoscypher.providers` for LLM providers, `chaoscypher.cleaners` for cleaners, `chaoscypher.archive_handlers` for archive handlers); entry-point plugins are subject to the same process privileges but at least route through your package manager and CI.

## Entry-point plugin groups

Plugins distributed as pip packages register under these entry-point groups — any other group name (e.g. `chaoscypher.plugins.*`) is silently ignored:

| Entry-point group | Plugin kind | Loaded class/callable |
|---|---|---|
| `chaoscypher.providers` | LLM providers | must subclass `BaseLLMProvider` |
| `chaoscypher.cleaners` | normalizer cleaners | cleaner contract |
| `chaoscypher.archive_handlers` | archive handlers | archive-handler contract |
| `chaoscypher.extensions` | Cortex API routers (e.g. enterprise features) | callable accepting an `APIRouter` |

Entry points are resolved via `importlib.metadata` and `ep.load()` imports the target module — any top-level code runs in the server process with full privileges, same as directory plugins. `CHAOSCYPHER_ALLOW_USER_PLUGINS=0` does **not** disable entry-point plugins; uninstall the package to remove one.
