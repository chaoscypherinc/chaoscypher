---
title: CLI Chat
description: Start a RAG-powered AI chat from the command line — chaoscypher chat uses your knowledge graph and indexed documents to answer questions, matching the web UI chat.
---

# Chat

The `chat` command starts an AI conversation using your knowledge graph and indexed documents. The LLM automatically calls tools to search nodes, retrieve relationships, query document chunks, and analyze graph structure -- matching the capabilities of the web UI chat.

```bash
chaoscypher chat --help
```

## Quick Message

Send a single message and get a response:

```bash
chaoscypher chat "What are the key findings about climate change?"
```

**Sample output:**

```
  → search_chunks(query=key findings about climate change)
  → search_nodes(query=climate change)

Based on the knowledge graph, there are several key findings about climate change
documented across your sources:

1. Global temperatures have risen by 1.1°C since pre-industrial levels, with
   acceleration observed in the last two decades.

   │ The IPCC report confirms a 1.1°C increase in global mean surface
   │ temperature relative to 1850-1900 baselines.
   └─ ipcc_report_2023.pdf

2. Arctic sea ice extent has declined by approximately 13% per decade since
   satellite observations began in 1979.

According to the graph, these findings are connected to 12 source documents
across 3 research domains.
```

## Interactive Mode

Omit the message to enter interactive chat:

```bash
chaoscypher chat
```

**Sample session:**

```
╭─────────────── Chaos Cypher Chat ───────────────╮
│ Interactive Chat Mode                           │
│                                                 │
│ Type your messages and press Enter to send.     │
│ Type 'exit' or 'quit' to end the chat.          │
│ Type 'clear' to start a new conversation.       │
╰─────────────────────────────────────────────────╯

You: How many nodes are in the graph?

Assistant
  → analyze_graph_structure()

The knowledge graph contains 247 nodes across 8 template types:

- 89 Character nodes
- 52 Location nodes
- 41 Event nodes
- 28 Organization nodes
- 19 Concept nodes
- 10 Document nodes
- 5 Theme nodes
- 3 Timeline nodes

There are 612 edges connecting these nodes.

You: Who is the most connected character?

Assistant
  → analyze_graph_structure(template_ids=['character'])
  → get_node_context(node_id=node_a1b2c3d4)

The most connected character is Pierre Bezukhov with 34 relationships.
He is connected to 18 other characters, 7 locations, and 9 events.
His key relationships include:

- Married to Natasha Rostova
- Friend of Andrei Bolkonsky
- Member of the Freemasons

You: exit
Goodbye!
```

## Tool Calls

The LLM uses tools automatically to search and retrieve information. Tool calls are displayed in real-time as they execute. For complex queries, the LLM chains multiple tool calls before delivering a final answer.

**Example showing multi-step tool calls:**

```bash
chaoscypher chat "How are Pierre and Andrei connected?"
```

```
  → search_nodes(query=Pierre)
  → search_nodes(query=Andrei)
  → get_node_edges(node_id=node_a1b2c3d4)
  → traverse_path(source_id=node_a1b2c3d4, target_id=node_e5f6g7h8)

Based on the knowledge graph, Pierre Bezukhov and Andrei Bolkonsky are
connected through multiple relationships:

1. Direct friendship — they share a "friend_of" relationship established
   early in the narrative.

2. Family connection — Pierre later marries Natasha Rostova, who was
   previously engaged to Andrei.

3. Shared membership — both are linked to philosophical and social
   circles within the graph.

The shortest path between them is a direct edge (friend_of), but there
are 4 indirect paths through shared connections.
```

### Available Tools

The chat command has access to the following tools:

| Tool | Purpose |
|------|---------|
| `graphrag_search` | GraphRAG retrieval -- community-aware search that combines graph structure with document context |
| `search_chunks` | Find document text -- passages, statements, and definitions from source documents |
| `search_nodes` | Find entities in the graph -- node IDs, labels, properties, and types |
| `search_templates` | Find relevant templates by concept (e.g., "people" finds "character" template) |
| `get_node` | Get detailed information about a specific node |
| `get_node_context` | Get a node with its relationships and optionally document chunks |
| `get_node_edges` | Get all relationships for a node |
| `traverse_path` | Find paths between two nodes in the graph |
| `resolve_node` | Resolve nicknames, aliases, or descriptions to the canonical entity node |
| `create_node` | Create a new node in the graph |
| `update_node` | Update a node's properties |
| `create_edge` | Create a relationship between two nodes |
| `analyze_graph_structure` | Get graph statistics and structure analysis |
| `summarize` | Summarize large amounts of document content |

## Options

| Option | Description |
|--------|-------------|
| `--context, -c TEXT` | Node or document ID to use as context |
| `--source, -s TEXT` | Scope to specific source ID (repeatable) |
| `--tag, -t TEXT` | Scope to all sources with this tag (repeatable) |
| `--system, -S TEXT` | Custom system prompt |
| `--database, -d TEXT` | Database name (default: `default`) |

## Scoping

Restrict the AI's context to specific sources or tags. When scoped, the LLM only uses information from the specified sources.

```bash
# Scope to a specific document
chaoscypher chat -s "source-id-123" "Summarize this document"

# Scope to multiple sources
chaoscypher chat -s "source-1" -s "source-2" "Compare these papers"

# Scope by tag
chaoscypher chat -t "research" "What are the common themes?"

# Combine source and tag scoping
chaoscypher chat -s "source-1" -t "notes" "Find connections"
```

In interactive mode, use the `/scope` command to view the current source scope:

```
You: /scope

Current scope:
  - Research Paper on Climate Change.pdf
  - IPCC Summary Report 2023.pdf
```

## Context

Provide a node or document ID to include as additional context in the conversation:

```bash
# Use a node as context
chaoscypher chat -c "node_abc123" "What else is related to this?"

# Use a document as context
chaoscypher chat -c "doc-456" "Summarize this document"
```

When context is provided, the first few chunks or node properties are included in the system prompt so the LLM has immediate awareness of the referenced entity.

## Interactive Commands

| Command | Description |
|---------|-------------|
| `help` | Show available commands |
| `clear` | Clear conversation history and start fresh |
| `/scope` | Show current source scope (only when scoped) |
| `exit`, `quit`, `q` | End the session |

## How It Works

1. Connects to the knowledge graph and search indexes
2. Sends your message to the LLM with tool-calling capabilities
3. The LLM searches nodes, retrieves relationships, and queries the knowledge graph as needed
4. Streams the response with entity references and citations
5. Supports multi-step tool calling (up to 10 iterations) for complex queries

Responses are streamed in real-time, so you see the answer as it is generated. Entity references like node names are rendered in bold, and document citations are displayed as inline blockquotes with source attribution.
