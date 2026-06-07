---
title: CLI Serve
description: Start a local Chaos Cypher API server with chaoscypher serve — the fastest way to expose the REST API to local tools and notebooks without the full Docker stack.
---

# Serve

The `serve` command starts a local API server backed by your knowledge graph database. It is the fastest way to expose Chaos Cypher's API to local tools, notebooks, or custom scripts without the full multi-container stack.

```bash
chaoscypher serve --help
```

## Quick Start

```bash
chaoscypher serve
```

**Sample output:**

```
╭──────────────── Server ────────────────╮
│ Chaos Cypher Local Server               │
│                                        │
│ Database: default                      │
│ API URL:  http://localhost:8081        │
│ Data dir: ~/.local/share/chaoscypher/databases/default │
╰────────────────────────────────────────╯

Database Statistics:
  Nodes: 247
  Edges: 612
  Templates: 8

Starting Cortex...
Press Ctrl+C to stop
```

## Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--port` | `-p` | `8081` | Port to listen on |
| `--host` | `-h` | `localhost` | Host address to bind to |
| `--database` | `-d` | `default` | Database to serve |
| `--reload` | | off | Auto-reload on file changes (development mode) |

## Examples

**Basic serve (default database, port 8081):**

```bash
chaoscypher serve
```

**Custom port:**

```bash
chaoscypher serve --port 9000
```

**Specific database:**

```bash
chaoscypher serve --database my-project
```

**Bind to all interfaces (LAN access):**

```bash
chaoscypher serve --host 0.0.0.0 --port 8081
```

**Development mode with auto-reload:**

```bash
chaoscypher serve --reload
```

## Cortex Detection

`chaoscypher serve` uses whichever server backend is available:

1. **Full Cortex** (`chaoscypher-cortex` installed) — Launches the complete FastAPI application. All API endpoints, middleware, and authentication work exactly as in production.

2. **Built-in minimal server** (fallback) — If Cortex is not installed, a lightweight FastAPI application provides essential endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Server health check |
| `GET /api/v1/stats` | Database statistics |
| `GET /api/v1/nodes` | List nodes (supports `limit`, `offset`, `template_id`) |
| `GET /api/v1/nodes/{node_id}` | Get a node by ID |
| `GET /api/v1/edges` | List edges (supports `limit`, `offset`, `source_node_id`) |
| `GET /api/v1/templates` | List templates |
| `GET /api/v1/templates/{template_id}` | Get a template by ID |

:::note[Install Cortex for the full API]

To get the complete API surface (search, chat, sources, workflows, etc.), install the Cortex package:
```bash
pip install chaoscypher-cortex
```
Or install the CLI with the server extra for the built-in fallback dependencies:
```bash
pip install 'chaoscypher-cli[server]'
```

:::

## CORS

The built-in fallback server includes CORS middleware allowing requests from `localhost:3000`, `localhost:8080`, and `localhost:8081` for local development. When using the full Cortex backend, CORS is configured via `settings.yaml`.

## Stopping the Server

Press `Ctrl+C` to stop the server gracefully.
