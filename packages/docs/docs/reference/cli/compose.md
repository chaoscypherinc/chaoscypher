---
title: Compose Commands
description: Manage knowledge graph compositions with chaoscypher compose — declaratively merge multiple Lexicon packages into a single unified database using axiomatize.yaml.
---

# Compose Commands

The `compose` command group manages knowledge graph compositions defined in an `axiomatize.yaml` file. A composition is a declarative description of one or more Lexicon packages (or local knowledge packages) that are merged into a single unified database ready to serve.

```bash
chaoscypher compose --help
```

## Subcommands

| Subcommand | Description |
|------------|-------------|
| [`init`](#init) | Write a starter `axiomatize.yaml` |
| [`build`](#build) | Merge the listed packages into one runtime database |
| [`up`](#up) | Build (if needed) and serve the composition over HTTP |
| [`down`](#down) | Stop the detached HTTP server |
| [`mcp`](#mcp) | Build (if needed) and serve the composition to an MCP host over stdio |
| [`run`](#run) | Execute a one-off command with the composition as the current database |

The common path is three commands: `init` to write the file, `build` to merge, then `mcp` (for an assistant) or `up` (for the HTTP API). `mcp` and `up` build on their own if the database is missing, so `build` is only needed to rebuild.

---

## init

Write a starter `axiomatize.yaml` in the current directory.

```bash
chaoscypher compose init [OPTIONS] [PACKAGES]...
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--name TEXT` | `-n` | the directory name | Composition name |
| `--config PATH` | `-c` | `axiomatize.yaml` | Where to write the file |
| `--force` | `-f` | off | Overwrite an existing file |

```bash
# A file with a commented example to edit
chaoscypher compose init

# List packages right away
chaoscypher compose init --name research ./research.ccx acme/eu-ai-act:1.2.0
```

---

## build

Resolve all packages referenced in `axiomatize.yaml`, download them from Lexicon (if not already cached), and merge them into a unified knowledge database. The database is written to the output directory defined in the config.

The composed database is a real Chaos Cypher database at `<output_dir>/databases/default`. Each package is imported into it through the same CCX importer that `graph package load` and `mount` use, so templates, entities, relationships, **sources, chunks and citations** all land, and every entity keeps the stable IRI it had in its package. After the import the composed knowledge is embedded and indexed for search (best-effort: without an embedding model it stays keyword-searchable). A `composition.json` in the output directory records what went in.

The first build also writes a `settings.yaml` into the output directory — the runtime settings the composed server and `compose run` tools read. It marks setup as complete (a composition needs no chat or extraction model) and makes the queue connection fail fast, since a local composition has no Valkey and Cortex would otherwise spend a minute retrying before starting. Edit it freely; later builds never overwrite it.

How packages meet in the composed database:

- **Templates are unified by name.** Two packages that both define a `Person` type produce one `Person` template.
- **Entities, relationships, sources and chunks keep their stable CCX IRIs.** Independently built packages never collide. The same package (or a fork that kept its IRIs) listed twice, or present in two compositions' inputs, is stored once — the later import wins on a collision, and the totals are counted from the database, not summed per package.
- `settings.merge_strategy` (`namespace` / `merge` / `replace`) is recorded in `composition.json` but does not change the result today: every strategy builds the database described above. Cross-package entity resolution (the same person appearing under different IRIs in two packages) is separate, planned work.

```bash
chaoscypher compose build [OPTIONS]
```

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |
| `--clean` | | off | Replace the composed database and manifest before building (the package cache, `settings.yaml`, pid record and server log in the output directory are kept) |

### Examples

**Build from the default config:**

```bash
chaoscypher compose build
```

```
Building composition: my-knowledge-base
  Config:    axiomatize.yaml
  Packages:  3
  Strategy:  merge
  Output:    ./output/my-knowledge-base

Resolving packages...

Built composition: my-knowledge-base
  Packages:  3
    • lexicon/science-fundamentals@1.2.0
    • lexicon/history-world@2.0.1
    • ./local/custom-entities
  Entities:      4,821
  Relationships: 9,304
  Database:      ./output/my-knowledge-base

Next steps:
  chaoscypher compose up -c axiomatize.yaml
```

**Use a custom config file:**

```bash
chaoscypher compose build --config research-compose.yaml
```

**Clean rebuild (delete previous output first):**

```bash
chaoscypher compose build --clean
```

:::note[Lexicon authentication]

If your packages require authentication, log in first:
```bash
chaoscypher lexicon login
```
Unauthenticated builds can still access public Lexicon packages.

:::

---

## up

Build the composition (if the database does not exist) and start the knowledge server: a Cortex API bound to `127.0.0.1` on the configured port, serving the composed database. With `--detach`, the server runs in the background and the command returns immediately.

A detached server's output goes to `server.log` in the output directory. `up --detach` waits until the server accepts a connection on its port (up to 60 seconds) and fails, quoting the log, if the process exits first or never binds — a port already in use is reported as a failure, not as a running server. A rebuild (`--build`, or `build --clean`) refuses to run while a recorded server is alive; stop it with `down` first.

```bash
chaoscypher compose up [OPTIONS]
```

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |
| `--port INT` | `-p` | from config | API port (overrides the port in `axiomatize.yaml`) |
| `--detach` | `-d` | off | Run the server in the background |
| `--build` | `-b` | off | Force a full rebuild before starting |

### Examples

**Start in the foreground (Ctrl+C to stop):**

```bash
chaoscypher compose up
```

```
Starting composition: my-knowledge-base
  Config:    axiomatize.yaml
  API port:  8081

Built composition: my-knowledge-base
  ...
```

The foreground server then blocks until stopped (Ctrl+C); on exit the CLI
prints `Composition stopped`.

**Start in the background:**

```bash
chaoscypher compose up --detach
```

```
Starting composition: my-knowledge-base
  Config:     axiomatize.yaml
  API port:   8081
  Detached mode: Yes

Composition started in background
  Server: http://localhost:8081

To stop:
  chaoscypher compose down -c axiomatize.yaml
```

The detached server's process id is written to `compose.pid` in the output directory;
[`down`](#down) uses it to stop the server from any later shell.

**Custom port:**

```bash
chaoscypher compose up --port 9000
```

**Force rebuild on every start:**

```bash
chaoscypher compose up --build
```

---

## down

Stop the server started with `compose up --detach`.

```bash
chaoscypher compose down [OPTIONS]
```

`compose up --detach` records the server's process id in `compose.pid` inside the
composition's output directory. `compose down` reads that record, sends the server a
graceful termination signal, waits up to `compose.process_terminate_timeout` seconds
(default 5), and force-kills it if it has not exited by then. The record is removed
either way.

```
Stopping composition: my-knowledge-base
Success: Composition stopped
```

If nothing is recorded — or the recorded process is already gone — the command says so
instead of claiming success:

```
Stopping composition: my-knowledge-base
No running composition server found (nothing recorded in ./output/my-knowledge-base/compose.pid)
```

A stale record left behind by a crash or a reboot is cleaned up on the next `down`, and
`compose up --detach` refuses to start a second server while a live one is recorded.

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |

---

## mcp

Serve the composition to an AI assistant over MCP stdio, building the composed database first if it does not exist.

```bash
chaoscypher compose mcp [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |
| `--mode {read,write}` | `-m` | from settings, usually `read` | MCP tool access mode |
| `--build` | `-b` | off | Force a rebuild before serving |

The command replaces itself with `chaoscypher mcp` pointed at the composed database, so stdio passes straight through and the host manages one process. Everything it prints before that goes to stderr. It is safe as a server command line: a built composition starts serving immediately, and a missing database is built on the first launch.

Claude Desktop:

```json
{
  "mcpServers": {
    "research-stack": {
      "command": "chaoscypher",
      "args": ["compose", "mcp", "-c", "/path/to/axiomatize.yaml"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add research-stack -- chaoscypher compose mcp -c /path/to/axiomatize.yaml
```

---

## run

Execute a one-off command with the composition's environment variables set. The composed database path and settings are injected as environment variables before the command is run. This is useful for running tests, analysis scripts, or any tool that needs access to the composed data.

```bash
chaoscypher compose run [OPTIONS] COMMAND...
```

### Injected environment variables

| Variable | Value |
|----------|-------|
| `CHAOSCYPHER_DATA_DIR` | `<output_dir>` — the composition's output directory, which holds the composed database |
| `CHAOSCYPHER_DATABASE` | `default` — the composed database's name inside that data dir |
| `CHAOSCYPHER_COMPOSE_NAME` | The composition's `name` from `axiomatize.yaml` |

These are the same variables `up` and `mcp` use, so any `chaoscypher` command run this way sees the composed database as its current database (`chaoscypher compose run chaoscypher source list`, for example). To serve the composition to an assistant, use [`mcp`](#mcp) rather than running `chaoscypher mcp` through `run`.

### Arguments

| Argument | Description |
|----------|-------------|
| `COMMAND` | Command to run (variadic — pass the full command and its arguments) |

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |

### Examples

**Run a Python script against the composed database:**

```bash
chaoscypher compose run python analyze.py
```

```
Running in composition: my-knowledge-base
  Command: python analyze.py

... (script output) ...
```

**Run a test suite:**

```bash
chaoscypher compose run pytest packages/*/tests/
```

**Use a custom config:**

```bash
chaoscypher compose run --config research-compose.yaml python export.py
```

The exit code of the run command matches the exit code of the subprocess.

---

## Typical Workflow

```bash
# 0. Write the config (or write axiomatize.yaml by hand)
chaoscypher compose init ./research.ccx acme/eu-ai-act

# 1. Build the composition from axiomatize.yaml
chaoscypher compose build

# 2. Start the server in the background
chaoscypher compose up --detach

# 3. Query the API or run tools while it's running
curl http://localhost:8081/api/v1/health
chaoscypher compose run python my_analysis.py

# 4. Stop when done
chaoscypher compose down
```

To give the composition to an AI assistant instead of (or as well as) the HTTP API:

```bash
chaoscypher compose mcp
```

For interactive development, run in the foreground instead:

```bash
# Build + serve in one step (foreground, Ctrl+C to stop)
chaoscypher compose up
```
