---
title: CLI Reference
description: Complete reference for the chaoscypher CLI — source management, knowledge graph operations, AI chat, database management, local serving, and package sharing.
---

# CLI Reference

The Chaos Cypher CLI (`chaoscypher`) provides command-line access to all core features -- source management, knowledge graph operations, AI chat, database management, local serving, multi-package composition, and package sharing.

## Installation

```bash
# Install uv first: https://docs.astral.sh/uv/getting-started/installation/
uv sync --package chaoscypher-cli
```

Verify the installation:

```bash
uv run chaoscypher --version
```

```
chaoscypher, version 0.1.0
```

## Help Output

Running `chaoscypher --help` displays the full list of available commands:

```
chaoscypher --help
Usage: chaoscypher [OPTIONS] COMMAND [ARGS]...

  Chaos Cypher CLI - Knowledge Graph Platform.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  benchmark    Run and inspect the extraction benchmark
  chat         Chat with AI using your knowledge graph
  completions  Generate shell completion script (bash, zsh, fish)
  compose      Multi-package orchestration and composition
  config       View and manage CLI configuration
  db           Manage databases (create, list, switch, delete, info, migrate)
  doctor       Run a comprehensive system diagnostic sweep
  graph        Build and manage knowledge graphs
  health       Check system health (LLM, embedding, search, database)
  lexicon      Lexicon Hub - login, search, manage packages
  mcp          Start MCP server for AI assistant integration
  pull         Download a package from Lexicon Hub
  push         Upload a package to Lexicon Hub
  serve        Start the local API server
  setup        Configure LLM provider for extraction and chat
  source       Add, list, search, and manage document sources
  upgrade      Apply pending Alembic migrations (alembic upgrade head)
```

## First-Time Setup

Run the setup wizard to configure your LLM provider:

```bash
chaoscypher setup
```

The wizard guides you through provider selection (Ollama, OpenAI, Anthropic, or Gemini), model configuration, and connection testing. For Ollama users, a VRAM-based preset system automatically selects appropriate models for your GPU.

```bash
# Skip the provider prompt
chaoscypher setup --provider ollama

# Ollama with a specific VRAM tier
chaoscypher setup --provider ollama --vram 24

# Non-interactive mode for CI/scripts (auto-detects provider from env vars)
chaoscypher setup --non-interactive
```

See [Configuration](config.md) for details.

## Command Reference

### Core Commands

| Command | Description |
|---------|-------------|
| [`setup`](config.md#setup-wizard) | Configure LLM provider (Ollama, OpenAI, Anthropic, Gemini) |
| [`chat`](chat.md) | Chat with AI using your knowledge graph (single message or interactive) |
| [`source`](sources.md) | Add, list, search, and manage document sources (includes `source quality`) |
| [`graph`](graph.md) | Build and manage knowledge graphs (nodes, links, templates, workflows, packages) |
| [`db`](database.md) | Manage databases (create, list, switch, delete, info, migrate) |
| [`health`](#health) | Check system health -- LLM, embedding, search, and database status |
| [`upgrade`](#upgrade) | Apply pending Alembic migrations (`alembic upgrade head`) |

### Package Management

| Command | Description |
|---------|-------------|
| [`lexicon`](lexicon.md) | Lexicon Hub authentication and package management |
| `pull` | Download a package from Lexicon Hub (shortcut for `lexicon pull`) |
| `push` | Upload a package to Lexicon Hub (shortcut for `lexicon push`) |

### Runtime Commands

| Command | Description |
|---------|-------------|
| [`mcp`](mcp.md) | Start [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server for AI assistant integration |
| [`serve`](#serve) | Start a local API server |
| [`compose`](#compose) | Multi-package orchestration using `axiomatize.yaml` |

### Utility Commands

| Command | Description |
|---------|-------------|
| [`config`](config.md) | View and manage CLI configuration |
| [`completions`](#shell-completions) | Generate shell completion scripts (bash, zsh, fish) |
| [`doctor`](utilities.md#doctor) | Run a comprehensive system diagnostic sweep (superset of `health`) |
| `diagnostics` | Export a diagnostic bundle for bug reports |
| [`benchmark`](benchmark.md) | Run and inspect the extraction benchmark |

### Shortcuts

For convenience, some commands are available at both the top level and within their group:

| Shortcut | Equivalent | Purpose |
|----------|-----------|---------|
| `chaoscypher pull <pkg>` | `chaoscypher lexicon pull <pkg>` | Download a package |
| `chaoscypher push <path>` | `chaoscypher lexicon push <path>` | Upload a package |

Quality evaluation commands are available under `chaoscypher source quality` (e.g., `chaoscypher source quality score <id>`). See the [Quality reference](quality.md) for details.

## health

Check that all Chaos Cypher system components are reachable and configured. The check runs in parallel and reports the status of Ollama, your configured chat and extraction models, embeddings, the search index, and the graph database. It takes no options.

```bash
chaoscypher health
```

### Sample Output

``` { .text .no-copy }

  Chaos Cypher System Health
  -----------------------------------
  + Ollama          Connected at http://localhost:11434
  + Chat Model      qwen3:30b
  + Extraction      qwen3:30b-instruct
  + Embeddings      qwen3-embedding:0.6b configured
  + Search Index    18,432 docs / 18,432 vectors
  + Database        247 entities / 612 relationships

  All systems healthy.

```

**Status indicators:**

| Symbol | Meaning |
|--------|---------|
| `+` (green) | Component is healthy |
| `x` (red) | Component has a problem |
| `!` (yellow) | Warning or not configured |

The command exits with status 0 if all checks pass, or non-zero if any check fails. For the full pre-launch sweep (which adds Lexicon hub, local Cortex API, settings-file, and stale-file checks) run `chaoscypher doctor`. See [Utility Commands → health](utilities.md#health) for the detailed check table.

---

## upgrade

Apply pending Alembic migrations against the configured database.

```bash
chaoscypher upgrade
```

This is equivalent to running `alembic upgrade head` directly. Cortex runs the same command on startup; `chaoscypher upgrade` is the operator-grade alternative for ad-hoc invocations — for example, after restoring a backup or pulling a new release in a dev environment.

### Options

| Option | Description |
|--------|-------------|
| `--database NAME` | Database to upgrade (defaults to the current database) |

```bash
# These are equivalent — pick whichever fits your workflow:
chaoscypher upgrade
# or
uv run alembic upgrade head
```

A non-zero exit from Alembic propagates as a non-zero exit code from `chaoscypher upgrade`.

See [Upgrading](../../getting-started/upgrading.md#operator-grade-upgrade-chaoscypher-upgrade) and [ADR-0006](../../architecture/adrs/0006-re-adopt-alembic.md) for background.

---

## serve

Start a local API server. This launches Cortex backed by your knowledge graph database.

```bash
chaoscypher serve
```

If Cortex is installed, it runs the full Cortex server. Otherwise, a built-in lightweight fallback server provides basic endpoints.

```
chaoscypher serve
╭──── Server ────╮
│ ChaosCypher Local Server           │
│                                    │
│ Database: default                  │
│ API URL:  http://localhost:8081    │
│ Data dir: ~/.local/share/chaoscypher/databases/default │
╰────────────────╯

Database Statistics:
  Nodes: 142
  Edges: 387
  Templates: 5

Starting Cortex...
Press Ctrl+C to stop
```

| Option | Description |
|--------|-------------|
| `--port, -p` | API port (default: `8081`) |
| `--host, -h` | Host to bind to (default: `localhost`) |
| `--database, -d` | Database to serve (default: `default`) |
| `--reload` | Auto-reload on file changes (dev mode) |

```bash
# Serve on a custom port
chaoscypher serve --port 9000

# Serve a specific database
chaoscypher serve --database my-project

# Enable auto-reload for development
chaoscypher serve --reload
```

## compose

Multi-package orchestration and composition. Combine multiple `.ccx` packages defined in an `axiomatize.yaml` configuration file into a unified knowledge system with merged graphs and shared contexts.

```bash
chaoscypher compose --help
```

```
chaoscypher compose --help
Usage: chaoscypher compose [OPTIONS] COMMAND [ARGS]...

  Multi-package orchestration and composition.

  Compose enables combining multiple .ccx packages defined in
  axiomatize.yaml into a unified knowledge system.

Options:
  --help  Show this message and exit.

Commands:
  build  Build composition package
  down   Stop composition services
  run    Run a one-off command
  up     Start composition services
```

### compose build

Compile an `axiomatize.yaml` into a runtime database. Resolves all referenced packages (from Lexicon Hub or local), downloads them, and merges them into a unified knowledge database ready for serving.

```bash
chaoscypher compose build
```

| Option | Description |
|--------|-------------|
| `--config, -c` | Path to composition config file (default: `axiomatize.yaml`) |
| `--clean` | Clean output directory before building |

```bash
# Build from a custom config file
chaoscypher compose build --config my-compose.yaml

# Clean build (removes previous output first)
chaoscypher compose build --clean
```

### compose up

Start the composition defined in `axiomatize.yaml`. Builds the database if it does not exist or if `--build` is specified, then starts a knowledge server from the composed packages.

```bash
chaoscypher compose up
```

| Option | Description |
|--------|-------------|
| `--config, -c` | Path to composition config file (default: `axiomatize.yaml`) |
| `--port, -p` | API port (overrides config setting) |
| `--detach, -d` | Run in background |
| `--build, -b` | Force rebuild before starting |

```bash
# Start on a custom port
chaoscypher compose up --port 9000

# Start in the background
chaoscypher compose up --detach

# Force rebuild and start
chaoscypher compose up --build
```

### compose down

Stop composition services started by `compose up --detach`.

```bash
chaoscypher compose down
```

| Option | Description |
|--------|-------------|
| `--config, -c` | Path to composition config file (default: `axiomatize.yaml`) |

### compose run

Execute a one-off command in the composition context. Sets environment variables for the composed database so scripts and tools can access the merged data.

```bash
chaoscypher compose run python script.py
```

| Option | Description |
|--------|-------------|
| `COMMAND` | Command and arguments to execute (required) |
| `--config, -c` | Path to composition config file (default: `axiomatize.yaml`) |

```bash
# Run tests against composed data
chaoscypher compose run pytest packages/*/tests/

# Run an analysis script with a custom config
chaoscypher compose run --config my-compose.yaml python analyze.py
```

## Global Options

The top-level CLI supports these options:

| Flag | Description |
|------|-------------|
| `--version` | Show the version and exit |
| `--help` | Show the help message and exit |

Most commands additionally support these output options:

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON |
| `--quiet, -q` | Minimal output |
| `--verbose, -v` | Detailed output |
| `--database, -d` | Target database (default: `default`) |

## Shell Completions

Generate tab completions for your shell using the `completions` command. Supports bash, zsh, and fish.

### Auto-install (recommended)

```bash
# Bash
chaoscypher completions bash --install

# Zsh
chaoscypher completions zsh --install

# Fish
chaoscypher completions fish --install
```

The `--install` flag writes the completion script directly to your shell configuration file (`~/.bashrc`, `~/.zshrc`, or `~/.config/fish/completions/chaoscypher.fish`).

### Manual installation

Print the completion script to stdout for manual setup:

```bash
# Print bash completions
chaoscypher completions bash

# Redirect to a file
chaoscypher completions bash > ~/.bash_completion.d/chaoscypher
```

### Other options

| Option | Description |
|--------|-------------|
| `SHELL` | Shell to generate for: `bash`, `zsh`, or `fish` (required) |
| `--install` | Install completions to shell config |
| `--uninstall` | Remove completions from shell config |
| `--show-install, -i` | Show detailed installation instructions |

```bash
# View installation instructions without installing
chaoscypher completions zsh --show-install

# Remove previously installed completions
chaoscypher completions bash --uninstall
```

## Configuration

All configuration lives in a single `settings.yaml` inside the data directory — the same file the web UI **Settings** page edits, so the CLI, the workers, and the web app always read one config. Manage it with [`chaoscypher config`](config.md) and [`chaoscypher setup`](config.md#setup-wizard); change the active database with [`chaoscypher db switch`](database.md#switch-database). Lexicon login state (token, username) is stored separately in `auth.json` in the config directory.

The data directory holding `settings.yaml` is platform-specific:

| Platform | Data directory |
|----------|----------------|
| **Linux** | `~/.local/share/chaoscypher/` |
| **macOS** | `~/Library/Application Support/chaoscypher/` |
| **Windows** | `%LOCALAPPDATA%\chaoscypher\` |
| **Docker** | `/data/` |

If you are upgrading from a pre-unification version, the old client-only `cli.yaml` is no longer read at all — it is ignored with a one-line startup notice. Run `chaoscypher setup` once (or set values with `chaoscypher config set`), then delete the stale `cli.yaml`.

See [Configuration](config.md) for full details.
