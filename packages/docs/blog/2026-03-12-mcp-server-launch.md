---
slug: mcp-server-launch
title: "Give Any AI Assistant Direct Access to Your Knowledge Graph with MCP"
authors: [denis]
tags: [feature-launch]
date: 2026-03-12
description: Chaos Cypher's MCP server lets Claude Desktop, Cursor, and other AI tools query and build your knowledge graph directly — no copy-paste required.
---

Your knowledge graph is stuck in a browser tab. You built something valuable -- a map of entities, relationships, and source documents that represents real understanding of a domain. But the moment you switch to Claude to write a report, or open Cursor to write code, or ask ChatGPT to help with analysis, that knowledge graph might as well not exist. You're back to copying text, pasting context, and manually cross-referencing. Two tools that should be working together are stuck in separate worlds.

<!-- truncate -->

Chaos Cypher now speaks MCP, which means any AI assistant that supports the protocol -- Claude Desktop, Claude Code, Cursor, Windsurf, and a growing list of others -- can directly query, search, traverse, and even write to your knowledge graph. No copy-paste. No context switching. Just ask.

This post walks through what that actually looks like, what's under the hood, and how to set it up in about two minutes.

## What Is MCP, and Why Should You Care?

MCP stands for Model Context Protocol. Anthropic released it as an open standard, and the simplest analogy is USB-C for AI tools. Before USB-C, every device had its own charger, its own cable, its own connector. MCP does the same thing for AI integrations: it defines one protocol that any AI host can use to talk to any tool server.

Instead of building a custom plugin for Claude, another for ChatGPT, another for Cursor, and another for every new AI tool that launches next month, you build one MCP server. Every compatible AI tool can use it immediately.

The adoption has been fast. Claude Desktop, Claude Code, Cursor, Windsurf, Cline, and Continue all support MCP today. The protocol handles tool discovery (the AI asks "what can you do?"), tool invocation (the AI calls a function with parameters), and result streaming. From the AI's perspective, your knowledge graph becomes just another set of capabilities it can use to answer questions.

From your perspective, it means you stop being the middleman between your data and your AI.

## What This Actually Looks Like

The best way to understand MCP is to see the before and after.

**Before MCP:** You have a knowledge graph with 200 entities extracted from research papers on gene therapy. You're writing a literature review in Claude. To reference your graph, you open Chaos Cypher in another tab, run a search, copy the results, paste them into Claude, ask your question, realize you need more context, go back to the graph, find related entities, copy those too, paste again. Repeat until frustrated.

**After MCP:** You tell Claude: "Search my knowledge graph for all entities related to CRISPR and find the shortest path to gene therapy applications." Claude calls `graphrag_search` to find relevant entities and document passages, then calls `find_shortest_path` to trace the relationship chain. You get a grounded answer with specific entities and relationships from your own research, in one turn.

Here are three scenarios that show the range of what's possible.

### Scenario 1: Research -- Connecting the Dots

You've been building a knowledge graph from papers on quantum computing and machine learning. You're deep in a writing session in Claude Desktop and want to understand where these two fields intersect in your collected research.

You ask: *"What are the connections between quantum computing and machine learning in my research? Show me the key entities and how they're related."*

Claude calls `search_nodes` to find nodes matching both topics, then `get_node_context` to pull the immediate neighborhood of the most central ones, including the edges that connect them and the source document chunks that support each relationship. You get back a structured map of how your research connects these fields -- not a generic internet answer, but one grounded in the specific papers you've indexed.

<!-- SCREENSHOT: Claude Desktop showing a research query about quantum computing and machine learning, with structured results showing entities, relationships, and source citations from the knowledge graph -->

### Scenario 2: Coding -- Your Project's Knowledge Base in Your Editor

You're in Cursor, working on a codebase that has an associated knowledge graph mapping its architecture -- services, APIs, data flows, dependencies. You need to understand how the authentication service connects to the billing pipeline.

You ask: *"Traverse from the Authentication Service node to anything related to billing. What's the path?"*

Cursor calls `resolve_node` to find the canonical node for "Authentication Service" (even if you didn't remember the exact label), then `traverse_path` to walk the graph two hops out, filtered to the relevant edge types. You see the chain: Authentication Service -> User Session -> Subscription Manager -> Billing Pipeline. Without leaving your editor.

<!-- SCREENSHOT: Cursor IDE showing an MCP query to traverse from an authentication node to billing-related nodes, with the path result displayed in the chat panel -->

### Scenario 3: Writing -- Summarize With Citations

You're drafting a report and need to summarize everything in your knowledge graph about a specific topic, with citations back to the original source documents.

You ask: *"Summarize all my sources related to climate policy in the European Union. Include which documents each claim comes from."*

Claude calls `get_summary_context` to retrieve and cluster document chunks relevant to the query. Because this tool returns the raw chunks with their source metadata rather than making an LLM call, Claude itself does the summarization -- giving you a synthesis grounded in your documents, with each claim traced back to a specific source.

<!-- SCREENSHOT: Claude Desktop showing a summarization query with results that include document citations and source references -->

## Under the Hood: 31 Tools, 7 Categories

Chaos Cypher exposes 31 tools through MCP, organized into seven categories. The design principle is that read operations are always safe and always available. Write operations are opt-in.

| Category | Read | Write | What It Does |
|---|---|---|---|
| **GraphRAG** | `graphrag_search` | -- | The flagship tool. Fuses Personalized PageRank over the knowledge graph with hybrid vector/keyword search. Finds answers that pure vector search misses because it follows relationships. |
| **Nodes** | `search_nodes`, `search_chunks`, `get_node`, `get_node_context`, `resolve_node` | `create_node`, `update_node`, `delete_node` | Full CRUD for graph nodes. Search by name, properties, or semantic similarity. Resolve aliases to canonical nodes. Get a node's full neighborhood with edges and supporting document chunks. |
| **Edges** | `list_edges`, `get_node_edges` | `create_edge` | Explore and create relationships. Filter by direction (incoming/outgoing), edge type, or connected node. |
| **Templates** | `list_templates`, `search_templates` | `create_template`, `delete_template` | Templates define the schema for nodes and edges. Search by name or description. Create new types on the fly. |
| **Analytics** | `analyze_graph_structure`, `find_shortest_path`, `find_similar_nodes`, `traverse_path` | -- | Structural analysis: community detection, PageRank centrality, degree distribution. Path finding between any two nodes. Semantic similarity via embeddings. Multi-hop traversal with depth and type filters. |
| **Documents** | `get_summary_context`, `get_document_status` | `add_document`, `confirm_extraction`, `wait_for_document`, `remove_document` | MCP-native document management. Queue files for background indexing and entity extraction. Confirm or override the auto-detected extraction domain for a source parked at `awaiting_confirmation`. Check processing status. Wait for completion. Retrieve clustered chunks for summarization. Full cascade delete. |
| **Extraction** | `get_extraction_tasks`, `get_extraction_chunks`, `get_extraction_progress` | `submit_chunk_extraction`, `finalize_extraction` | Client-driven entity extraction. The AI assistant reads chunks, extracts entities itself, and submits results back — no server LLM required. Track progress and finalize to commit to the knowledge graph. |

**Read/write mode split:** 19 tools are read-only and always available. 12 tools require write mode to be explicitly enabled. This is controlled by a single setting -- if you're not comfortable with an AI modifying your graph, just leave it in read mode. The AI can still search, traverse, and analyze everything.

**Two transport modes:** The MCP server runs in two ways depending on your setup:

- **stdio** -- For desktop AI tools like Claude Desktop and Cursor. The CLI starts a server that communicates over standard input/output. No network involved.
- **Streamable HTTP** -- For the Docker stack. The Cortex API exposes MCP at `/api/v1/mcp` using the Streamable HTTP transport, so any MCP client on the network can connect.

Both transports expose the same 31 tools with the same behavior. The only difference is how they're connected.

## Try It Yourself

Setup depends on how you run Chaos Cypher. Three paths, all quick.

### Path 1: CLI + Claude Desktop

If you have Chaos Cypher installed as a CLI tool, add this to your Claude Desktop configuration file (`claude_desktop_config.json`):

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

Restart Claude Desktop. You should see Chaos Cypher listed as an available MCP server in the tools panel.

<!-- SCREENSHOT: Claude Desktop settings showing the Chaos Cypher MCP server listed and connected in the MCP servers panel -->

### Path 2: CLI + Claude Code

One command:

```bash
claude mcp add chaoscypher -- chaoscypher mcp
```

That's it. Claude Code will discover Chaos Cypher's tools automatically on the next session.

### Path 3: CLI + Cursor

Add this to your Cursor MCP configuration (`.cursor/mcp.json` in your project, or the global settings):

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

<!-- SCREENSHOT: Cursor settings showing Chaos Cypher MCP server configured and connected -->

### Path 4: Docker Stack (Already Running)

If you run Chaos Cypher via `docker-compose`, the MCP endpoint is already live. Your Cortex API serves MCP at:

```
http://localhost:8080/api/v1/mcp
```

Any MCP client that supports the Streamable HTTP transport can connect directly. No additional configuration on the Chaos Cypher side.

### Configuring Access Mode

By default, MCP runs in read-only mode. To enable write tools (creating nodes, adding documents, etc.), update your `settings.yaml`:

```yaml
mcp:
  mode: write         # "read" (default) or "write" for full access
  auto_extract: true  # auto-extract entities from documents uploaded via MCP
```

Read mode exposes the 19 read tools. Write mode exposes all 31. The `auto_extract` flag controls whether documents uploaded via the `add_document` tool automatically go through entity extraction after indexing, or just get chunked and embedded for RAG search.

If you're using the CLI with a specific database, pass it as a flag:

```bash
chaoscypher mcp --database my-research
```

## Your Data Stays Local

This is worth stating explicitly: MCP doesn't send your knowledge graph data to any external service. The protocol is a local communication channel between the AI tool running on your machine and the Chaos Cypher server running on your machine (or your network, if you use Docker). When Claude calls `graphrag_search`, the query goes from Claude to your local MCP server, your server searches your local database, and the results go back to Claude. Your documents, entities, and relationships never leave your infrastructure.

The AI model itself runs wherever it runs -- that's between you and your provider. But the knowledge graph data stays entirely under your control. If you pair Chaos Cypher with a local model via Ollama, the entire pipeline is air-gapped. See our [local AI setup guide](/blog/local-ai-knowledge-graph) for the full walkthrough.

## What's Next

MCP support is the foundation for a broader vision: your knowledge graph as a persistent layer that any tool in your workflow can tap into. Here's what's on the roadmap:

- **Prompt templates** -- Pre-built MCP prompts for common patterns like "summarize this topic with citations" or "find contradictions in my sources," so you don't have to craft the right question every time.
- **Resource exposure** -- Making graph nodes and documents available as MCP resources, so AI tools can browse your knowledge graph like a file system.
- **Multi-database switching** -- Seamlessly switch between knowledge graphs within a single MCP session.

The flagship `graphrag_search` tool deserves its own explanation -- it's doing a lot more than keyword lookup. Read [how GraphRAG works](/blog/graphrag-enhanced-search) for the full deep-dive on the retrieval pipeline.

The MCP server ships with Chaos Cypher today. If you're already running it, you have it -- just configure your AI tool and go.

- **Documentation:** Full MCP setup guide and tool reference in the [docs](/docs/user-guide/mcp)
- **Source:** The MCP implementation lives in the `chaoscypher_core.mcp` package.
- **Issues:** Found a bug or have a feature request? [Open an issue](https://github.com/chaoscypherinc/chaoscypher/issues) or [start a discussion](https://github.com/chaoscypherinc/chaoscypher/discussions)

The gap between "having a knowledge graph" and "using a knowledge graph" has always been the friction of switching contexts. MCP closes that gap. Your knowledge graph is no longer a destination you visit -- it's a capability that follows you into whatever tool you're already working in.
