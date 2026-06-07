---
title: Utility Commands
description: CLI utility commands — chaoscypher health, diagnostics export, and shell completions for quick system checks and setup.
---

# Utility Commands

This page covers the smaller commands for system health, the comprehensive `doctor` diagnostic sweep, diagnostics bundle export, and shell completions.

---

## health

Check that all Chaos Cypher system components are reachable and configured. The health check runs in parallel and reports the status of Ollama, your configured models, embeddings, the search index, and the graph database.

```bash
chaoscypher health
```

### Example Output

```

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

**What is checked:**

| Check | How |
|-------|-----|
| **Ollama** | `GET /api/tags` at the configured URL |
| **Chat Model** | Presence in the installed models list from Ollama |
| **Extraction Model** | Presence in the installed models list from Ollama |
| **Embeddings** | Reads the configured embedding model name from `settings.yaml` (`embedding.model`) |
| **Search Index** | Full-text document count and vector count from the search repository |
| **Database** | Entity count and relationship count from the graph repository |

The health command exits with status 0 if all checks pass, or a non-zero status if any check fails.

---

## doctor

Run a comprehensive system diagnostic sweep. `doctor` is a **superset of `health`** — it runs every probe `health` runs, then adds the wider checks an operator wants when something is "off" but the cause is not yet known. Use `health` for the fast, scripted-friendly subset and `doctor` for a full pre-launch sweep. Like `health`, it takes no options.

```bash
chaoscypher doctor
```

### Additional checks (beyond `health`)

Alongside the Ollama, chat/extraction model, embeddings, search index, and database checks from `health`, `doctor` also reports:

| Check | How |
|-------|-----|
| **Lexicon Hub** | `HEAD` request to the configured hub URL (`lexicon.url`); a warning rather than a failure so an air-gapped box stays green |
| **Cortex API** | `GET /api/v1/health` against local candidates (`http://127.0.0.1:8000`, `http://127.0.0.1:8080`); informational, since the CLI works standalone |
| **Config File** | Presence and parse status of `settings.yaml` (a parse error is reported as a failure) |
| **Stale Files** | Leftover, no-longer-read files in the config directory (e.g. `cli.yaml`, `credentials.json`) flagged as safe to delete |

The same `+` / `x` / `!` status indicators are used. `doctor` exits with status 0 if all checks pass, or non-zero if any check fails.

---

## diagnostics

Export a ZIP bundle containing system information, database statistics, sanitized settings, and any available log files. Attach the bundle to bug reports to provide the development team with the context needed to reproduce and fix issues.

```bash
chaoscypher diagnostics [OPTIONS]
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--output PATH` | `-o` | Output path for the ZIP file (default: current directory) |

### Default Filename

When `--output` is not specified, the file is named with a UTC timestamp:

```
chaoscypher-diagnostics-20260413T142356.zip
```

### Examples

**Export to the current directory:**

```bash
chaoscypher diagnostics
```

```

  Chaos Cypher Diagnostics
  -----------------------------------
  + Database found: /home/user/.local/share/chaoscypher/databases/default/app.db
  + Log directory: 3 log files
  Collecting diagnostics...
  + Bundle saved: chaoscypher-diagnostics-20260413T142356.zip (48 KB)

  Attach this file to your bug report.

```

**Export to a specific path:**

```bash
chaoscypher diagnostics -o /tmp/debug.zip
```

### Bundle Contents

The ZIP file includes:

- **System information** — OS, Python version, installed package versions
- **Database statistics** — entity counts, edge counts, source counts (no content data)
- **Sanitized settings** — configuration with API keys and passwords redacted
- **Log files** — any `.log` files found in the CLI data directory

No document content, graph node data, or personal information is included.

---

## completions

Generate and install shell completion scripts for `bash`, `zsh`, or `fish`. Once installed, pressing Tab while typing a `chaoscypher` command shows available subcommands, options, and arguments.

```bash
chaoscypher completions SHELL [OPTIONS]
```

### Arguments

| Argument | Choices | Description |
|----------|---------|-------------|
| `SHELL` | `bash`, `zsh`, `fish` | Shell to generate completions for |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--install` | | Install completions to the shell config file automatically |
| `--uninstall` | | Remove completions from the shell config file |
| `--show-install` | `-i` | Show manual installation instructions |

### Examples

**Auto-install (recommended):**

```bash
chaoscypher completions bash --install
# Installs to ~/.bashrc, then run: source ~/.bashrc

chaoscypher completions zsh --install
# Installs to ~/.zshrc, then run: source ~/.zshrc

chaoscypher completions fish --install
# Installs to ~/.config/fish/completions/chaoscypher.fish
```

**Print the script to stdout (manual installation):**

```bash
# Append to your shell config manually
chaoscypher completions bash >> ~/.bashrc

# Or save to a dedicated file
chaoscypher completions zsh > ~/.zfunc/_chaoscypher
```

**Show detailed manual installation instructions:**

```bash
chaoscypher completions bash --show-install
chaoscypher completions zsh --show-install
chaoscypher completions fish --show-install
```

**Remove previously installed completions:**

```bash
chaoscypher completions bash --uninstall
chaoscypher completions zsh --uninstall
chaoscypher completions fish --uninstall
```

:::note[Re-running --install]

If completions are already installed, `--install` updates the existing block in place rather than appending a duplicate. It is safe to run multiple times.

:::
