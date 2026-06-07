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

### HTTP Mode (Streamable HTTP)

When running the full Docker stack, the MCP server is available at the Cortex API endpoint. This is useful for web-based clients or remote access.

```
POST http://localhost:8080/api/v1/mcp
```

The HTTP transport supports Server-Sent Events (SSE) for streaming responses and respects all existing Cortex authentication settings.

## Configuration

MCP settings are configured in [`settings.yaml`](../getting-started/configuration.md):

```yaml
mcp:
  mode: read              # "read" (default) or "write"
  auto_extract: false     # Run server-side entity extraction after indexing (opt-in)
```

| Setting | Default | Description |
|---------|---------|-------------|
| `mode` | `read` | Tool access level. `read` exposes 19 search/query tools. `write` exposes all 31 tools including create, update, and delete operations. |
| `auto_extract` | `false` | When `true`, the server runs entity extraction after indexing uploaded documents. When `false` (default), the MCP client drives extraction itself using `submit_chunk_extraction` and `finalize_extraction`. |

:::tip[Safe by default]

MCP starts in **read-only mode**. Your AI assistant can search and explore your knowledge graph but cannot modify it. Switch to `write` mode when you want to allow node/edge creation and document uploads.

:::

## Available Tools

### Read Tools (19)

These tools are always available regardless of mode:

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
| `list_edges` | List edges with optional node filter |
| `list_templates` | List available templates with optional type filter |
| `analyze_graph_structure` | Graph statistics, community detection, PageRank, degree distribution |
| `find_shortest_path` | BFS shortest path between two nodes |
| `find_similar_nodes` | Vector similarity search on node embeddings |
| `traverse_path` | Multi-hop BFS traversal with depth and type filters |
| `get_summary_context` | Retrieve and cluster document chunks for summarization |
| `get_document_status` | Check status of queued, in-progress, and completed document uploads |
| `get_extraction_tasks` | List extraction tasks for a source document with status and entity counts |
| `get_extraction_chunks` | Retrieve extracted entities and relationships from individual chunks |
| `get_extraction_progress` | Check overall extraction progress for a source (completed chunks, total, percentage) |

### Write Tools (12)

These tools are only available when `mcp.mode` is set to `write`:

| Tool | Description |
|------|-------------|
| `create_node` | Create a new graph node with template, label, and properties |
| `update_node` | Update a node's label or properties |
| `delete_node` | Delete a node and its connected edges |
| `create_edge` | Create a relationship between two nodes |
| `create_template` | Create a node or edge template with schema |
| `delete_template` | Delete a template from the schema |
| `add_document` | Queue a file for background indexing and optional entity extraction |
| `wait_for_document` | Wait for a document to finish processing, polling until it reaches the target status |
| `remove_document` | Delete a source document and all its derived data |
| `confirm_extraction` | Confirm (or override) the auto-detected extraction domain for a source parked at `awaiting_confirmation` and start extraction |
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
3. If `auto_extract` is enabled, entity extraction runs automatically after indexing
4. Use `get_document_status` to check progress of queued and in-progress uploads

### Client-Driven Extraction

MCP defaults to **client-driven extraction** — the AI assistant performs entity extraction itself using `submit_chunk_extraction` and `finalize_extraction`, without requiring a server-side LLM. This means:

- **No server LLM needed** for extraction — the connected AI assistant does the work
- The assistant reads chunks via `get_extraction_chunks`, extracts entities from each, and submits results back
- After all chunks are processed, `finalize_extraction` commits results to the knowledge graph
- Set `auto_extract: false` in settings to use this workflow exclusively

This is useful when the AI assistant has better extraction capabilities than the local LLM, or when no local LLM is configured.

## Privacy

All data stays local. The MCP server gives your AI assistant **tool access** to query your knowledge graph — your documents and graph data are never sent to external services beyond what your chosen LLM provider requires for chat.

## See also

- [CLI reference: MCP Server](../reference/cli/mcp.md) — `chaoscypher mcp` command options and AI assistant configuration snippets
- [API reference: Grounding (MCP)](../reference/api/grounding.md) — HTTP endpoints for node search, edge traversal, and neighbor discovery used by the HTTP MCP transport
