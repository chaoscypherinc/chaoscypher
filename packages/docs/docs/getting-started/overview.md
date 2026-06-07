---
id: overview
title: Overview
description: Chaos Cypher is an AI-powered knowledge graph engine — upload documents, extract entities and relationships, and query your knowledge with RAG chat.
---

# Overview

Chaos Cypher is an AI-powered knowledge graph engine that transforms unstructured documents into structured, queryable knowledge. Upload PDFs, Word documents, web pages, or plain text — Chaos Cypher automatically indexes them for search, extracts entities and relationships into a knowledge graph, and lets you ask questions with retrieval-augmented generation (RAG).

## Who is it for?

- **Researchers** managing large document collections who need to find connections across sources
- **Knowledge workers** building structured knowledge bases from unstructured content
- **Developers** wanting a self-hosted, multi-LLM knowledge platform with a clean API
- **Teams** needing a shared knowledge graph with document processing pipelines

## Key Capabilities

**Document Processing** — Upload files or URLs. Chaos Cypher chunks, embeds, and indexes content automatically. Optionally extract entities and relationships into a knowledge graph using AI.

**Knowledge Graph** — Explore extracted entities and relationships through an interactive graph canvas. Create, edit, and connect nodes manually or through AI extraction. Apply templates for consistent entity types.

**AI Chat with RAG** — Ask questions about your documents. Responses are grounded in your actual content with citations. Scope conversations to specific sources or search the entire database.

**Semantic Search** — Find information using keyword, semantic (vector), or hybrid search. GraphRAG fuses graph traversal with vector search for multi-hop questions. Re-ranking ensures the most relevant results surface first.

**MCP Server** — Built-in [Model Context Protocol](https://modelcontextprotocol.io/) server lets AI assistants like Claude Desktop, Cursor, and ChatGPT query and build your knowledge graph directly.

**Multi-LLM Support** — Works with Ollama (fully local), OpenAI, Anthropic, and Gemini. Switch providers with a single configuration change. Embeddings run locally on the CPU — no API keys needed.

## Quick Code Preview

```python
from chaoscypher_core import ChaosCypher

# Extract entities from any document — one line
result = ChaosCypher.extract_sync("paper.pdf")
print(f"{len(result.entities)} entities found")
```

```python
from chaoscypher_core import Engine

# Build a knowledge graph with persistent storage
with Engine("./data/databases/demo") as engine:
    alice = engine.add_node("Person", "Alice", properties={"role": "Engineer"})
    bob = engine.add_node("Person", "Bob", properties={"role": "Designer"})
    engine.add_edge("knows", alice, bob)
    print(f"Graph: {engine.get_stats().nodes} nodes")
```

[Developer Quick Start](../developer-guide/quickstart.md)

## Neural Architecture

Chaos Cypher is organized using a brain-inspired metaphor:

- **Core** — The brain. Framework-agnostic business logic using hexagonal architecture.
- **Cortex** — The processing center. FastAPI backend with vertical slice architecture.
- **Neuron** — Worker cells. Background task processing for LLM calls and operations.
- **Interface** — The interaction layer. React + TypeScript web UI.

This separation means each component can be used independently — the CLI uses Core directly without the web stack, and workers process jobs without the API server.

[Install Chaos Cypher](installation.md)
