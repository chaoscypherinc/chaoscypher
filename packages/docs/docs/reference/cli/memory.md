---
title: Memory CLI
description: Export a scope of memories as a .ccx memory pack with chaoscypher memory export, and link the memories not linked yet with chaoscypher memory relink.
---

# Memory

Memory packs: the memories of a scope, with their facts and provenance, as a `.ccx` package another database loads. And the relink: linking the memories whose linking could not finish when they were remembered.

## export

### Usage

```bash
chaoscypher memory export [OPTIONS] OUTPUT
```

### Options

| Option | Description |
|--------|-------------|
| `OUTPUT` | The `.ccx` file to write |
| `--scope, -s` | Scope to export (default: every memory) |
| `--title, -t` | Display title for the pack (default: `memory:SCOPE`, or `memory:all` without `--scope`) |
| `--database, -d` | Database name (default: `default`) |
| `--help` | Show help message |

### Examples

```bash
# Export the "work" scope
chaoscypher memory export --scope work work.ccx

# Load it into another database
chaoscypher graph package load work.ccx --database other
```

An imported memory keeps who said it, its scope and its date; it can be recalled, replaced and forgotten like one remembered there. A package whose key signature does not match its bytes is refused (tampering); the signature is not a statement of who made the pack. See [Memory](../../user-guide/memory.md).

## relink

A memory whose linking could not finish (the model was busy, failing or not set up, or the server stopped) is kept as a note and marked with why. A write-mode server links it on its next start; `relink` links it now, oldest first, retrying a memory that keeps failing at once instead of waiting out its backoff.

### Usage

```bash
chaoscypher memory relink [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--database, -d` | Database name (default: `default`) |
| `--limit, -n` | Link at most N memories, oldest first (default: every one) |
| `--dry-run` | Print how many memories are not linked yet, by state, and link nothing |
| `--include-empty` | Also link again the memories that linked with no facts (after a model change, say) |
| `--help` | Show help message |

### Output

One line of counts, never a memory's text:

```text
Linked 3, linked empty 1, still pending 1 (model failed), no model 0, linking elsewhere 0.
```

`linking elsewhere` counts the memories another process was linking. `--dry-run` prints the counts it would start from:

```text
Not linked yet: 2 (0 pending, 0 linking now, 0 interrupted, 1 no model, 1 failed). Linked empty: 1.
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Nothing is left unlinked (always, with `--dry-run`) |
| `1` | An error (printed as `Error: ...`) |
| `3` | Some memory is still not linked: its model failed, none is set up, or another process is linking it |
| `130`, `143` | Stopped by Ctrl-C (130) or SIGTERM (143); the memory it was linking is given back and links on the next run |

### Examples

```bash
# What is not linked yet, and why
chaoscypher memory relink --dry-run

# Link everything now
chaoscypher memory relink

# Link from a timer of your own instead of at server start
# (with mcp.memory.relink_on_start: false in the settings), ten at a time
chaoscypher memory relink --limit 10
```

See [When linking has to wait](../../user-guide/memory.md#when-linking-has-to-wait).
