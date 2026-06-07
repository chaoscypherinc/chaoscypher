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
| [`build`](#build) | Compile `axiomatize.yaml` into a runtime database |
| [`up`](#up) | Build (if needed) and run the composition server |
| [`down`](#down) | Stop a running composition server |
| [`run`](#run) | Execute a one-off command in the composition context |

---

## build

Resolve all packages referenced in `axiomatize.yaml`, download them from Lexicon (if not already cached), and merge them into a unified knowledge database. The database is written to the output directory defined in the config.

```bash
chaoscypher compose build [OPTIONS]
```

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |
| `--clean` | | off | Delete the output directory before building |

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
  Database:      ./output/my-knowledge-base/app.db

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
chaoscypher login
```
Unauthenticated builds can still access public Lexicon packages.

:::

---

## up

Build the composition (if the database does not exist) and start the knowledge server. With `--detach`, the server runs in the background and the command returns immediately.

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

Server running at http://localhost:8081
Press Ctrl+C to stop
```

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

Stop the server that was started with `compose up --detach`.

```bash
chaoscypher compose down [OPTIONS]
```

### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | `axiomatize.yaml` | Path to composition config file |

### Examples

**Stop the default composition:**

```bash
chaoscypher compose down
```

```
Stopping composition: my-knowledge-base
Composition stopped
```

**Stop a named composition:**

```bash
chaoscypher compose down --config research-compose.yaml
```

---

## run

Execute a one-off command with the composition's environment variables set. The composed database path and settings are injected as environment variables before the command is run. This is useful for running tests, analysis scripts, or any tool that needs access to the composed data.

```bash
chaoscypher compose run [OPTIONS] COMMAND...
```

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
# 1. Build the composition from axiomatize.yaml
chaoscypher compose build

# 2. Start the server in the background
chaoscypher compose up --detach

# 3. Query the API or run tools while it's running
curl http://localhost:8081/api/v1/stats
chaoscypher compose run python my_analysis.py

# 4. Stop when done
chaoscypher compose down
```

For interactive development, run in the foreground instead:

```bash
# Build + serve in one step (foreground, Ctrl+C to stop)
chaoscypher compose up
```
