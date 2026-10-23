---
title: Memory CLI
description: Export a scope of memories as a .ccx memory pack with chaoscypher memory export; load it elsewhere with chaoscypher package load.
---

# Memory

Memory packs: the memories of a scope, with their facts and provenance, as a `.ccx` package another database loads.

## Usage

```bash
chaoscypher memory export [OPTIONS] OUTPUT
```

## Options

| Option | Description |
|--------|-------------|
| `OUTPUT` | The `.ccx` file to write |
| `--scope, -s` | Scope to export (default: every memory) |
| `--title` | Display title for the pack (default: `memory:SCOPE`) |
| `--database, -d` | Database name (default: `default`) |
| `--help` | Show help message |

## Examples

```bash
# Export the "work" scope
chaoscypher memory export --scope work work.ccx

# Load it into another database
chaoscypher package load work.ccx --database other
```

An imported memory keeps who said it, its scope and its date; it can be recalled, replaced and forgotten like one remembered there. A package whose key signature does not match its bytes is refused (tampering); the signature is not a statement of who made the pack. See [Memory](../../user-guide/memory.md).
