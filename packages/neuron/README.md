# ChaosCypher Neuron

**Background task processing — unified worker cell**

Neuron is ChaosCypher's background worker process. A single `cc-neuron`
entry point runs two independently-paced queues in one process:

- **LLM queue** — serialized (default 1 concurrent) for chat, embeddings,
  tool calls, chunk extraction, and per-page vision analysis. Sequential
  execution avoids overwhelming the LLM provider and keeps priority
  queueing fair.
- **Operations queue** — parallel (default 8 concurrent) for source
  indexing, imports/exports, workflows, vision finalization, and other
  CPU/IO-bound tasks that benefit from concurrency.

Tasks are pulled from [Valkey](https://valkey.io/) via ChaosCypher's own
queue client (`chaoscypher_core.queue`); there is no ARQ, Celery, or RQ
dependency.

## Installation

```bash
# Workspace sync — installs core + cortex + neuron + dev tools
uv sync --all-packages --extra dev

# Single-package mode (neuron + its core dependency only)
uv sync --package chaoscypher-neuron
```

The repo uses uv workspaces (see root `pyproject.toml` `[tool.uv.workspace]`).
Install uv via [the official installer](https://docs.astral.sh/uv/getting-started/installation/)
before running these commands; `pip install -e .` is not supported.

## Usage

```bash
# Start the unified worker (both queues)
cc-neuron

# With a custom Valkey instance
QUEUE_HOST=localhost QUEUE_PORT=6379 cc-neuron
```

The worker auto-detects which queue handlers are registered and polls
each queue at its configured concurrency. There is no separate
`cc-neuron-llm` / `cc-neuron-ops` binary — both queues run in the same
process.

### Docker

In the all-in-one image, `cc-neuron` is launched by supervisord alongside
cortex, valkey, and nginx. In the multi-container deployments
(`packages/docker/multi-container/docker-compose.{dev,prod}.yml`) the
worker runs in its own `neuron` service:

```bash
docker run -e QUEUE_HOST=valkey -e QUEUE_PORT=6379 chaoscypher-neuron
```

## Configuration

Worker behaviour is configured through ChaosCypher's settings layer
(env var → `settings.yaml` in the data dir (`/data/settings.yaml` inside
the `app-data` Docker volume) → package default).
Concurrency and timeouts live under `QueueSettings`; only the Valkey
connection itself is read directly from the environment so the worker
can bootstrap before settings are loaded:

```bash
QUEUE_HOST=localhost           # Valkey hostname
QUEUE_PORT=6379                # Valkey port
QUEUE_PASSWORD=chaoscypher     # Valkey password
QUEUE_DB=0                     # Valkey logical DB
LOG_LEVEL=INFO
USE_JSON_LOGGING=false
```

Per-queue concurrency, retry limits, and timeouts are not env-driven —
edit `settings.yaml` and restart the worker, or change them through the
Settings UI (cortex pushes updates via the `settings_changes` channel and
`cc-neuron` picks them up at the next poll boundary).

## Architecture

Neuron is part of the ChaosCypher neural architecture:

- **Core** — domain logic + operational substrate (queue, settings, handlers)
- **Cortex** — FastAPI backend that enqueues work
- **Neuron** — unified worker that consumes both queues 👈 You are here
- **Interface** — React UI

### Why one process, two queues?

- **Separate concurrency control.** The LLM queue intentionally throttles
  to 1; the Operations queue parallelises to 8. Running them in the same
  process avoids container overhead and the need for two scaler knobs
  while keeping each queue independently paced.
- **Shared resources.** Database adapter, LLM provider, settings cache,
  recovery loops, and the Valkey connection pool are initialised once
  per process instead of duplicated across two worker images.
- **Startup recovery.** A single recovery sweep (`SourceRecovery` +
  orphan-task rehydration) runs at boot, not twice.

## Task types

### LLM queue
- `chat_completion` / `chat_background` — chat completions (interactive and background)
- `tool_execution` — execute a tool with the LLM
- `extract_chunk` / `finalize_extraction` — entity / relationship extraction over a chunk + finalize pass
- `vision_page` — per-page vision analysis
- `embed_chunks` — generate chunk embeddings
- `regenerate_template_embeddings` — refresh template embeddings

### Operations queue
- `index_document` — chunking + entity prep
- `import_ccx` / `import_commit` / `import_analysis` — package import pipeline
- `export_graph` / `export_by_sources` — package export
- `execute_workflow` / `execute_step` — workflow execution
- `vision_finalize` — assemble per-page vision results
- `fetch_url` — URL source fetching
- bulk / reset / cleanup ops — `bulk_nodes`, `bulk_edges`, `bulk_templates`,
  `lexicon_import`, `recalculate_quality_scores`, `rebuild_search_indexes`,
  `reset_knowledge_base`, `reset_all`, `graph_cleanup`, `cleanup_orphans`,
  `build_graph_snapshot`

Canonical mapping lives in `chaoscypher_core.constants.OPERATION_QUEUE_ROUTING`
(enforced by lint rule CC044).

## Monitoring

```bash
# Container logs
docker compose logs -f chaoscypher           # all-in-one (supervisord muxes)
docker compose logs -f neuron                # multi-container neuron service

# Queue depth + per-task status via cortex (must carry an authenticated
# session — nginx auth_request gates /api/v1)
curl http://localhost/api/v1/queue/stats
```

## Development

```bash
# Workspace install (from repo root)
uv sync --all-packages --extra dev

# Run tests
uv run pytest packages/neuron

# Auto-restart on source edits (watchdog is not a workspace dependency;
# --with adds it for this run)
uv run --with watchdog watchmedo auto-restart -d packages/neuron/src -p "*.py" -- cc-neuron
```

## Scaling

The all-in-one image runs exactly one `cc-neuron` instance under
supervisord. For horizontal scale-out, use the multi-container
deployment and replicate the `neuron` service:

```bash
cd packages/docker/multi-container
docker compose -f docker-compose.prod.yml up -d --scale neuron=4
```

Each replica polls both queues; Valkey distributes tasks across them.

## License

AGPL-3.0 — see the repository `LICENSE` file.
