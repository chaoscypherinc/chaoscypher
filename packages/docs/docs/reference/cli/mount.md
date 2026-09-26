---
title: Mount Command
description: Pull a knowledge package and serve it over MCP in one command with chaoscypher mount — build a knowledge graph once, then mount it into Claude Desktop, Claude Code, Cursor, or any MCP client.
---

# Mount

`chaoscypher mount` is the one-command form of *build it once, mount it anywhere*. It takes a knowledge package — a local `.ccx` file or a Lexicon Hub reference — imports it into a database of its own, makes it searchable, and then serves that database to the calling AI assistant over MCP stdio.

```bash
chaoscypher mount [OPTIONS] PACKAGE
```

`PACKAGE` is either a path to a `.ccx` file or a hub reference in `owner/name` form.

## What a mount does

1. **Resolve the package.** A local `.ccx` path is used as-is. A hub reference is downloaded into the packages directory (the same one `chaoscypher lexicon list` scans), so a second mount of the same version is a local read.
2. **Import it into its own database.** The database name is derived from the package (`acme/eu-ai-act` becomes `acme-eu-ai-act`; `research.ccx` becomes `research`) unless you pass `--database`. Templates, entities, relationships, **sources, chunks, and citations** are all imported, so answers on the far side of the mount still trace back to the text they came from.
3. **Index it for search.** Imported nodes and chunks are embedded with your configured embedding model and pushed into the vector index. If no embedding model is configured yet, the mount still works — keyword search stays available and the command tells you indexing was skipped.
4. **Serve it.** The command hands the database to the same stdio MCP server `chaoscypher mcp` starts, with all 21 read tools (and the write tools if you ask for `--mode write`).

A marker file, `mount.json`, is written into the database directory with the package's SHA-256 and the path of the archive it came from, once the import **and** the search indexing have succeeded. Running the same command again — which is exactly what an MCP host does every time it launches — finds the marker, skips the import, and goes straight to serving. For a hub package this also means no network call on relaunch: the cached archive recorded in the marker is served, so a laptop that mounted `acme/research` yesterday still serves it on a plane today. Pass `--refresh` to pull and import again (picking up a newer hub version); if the package bytes change, it re-imports on its own. CCX import is an upsert by stable IRI, so a re-import never duplicates. If indexing could not run (say, the embedding model failed to download), no marker is written and the next launch retries.

Everything the command prints goes to **stderr**. Stdout is the MCP JSON-RPC channel, and it stays clean from the first byte.

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--database NAME` | `-d` | Database to mount into (default: derived from the package name) |
| `--version VERSION` | `-v` | Hub package version to pull (default: latest) |
| `--mode {read,write}` | `-m` | MCP tool access mode (default: from settings, usually `read`) |
| `--no-serve` | | Import and index the package, then exit without starting the MCP server |
| `--refresh` | | Re-import even if this package is already mounted in the database |

## Examples

```bash
# Mount a package you built on another machine
chaoscypher mount ./research.ccx

# Mount a package from the Lexicon Hub
chaoscypher mount acme/eu-ai-act

# Pin a version and choose the database name
chaoscypher mount acme/eu-ai-act --version 1.2.0 --database aiact

# Import only — inspect it in the UI or serve it later with `chaoscypher mcp`
chaoscypher mount ./research.ccx --no-serve
```

Because a mount is idempotent, the natural place for the command is the MCP host's configuration:

### Claude Desktop

```json
{
  "mcpServers": {
    "research": {
      "command": "chaoscypher",
      "args": ["mount", "acme/research"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add research -- chaoscypher mount acme/research
```

### Cursor

```json
{
  "mcpServers": {
    "research": {
      "command": "chaoscypher",
      "args": ["mount", "acme/research"],
      "transportType": "stdio"
    }
  }
}
```

The first launch imports the package (a few seconds for a typical package; longer while the embedding model warms up). Every launch after that is as fast as `chaoscypher mcp`.

## Mount vs. load

| | `chaoscypher graph package load` | `chaoscypher mount` |
|---|---|---|
| Target | The database you are working in | A database of the package's own |
| What lands | Everything in the package, sources and citations included | The same |
| Then what | You keep building on it | It is served to an MCP host |
| Re-run | Re-imports (upsert by IRI, so nothing duplicates) | Skips when the package is unchanged |

Use `load` when you want a package's knowledge merged into your own graph. Use `mount` when you want an assistant to be able to ask it questions.

## Unmounting

A mounted package is an ordinary database. To unmount it, delete the database:

```bash
chaoscypher db delete acme-eu-ai-act
```

The downloaded `.ccx` stays in the packages directory; mount it again any time.

## No LLM required

Mounting does not need a chat or extraction model. The MCP host's own model asks the questions; Chaos Cypher answers them from the package. A fresh install can run `chaoscypher mount` before `chaoscypher setup`. Run `setup` (or configure an embedding model) when you want semantic search over the mounted knowledge rather than keyword search alone.

## See also

- [MCP Server CLI](mcp.md) — the server this command hands off to, and every tool it exposes
- [MCP user guide](../../user-guide/mcp.md) — client configuration and the client-driven extraction flow
- [Lexicon Hub](../../lexicon-hub/index.md) — finding and publishing packages
- [Import & Export](../../user-guide/import-export.md) — what a CCX package contains
