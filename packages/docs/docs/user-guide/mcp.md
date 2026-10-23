---
id: mcp
title: MCP Server
description: Connect Claude Desktop, Cursor, Windsurf, and other MCP-compatible AI assistants to your Chaos Cypher knowledge graph for direct querying, search, and graph building.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# MCP Server

Chaos Cypher includes a built-in [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets any MCP-compatible AI assistant — Claude Desktop, ChatGPT, Cursor, Windsurf, and others — query, search, and build your knowledge graph directly.

## What is MCP?

MCP is an open protocol that standardizes how AI assistants connect to external tools. Instead of copy-pasting data into chat windows, MCP lets your AI assistant call tools directly — searching your graph, traversing relationships, or adding documents — all through a structured API.

## Setup

Chaos Cypher provides two transport modes for MCP:

### CLI Mode (stdio)

Use the `chaoscypher mcp` command to connect AI assistants that support stdio transport (Claude Desktop, Cursor, etc.). No Docker required — the CLI connects directly to your local database.

<Tabs>
<TabItem value="claude-desktop" label="Claude Desktop">


Add to your Claude Desktop configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "chaoscypher": {
      "command": "chaoscypher",
      "args": ["mcp"]
    }
  }
}
```

</TabItem>
<TabItem value="claude-code" label="Claude Code">


```bash
claude mcp add chaoscypher -- chaoscypher mcp
```

</TabItem>
<TabItem value="cursor" label="Cursor">


Add to your Cursor MCP settings:

```json
{
  "mcpServers": {
    "chaoscypher": {
      "command": "chaoscypher",
      "args": ["mcp"],
      "transportType": "stdio"
    }
  }
}
```

</TabItem>
</Tabs>


To use a specific database:

```json
{
  "mcpServers": {
    "chaoscypher": {
      "command": "chaoscypher",
      "args": ["mcp", "--database", "my-project"]
    }
  }
}
```

### Mount a package

If the knowledge you want the assistant to use already exists as a `.ccx` package — one you exported on another machine, or one from the [Lexicon Hub](../lexicon-hub/index.md) — you don't need to import it by hand first. `chaoscypher mount` pulls the package, imports it into a database of its own (sources and citations included), indexes it, and serves it over stdio in one step:

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

The import runs once; every later launch finds the package unchanged and goes straight to serving. A local file works the same way (`"args": ["mount", "/path/to/research.ccx"]`). See the [Mount command reference](../reference/cli/mount.md) for the options and the difference between mounting and loading a package.

### HTTP Mode (Streamable HTTP)

When running the full Docker stack, the MCP server is available at the Cortex API endpoint. This is useful for web-based clients or remote access.

```
POST http://localhost:8080/api/v1/mcp
```

The HTTP transport supports Server-Sent Events (SSE) for streaming responses and respects all existing Cortex authentication settings.

:::note[API port]

`http://localhost:8080` is the API port for the multi-container development stack. The all-in-one container (the primary install) serves the API on port **80** instead — use `http://localhost/api/v1/mcp` there.

:::

## Configuration

MCP settings are configured in [`settings.yaml`](../getting-started/configuration.md):

```yaml
mcp:
  mode: read                              # "read" (default) or "write"
  auto_extract: false                     # Run server-side entity extraction after indexing (opt-in)
  confirmation_required_default: true     # Domain-confirmation gate default for MCP uploads
  max_extraction_payload_bytes: 10485760  # Per-call cap on submit_chunk_extraction payloads
  extraction_rate_limit_per_minute: 100   # submit_chunk_extraction calls per source per minute
  completed_history_limit: 20             # Completed uploads kept in the in-memory status history
  profile: full                           # "full" (every tool) or "memory" (the five memory tools)
  tools: []                               # Allowlist of tool names; empty serves the whole profile
```

| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | `read` | Tool access level. `read` exposes 25 tools: search/query plus the self-benchmark tools. `write` exposes all 44 tools including create, update, delete, site import, and client-driven extraction operations. |
| `auto_extract` | `false` | When `true`, the server runs entity extraction after indexing uploaded documents. When `false` (default), the MCP client drives extraction itself using `submit_chunk_extraction` and `finalize_extraction`. |
| `confirmation_required_default` | `true` | Server-wide default for the [domain-confirmation gate](#document-processing-via-mcp). When `true`, an upload with an auto-detected domain parks at `awaiting_confirmation` until `confirm_extraction` is called; a per-call `auto_confirm: true` on `add_document` overrides it for a single upload. |
| `max_extraction_payload_bytes` | `10485760` | Maximum combined UTF-8 size (10 MiB) of `entities_text` + `relationships_text` accepted per `submit_chunk_extraction` call. Larger submissions are rejected with `PAYLOAD_TOO_LARGE`. |
| `extraction_rate_limit_per_minute` | `100` | Maximum `submit_chunk_extraction` calls per source per 60-second sliding window. |
| `completed_history_limit` | `20` | Completed MCP uploads retained in the in-memory status history before the oldest are evicted. |
| `profile` | `full` | The served tool profile: `full` is every tool; `memory` is the memory tools alone (see [Profiles](#profiles)). |
| `tools` | `[]` | An allowlist of tool names narrowing the profile; empty serves the whole profile. |
| `memory.result_chars` | `1600` | The most characters a memory tool answers with. |
| `memory.wait_seconds` | `120` | How long `remember` waits for the linking before answering "Saved, still linking." |
| `memory.default_by` | `user` | Who a memory is by when the call does not say. |

:::tip[Safe by default]

MCP starts in **read-only mode**. Your AI assistant can search and explore your knowledge graph but cannot modify it. Switch to `write` mode when you want to allow node/edge creation and document uploads.

:::

## Profiles

A profile is the set of tools the server lists. `full` (the default) lists every tool of the mode. `memory` lists the memory tools alone: `remember`, `recall`, `forget`, `list_memories` and `digest`, the short list an agent memory client needs so that a small model's tool list stays short. Pick it on the command line or in the settings:

```bash
chaoscypher mcp --profile memory --mode write
```

```yaml
mcp:
  profile: memory
  tools: []        # or a narrower allowlist, for example [recall, list_memories]
```

An allowlist narrows the profile further; a name that is not a registered tool is a configuration error, named at start. The [Memory](memory.md) page describes the tools and what they answer.

## Available Tools

### Read Tools (25)

These tools are available in both modes:

| Tool | Description |
|------|-------------|
| `graphrag_search` | Graph-enhanced RAG — fuses knowledge graph traversal with vector search for multi-hop questions |
| `search_nodes` | Hybrid search for graph nodes by name, properties, or semantic similarity |
| `search_chunks` | Hybrid search for document chunks (vector + keyword) |
| `search_templates` | Search templates by name or description |
| `get_node` | Get a single node by ID or search query |
| `get_node_context` | Get a node with its 1-hop edges and optionally related chunks |
| `get_node_edges` | Get edges for a node with direction and type filters |
| `resolve_node` | Resolve an alias or nickname to its canonical node |
| `list_nodes` | List nodes without a query: all of them or one template's (by id or name), paged by `skip` and `limit` |
| `recall` | What is known about something, as plain text in the graph's shape: an entity card, a grouped list or a relation chain, each fact tagged with who said it and when (see [Memory](memory.md)) |
| `list_memories` | The memories, newest first, one line each |
| `digest` | What changed since a date: memories and facts added, replaced and forgotten |
| `list_edges` | List edges with optional node filter |
| `list_templates` | List available templates with optional type filter |
| `analyze_graph_structure` | Graph statistics, community detection, PageRank, degree distribution |
| `find_shortest_path` | BFS shortest path between two nodes |
| `find_similar_nodes` | Vector similarity search on node embeddings |
| `traverse_path` | Multi-hop BFS traversal with depth and type filters |
| `get_summary_context` | Retrieve and cluster document chunks for summarization |
| `get_document_status` | Check status of queued, in-progress, and completed document uploads |
| `start_benchmark` | Start an extraction instruction-probe run answered by the calling model itself (see [Benchmark any MCP client](../reference/extraction-benchmark.md#benchmark-any-mcp-client)) |
| `get_benchmark_task` | Get the next probe prompt (entity or relationship stage) of a benchmark run |
| `submit_benchmark_output` | Submit the model's answer for one stage; returns the probe's verdict when it completes |
| `get_benchmark_progress` | Submitted, passed and pending probes of a benchmark run |
| `finish_benchmark` | Score a benchmark run and write its results file |

### Write Tools (19)

These tools are only available when `mcp.mode` is set to `write`:

| Tool | Description |
|------|-------------|
| `remember` | Remember one sentence or note as a dated memory linked to the people, places and things the graph knows (see [Memory](memory.md)) |
| `forget` | Forget one memory: a preview of the matches with handles, then the confirmed removal of the memory and the facts nothing else states; what it had replaced reopens |
| `create_node` | Create a new graph node with template, label, and properties |
| `update_node` | Update a node's label or properties |
| `delete_node` | Delete a node and its connected edges |
| `create_edge` | Create a relationship between two nodes |
| `create_template` | Create a node or edge template with schema |
| `delete_template` | Delete a template from the schema |
| `add_document` | Queue a file for background indexing and optional entity extraction |
| `add_site` | Import every page of a documentation site from its address, in the background (see [Importing a docs site](#importing-a-docs-site)) |
| `refresh_site` | Check an imported site for updates: only changed pages are re-imported, new pages added, unlisted ones marked gone; the pages it leaves out stay out (`exclude` replaces them) |
| `wait_for_document` | Wait for a document to finish processing, polling until it reaches the target status. Cannot see a source parked at `awaiting_confirmation` — see [Document Processing via MCP](#document-processing-via-mcp) |
| `remove_document` | Delete a source document and its derived data; an entity or relationship another source also attests is kept and rebuilt from that source |
| `confirm_extraction` | Confirm (or override) the auto-detected extraction domain for a source parked at `awaiting_confirmation` and start extraction |
| `get_extraction_tasks` | Start a client-driven extraction and return its planning metadata: chunk count, chunk indices, extraction instructions, and existing templates (no chunk text) |
| `get_extraction_chunks` | Fetch the source text of specific chunk groups, with numbered sentences and token estimates, for the client to extract from |
| `get_extraction_progress` | Check extraction submission progress for a source (submitted and missing chunk indices, and whether it is ready to finalize) |
| `submit_chunk_extraction` | Submit extracted entities and relationships for a specific document chunk |
| `finalize_extraction` | Finalize the extraction process for a source, committing results to the knowledge graph |

## Usage Examples

Once configured, you can interact with your knowledge graph naturally through your AI assistant:

- *"Find all connections between quantum computing and machine learning in my knowledge graph"*
- *"What documents mention CRISPR gene editing?"*
- *"Show me the shortest path between Alice and the Research Department"*
- *"Search for entities related to climate change"*
- *"Add a new Person node for Dr. Smith with role: Lead Researcher"* (write mode)
- *"Upload this research paper to my knowledge graph"* (write mode)

## Document Processing via MCP

When using write mode, the `add_document` tool queues files for background processing:

1. The file is added to an in-memory processing queue
2. Documents are processed one at a time (chunking, embedding, indexing)
3. If extraction is requested with an auto-detected domain, the source parks at `awaiting_confirmation` after indexing. Call `confirm_extraction` (optionally overriding the domain) to start extraction, or pass `auto_confirm: true` to `add_document` to skip the gate for that upload
4. Once the domain is confirmed (or the gate skipped), extraction proceeds — server-side if `auto_extract` is enabled, otherwise client-driven (below)
5. Use `get_document_status` to check progress of queued and in-progress uploads

:::warning[Parked sources are invisible to wait_for_document]

`wait_for_document` only sees in-memory processor jobs — it cannot see a source parked at `awaiting_confirmation` (that state lives in the database). For a parked upload, poll `get_document_status` and call `confirm_extraction` to proceed.

:::

The same domain-confirmation gate applies to REST API uploads: `POST /api/v1/sources` accepts an `auto_confirm` form field that also defaults to `false`, so a default API upload without a forced domain parks at `awaiting_confirmation` after indexing. See [Sources](sources.md) for the confirmation workflow.

### Importing a docs site

`add_site(url)` imports every page of a documentation site, each as its own document. It finds the pages (the site's `llms.txt`, its sitemap, or its links), answers at once in plain words, then fetches and indexes each page for search in the background:

```
add_site(url="https://docs.example.com")
→ Importing https://docs.example.com/ (the site's title: "Example Docs"): 71 pages found by llms.txt. The pages are fetched first, then indexed for search one by one; refresh_site checks the site for updates later.

refresh_site(site="Example Docs")
→ Checking https://docs.example.com/ (the site's title: "Example Docs") for updates.

refresh_site(site="Example Docs", exclude=["https://docs.example.com/blog"])
→ Checking https://docs.example.com/ (the site's title: "Example Docs") for updates. 1 page is left out: https://docs.example.com/blog. refresh_site with exclude changes that list.
```

`refresh_site` takes the site's address or title, and optionally `exclude`: the page addresses to leave out from now on, replacing the site's list (`[]` brings every page back). Without it, the pages the site leaves out (unticked at import, passed as `exclude` before, or deleted from the site) stay out, and the answer names a few of them; a shorter list's answer names the pages it brings back. An entry that is not a full address of one of the site's pages is refused, naming it. A refused address, an address with no pages, a site already imported (any spelling of the same address), and a check while one is running each answer in one sentence. A site's title is the site's own text: answers label and quote it, cleaned to one short line, so an agent never reads it as part of the answer. The pages are indexed for search; extract a page's entities with the client-driven extraction tools below. How pages are found and the politeness rules: [Import a Docs Site](site-import.md).

### Client-Driven Extraction

MCP defaults to **client-driven extraction** — the AI assistant performs entity extraction itself using `submit_chunk_extraction` and `finalize_extraction`, without requiring a server-side LLM. This means:

- **No server LLM needed** for extraction — the connected AI assistant does the work
- The assistant reads chunks via `get_extraction_chunks`, extracts entities from each, and submits results back
- After all chunks are processed, `finalize_extraction` commits results to the knowledge graph
- Set `auto_extract: false` in settings to use this workflow exclusively

The domain-confirmation gate applies here too: if extraction is requested with an auto-detected domain, the source parks at `awaiting_confirmation` after indexing. Call `confirm_extraction` (optionally overriding the domain) to start extraction, or pass `auto_confirm: true` to `add_document` to skip the gate for that upload.

Two operational limits apply to client-driven extraction (both [configurable](#configuration)):

- Each `submit_chunk_extraction` call may carry at most `mcp.max_extraction_payload_bytes` (default 10 MiB) of combined `entities_text` + `relationships_text`; larger submissions are rejected with `PAYLOAD_TOO_LARGE`.
- Each source accepts at most `mcp.extraction_rate_limit_per_minute` (default 100) `submit_chunk_extraction` calls per 60-second sliding window.

This is useful when the AI assistant has better extraction capabilities than the local LLM, or when no local LLM is configured.

## Maintenance Mode

If the database is blocked on a pending schema upgrade — for example, a destructive migration is pending with auto-apply disabled, or the pre-upgrade backup failed — the MCP server starts in a degraded **maintenance mode** instead of failing with an opaque JSON-RPC error. In this mode the normal toolset is not advertised; only two tools are available:

| Tool | Description |
|------|-------------|
| `upgrade_status` | Reports the pending migrations blocking normal use (each with its risk tier and a plain-language description), the most recent pre-upgrade backup path, and the last-applied revisions. Call this first. |
| `apply_upgrade` | Applies the pending migrations. Safe and data-changing migrations apply automatically; destructive migrations (which may drop columns or force re-extraction) require `confirm_destructive: true`. A verified backup is taken before anything is applied. |

Any other tool call returns a clear error explaining that the database needs a one-time upgrade. After a successful `apply_upgrade`, reconnect the MCP server to restore the normal toolset. This is the MCP equivalent of the web maintenance page — see [Upgrading](../getting-started/upgrading.md).

## Privacy

All data stays local. The MCP server gives your AI assistant **tool access** to query your knowledge graph — your documents and graph data are never sent to external services beyond what your chosen LLM provider requires for chat.

## See also

- [CLI reference: MCP Server](../reference/cli/mcp.md) — `chaoscypher mcp` command options and AI assistant configuration snippets
- [API reference: Grounding (MCP)](../reference/api/grounding.md) — HTTP endpoints for node search, edge traversal, and neighbor discovery used by the HTTP MCP transport
