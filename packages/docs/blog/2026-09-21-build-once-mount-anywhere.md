---
slug: build-once-mount-anywhere
title: "Build It Once, Mount It Anywhere: One Knowledge Package, Any MCP Client"
authors: [denis]
tags: [graphrag, opensource, selfhosted, ai]
date: 2026-10-26
draft: false
description: A knowledge graph built on one machine, packaged as a .ccx file, and mounted into Claude Desktop, Claude Code, or any other MCP client on another machine with a single command — citations intact.
---

The local AI stack has a runtime, a model format, a model hub, and an agent protocol. It does not yet have a portable knowledge layer. The graph you spent an afternoon building is trapped in whichever app built it, and the assistant you actually work in cannot see it.

`chaoscypher mount` is the smallest thing we could ship that fixes that. One command pulls a knowledge package, imports it into a database of its own, indexes it, and serves it over MCP. Put that command in an assistant's config and the assistant has the knowledge every time it launches.

<!-- truncate -->

This post builds a package on one machine, moves it to another, and mounts the same file into two different assistants, with the same command ready for any other MCP client. Nothing about the package changes between them.

## What You'll Build

- A knowledge graph extracted from a set of documents, exported as a single `.ccx` file
- That file mounted into Claude Desktop and Claude Code on a second machine
- The same multi-hop question answered by both, each citing from the same evidence base

The package format is [CCX](/docs/reference/ccx-format), an open, documented format with a standalone reader. What travels is the graph, the source text, the chunks, and the citations. What does not travel is any dependency on the machine that built it.

## Step 1: Build the graph, then export it

On the build machine, run Chaos Cypher and add your documents. The [ten-minute quickstart](/blog/graphrag-ollama-10-minutes) covers this with Ollama; the [previous post](/blog/claude-builds-its-own-knowledge-graph) does it with Claude doing the extraction. Either way you end up with a graph in a database.

Export it:

```bash
chaoscypher graph package export --output research.ccx
```

The default export carries everything a mount needs: templates, entities, relationships, sources, chunks, and citations. The archive is deterministic and checksummed, so the same graph produces the same bytes and a tampered file fails validation on the other side.

Copy `research.ccx` to the second machine however you like, or publish it to the [Lexicon Hub](/docs/lexicon-hub) with `chaoscypher push research.ccx` and pull it by name.

<!-- optional screenshot: terminal output of the export command with the entity/relationship/source counts -->

## Step 2: Mount it

On the second machine, install the CLI:

```bash
pipx install chaoscypher-cli
```

Then mount the package once, without serving, to see what it does:

```bash
chaoscypher mount ./research.ccx --no-serve
```

This is the real output for a package built from fourteen of Chaos Cypher's own architecture pages:

```
Importing: archdocs.ccx
✓ Mounted ./archdocs.ccx → database archdocs: 177 entities, 205 relationships, 14 sources, 178 citations
Indexing for search with the local embedding model (Qwen/Qwen3-Embedding-0.6B); the first use downloads it.
✓ Indexed 14 sources and 177 nodes for search

Serve it with: chaoscypher mcp --database archdocs
```

Three things happened. The package went into a database named after it, so it does not mix with anything else on the machine. The sources and citations came along, not just the entities, which is what keeps answers traceable. And a `mount.json` marker was written with the package's SHA-256, so the next mount of the same file skips the import.

That last point is the whole design. An MCP host runs its server command on every launch. `mount` is safe to run on every launch.

There is no `chaoscypher setup` in this step either. Mounting needs no chat or extraction model; the assistant's own model asks the questions. With vector search on (the default) the mount is indexed for semantic search with the configured embedding model (by default a local one, downloaded on first use); if vector search is off, it is keyword-searchable only.

## Step 3: Mount it into your assistants

The command is the same each time. Only the config file changes.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "research": {
      "command": "chaoscypher",
      "args": ["mount", "/home/me/research.ccx"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add research -- chaoscypher mount /home/me/research.ccx
```

**Any other MCP client.** The [MCP user guide](/docs/user-guide/mcp) has the Cursor configuration alongside Claude's; for any client that launches a stdio MCP server, point its server list at the same `chaoscypher mount` command. The knowledge on the other end is identical.

If the package came from the Hub, replace the path with the reference:

```json
"args": ["mount", "acme/research"]
```

<!-- optional screenshot: Claude Desktop's MCP server list showing "research" connected with 25 tools -->

## Step 4: Ask both the same question

Pick a question that needs two hops, so the graph has something to do:

> Which components depend on the queue client, and which ADR governs changing it?

Each assistant calls `graphrag_search`. The tool embeds the question, seeds a Personalized PageRank walk from the closest entities, and fuses two evidence lists: the chunks the top entities were cited from, and the chunks a hybrid keyword-and-vector search would return. The response carries the graph context and the chunks with aliases the assistant uses for inline citations.

Because the citations traveled inside the package, every assistant cites from the same sources and chunks the graph was built from. Each host's model picks which chunks to cite; the evidence it picks from is identical. Two assistants, one set of receipts.

<!-- optional screenshot: side-by-side of Claude Desktop and Claude Code answering the same question with chunk citations -->

**In plain English:** you built the knowledge once. Every assistant that mounts it gets the same facts and the same evidence, without re-reading a single document.

## Why a mount and not an import

Chaos Cypher already had `chaoscypher graph package load`, which merges a package into the database you are working in. That is the right tool when you want the knowledge in *your* graph, alongside everything else you have built.

A mount is different in two ways. It goes into its own database, so it cannot collide with your work and can be thrown away with `chaoscypher db delete`. And it is idempotent, so it belongs in a config file rather than a runbook.

The metaphor is deliberate. You mount a volume; you do not copy its contents into your home directory.

## What it does not do yet

A mount is read-only by default. Pass `--mode write` if you want the assistant to be able to add to the mounted database, but the package on disk does not change when it does. There is no `unmount` command; deleting the database is the unmount. And the default export leaves embedding vectors out, so with vector search on, the mounting machine re-embeds the package with whatever embedding model is configured there; that happens once, on the first launch, and takes a moment. With vector search off, nothing is embedded and the mount is keyword-only. Pass `--embeddings` at export time if both machines use the same model and you want to skip it.

## Next steps

- The [Mount command reference](/docs/reference/cli/mount) lists every option and the mount-versus-load comparison.
- Packages compose. An `axiomatize.yaml` can merge several packages into one served database with `chaoscypher compose`. That is a post of its own.
- If you publish a package to the Hub, the same `mount` line works for everyone who pulls it.
