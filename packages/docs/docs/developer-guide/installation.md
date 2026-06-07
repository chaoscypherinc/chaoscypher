---
id: installation
title: Installation
description: Install chaoscypher-core as a standalone Python library from PyPI for use in your own applications. Requires Python 3.14+ and SQLite 3.35+.
---

# Installation

This guide covers installing `chaoscypher-core` as a standalone Python library for use in your own applications.

## Requirements

- **Python 3.14** or later
- **SQLite 3.35+** (included with Python on most platforms)

## Install from PyPI

```bash
pip install chaoscypher-core
```

This installs the core library with all production dependencies, including:

- **SQLModel** and **Pydantic** for data modeling
- **sqlite-vec** for vector similarity search
- **LangChain** ecosystem for LLM orchestration and workflows
- **LLM provider SDKs** (Ollama, OpenAI, Anthropic, Gemini)
- **Document processing** (PDF, OCR, audio transcription)
- **Content normalization** (encoding fixes, deduplication)

### Development Extras

For testing, linting, and type checking:

```bash
pip install chaoscypher-core[dev]
```

The `[dev]` extra adds pytest, ruff, mypy, pre-commit, pip-audit, and related tooling.

## What's Included

`chaoscypher-core` is the framework-agnostic brain of Chaos Cypher. It provides:

| Capability | Description |
|-----------|-------------|
| **Graph CRUD** | Create, read, update, delete nodes, edges, and templates |
| **Storage adapters** | SQLite adapter with full protocol coverage |
| **Entity extraction** | LLM-powered extraction from documents |
| **RAG pipeline** | Chunking, embedding, and vector search |
| **Workflow engine** | LangGraph-based workflow execution |
| **Graph analytics** | Centrality, clustering, path analysis |
| **Search** | Keyword, vector, semantic, and hybrid search |
| **Quality scoring** | Entity and relationship quality metrics |

## What Requires the Full Stack

Some features need the complete Chaos Cypher platform (Cortex API + Neuron workers + Valkey + Docker):

- **Background job queue** -- async processing via Valkey
- **Web UI** -- React interface for graph exploration
- **REST API** -- FastAPI endpoints with authentication
- **Multi-user access** -- concurrent access with proper locking
- **Queue monitoring** -- real-time job status dashboard

See the [Getting Started guide](../getting-started/installation.md) for deploying the full platform.

## Verify the Installation

```python
import chaoscypher_core
print(chaoscypher_core.__version__)
```

## Next Steps

- [Quick Start](quickstart.md) -- build a knowledge graph in under 20 lines
- [Core Concepts](core-concepts.md) -- understand the architecture
