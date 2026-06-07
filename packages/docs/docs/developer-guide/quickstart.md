---
id: quickstart
title: Quick Start
description: Get productive with chaoscypher-core in minutes — LLM chat, entity extraction, knowledge graph operations, and document processing using the simplest API surface.
---

# Quick Start

This guide gets you productive with `chaoscypher-core` in minutes. It covers the most common tasks -- chatting with an LLM, extracting entities, building a knowledge graph, and processing documents -- using the simplest API surface first.

## One-Liner LLM Chat

The `ChaosCypher` facade gives you zero-boilerplate access to LLM chat, embeddings, extraction, and search. No database, no setup.

```python
from chaoscypher_core import ChaosCypher

response = ChaosCypher.chat_sync("What is a knowledge graph?")
print(response.content)
```

That's it. Defaults to Ollama on `localhost:11434`. To switch providers:

```python
ChaosCypher.configure(provider="openai", api_key="sk-...")
response = ChaosCypher.chat_sync("What is a knowledge graph?")
```

:::tip[Provider auto-detection]

If you skip the `provider` argument, Chaos Cypher detects it from the API key prefix: `sk-ant-` maps to Anthropic, `sk-` to OpenAI. You can also set environment variables (`CHAOSCYPHER_LLM_PROVIDER`, `OPENAI_API_KEY`, etc.) and skip `configure()` entirely.

:::

## Extract Entities from a Document

Run LLM-powered entity extraction on any file -- no database, no graph setup.

```python
from chaoscypher_core import ChaosCypher

result = ChaosCypher.extract_sync("paper.pdf")
print(result.model_dump_json(indent=2))
```

`ChaosCypher` auto-detects the file type, chunks the text, and extracts entities and relationships. The result is an `ExtractionResult` model with attribute access and `.model_dump_json()` for JSON output.

To extract from raw text instead of a file, use the `text=` keyword argument to skip file detection:

```python
result = ChaosCypher.extract_sync(text="Albert Einstein developed the theory of relativity...")
print(result.entities)
```

:::tip[Chunking without extraction]

Inspect intermediate chunking results before extraction:

```python
chunks = ChaosCypher.chunk_sync("paper.pdf")
print(f"{chunks.total_small_chunks} chunks in {chunks.total_groups} groups")

# Or from raw text
chunks = ChaosCypher.chunk_sync(text="Long document content here...")
```

Or just load file text directly (no LLM needed):

```python
text = ChaosCypher.load("paper.pdf")
```

:::

## Generate Embeddings

Single text or batch -- both sync and async:

```python
from chaoscypher_core import ChaosCypher

# Single embedding
result = ChaosCypher.embed_sync("quantum entanglement")
print(f"Dimensions: {len(result.embedding)}")

# Batch embedding (always returns BatchEmbedResult)
batch = ChaosCypher.embed_batch_sync(["text one", "text two", "text three"])
print(f"{len(batch.embeddings)} embeddings generated")
```

`embed()` accepts a string and returns `EmbedResult`. `embed_batch()` accepts a list and always returns `BatchEmbedResult` -- no runtime type checking needed.

## Build a Knowledge Graph

For persistent storage with a full graph database, use `Engine`. It wires up SQLite storage, repositories, and services in one call.

### Recommended: Inline Configuration

Pass `provider=` and `api_key=` directly to Engine -- no need to construct settings objects:

```python
from chaoscypher_core import Engine

with Engine(database="demo", provider="openai", api_key="sk-...") as engine:
    alice = engine.add_node("Person", "Alice", properties={"role": "Engineer"})
    bob = engine.add_node("Person", "Bob", properties={"role": "Designer"})
    engine.add_edge("knows", alice, bob)

    stats = engine.get_stats()
    print(f"Graph: {stats.nodes} nodes, {stats.edges} edges")
```

`add_node` and `add_edge` automatically create templates if they don't exist. The database and tables are created automatically on first use.

Engine also inherits `ChaosCypher.configure()` settings, so you can configure once at program start:

```python
ChaosCypher.configure(provider="openai", api_key="sk-...")

# All Engine instances inherit the configured provider
with Engine(database="demo") as engine:
    alice = engine.add_node("Person", "Alice")
```

### Query the Graph

```python
# List all nodes (returns PaginatedResult with .data and .total)
result = engine.list_nodes()
for node in result.data:
    print(f"{node.label} (template: {node.template_id})")

# Get database statistics
stats = engine.get_stats()
print(f"Nodes: {stats.nodes}, Edges: {stats.edges}")
```

### Search (Sync and Async)

Engine provides both sync and async search:

```python
from chaoscypher_core import Engine

# Sync (scripts, notebooks)
with Engine(database="demo") as engine:
    results = engine.search_sync("quantum entanglement")
    for r in results:
        print(f"{r.label} ({r.score:.2f})")
```

```python
# Async
import asyncio

async def main():
    async with Engine(database="demo") as engine:
        results = await engine.search("quantum entanglement", mode="semantic")
        for r in results:
            print(f"{r.label} ({r.score:.2f})")

asyncio.run(main())
```

Search supports three modes: `"hybrid"` (default), `"semantic"`, and `"keyword"`.

### Chat and Embed through Engine

Engine exposes LLM methods that use the engine's configured provider:

```python
# Sync
with Engine(database="demo", provider="openai", api_key="sk-...") as engine:
    response = engine.chat_sync("Summarize this graph")
    print(response.content)

    embedding = engine.embed_sync("quantum entanglement")
    print(f"Dimensions: {len(embedding.embedding)}")

    batch = engine.batch_embed_sync(["text one", "text two"])
    print(f"{len(batch.embeddings)} embeddings")
```

```python
# Async
async with Engine(database="demo") as engine:
    response = await engine.chat("Summarize this graph")
    embedding = await engine.embed("quantum entanglement")
    batch = await engine.batch_embed(["text one", "text two"])
```

### Clean Up

Always close the engine to release database connections. The recommended pattern is the context manager (`with Engine(...)`) shown above.

---

## Process Documents End-to-End

### Single Document (Facade)

The simplest path from a file on disk to a fully populated knowledge graph:

```python
from chaoscypher_core import ChaosCypher

# Sync
result = ChaosCypher.add_document_sync("paper.pdf", database="demo")
print(f"Created {len(result.nodes)} nodes, {len(result.edges)} edges")
```

```python
# Async
result = await ChaosCypher.add_document("paper.pdf", database="demo")
```

This handles loading, chunking, indexing, extraction, and commit in a single call.

### Batch Documents (Facade)

Process multiple files at once with `add_documents`:

```python
from chaoscypher_core import ChaosCypher

# Sync -- glob pattern or explicit list
results = ChaosCypher.add_documents_sync("papers/*.pdf", database="demo")
print(f"Processed {len(results)} documents")

# Or pass a list
results = ChaosCypher.add_documents_sync(
    ["doc1.pdf", "doc2.pdf", "notes.txt"],
    database="demo",
)
```

```python
# Async
results = await ChaosCypher.add_documents(["doc1.pdf", "doc2.pdf"], database="demo")
```

### Single Document (Engine)

For repeated operations or more control, use Engine directly:

```python
from chaoscypher_core import Engine

# Sync
with Engine(database="demo") as engine:
    result = engine.add_document_sync("paper.pdf")
    print(f"Created {len(result.nodes)} nodes, {len(result.edges)} edges")
```

```python
# Async
async with Engine(database="demo") as engine:
    result = await engine.add_document("paper.pdf")
```

### Batch Documents (Engine)

```python
# Sync
with Engine(database="demo") as engine:
    results = engine.add_documents_sync(["doc1.pdf", "doc2.pdf"])
    print(f"Processed {len(results)} documents")
```

```python
# Async
async with Engine(database="demo") as engine:
    results = await engine.add_documents("papers/*.pdf")
```

### Process Text Already in Memory

If you have text from a web scrape or user input (no file on disk), use `process_document`:

```python
# Sync
with Engine(database="demo") as engine:
    result = engine.process_document_sync(text, filename="scraped_article.txt")
    print(f"Created {len(result.nodes)} nodes")
```

```python
# Async
async with Engine(database="demo") as engine:
    result = await engine.process_document(text, filename="scraped_article.txt")
```

### Tracking Progress

Use the `on_progress` callback to monitor long-running operations:

```python
from chaoscypher_core import Engine

def on_progress(stage, result):
    """Called after each pipeline stage completes."""
    print(f"Completed: {stage}")  # "chunking", "indexing", "extraction"

with Engine(database="demo") as engine:
    result = engine.add_document_sync("paper.pdf", on_progress=on_progress)
    print(f"Nodes created: {len(result.nodes)}")
```

### Error Handling

The SDK raises specific exceptions for different failure modes:

```python
from chaoscypher_core import Engine, NotFoundError, ValidationError, OperationError

with Engine(database="demo") as engine:
    try:
        node = engine.get_node("nonexistent-id")
    except NotFoundError:
        print("Node does not exist")
    except ValidationError as e:
        print(f"Invalid input: {e}")
    except OperationError as e:
        print(f"Operation failed: {e}")
```

| Exception | When raised |
|-----------|-------------|
| `NotFoundError` | Entity (node, edge, template, source) not found by ID |
| `ValidationError` | Invalid input data (bad types, missing required fields) |
| `OperationError` | Operation failed (LLM unavailable, storage error) |
| `ConflictError` | Duplicate entity or constraint violation |

---

## Configuration

### Quick: `configure()` or Kwargs

The simplest way to set up a provider -- works for both the `ChaosCypher` facade and `Engine`:

```python
from chaoscypher_core import ChaosCypher, Engine

# Option 1: Global configure (affects all subsequent calls)
ChaosCypher.configure(provider="openai", api_key="sk-...")
result = ChaosCypher.extract_sync("paper.pdf")  # uses OpenAI

# Option 2: Per-engine inline kwargs
with Engine(database="demo", provider="anthropic", api_key="sk-ant-...") as engine:
    response = engine.chat_sync("Hello")
```

You can also configure embedding models and chunking parameters:

```python
ChaosCypher.configure(
    provider="openai",
    api_key="sk-...",
    embedding_model="BAAI/bge-large-en-v1.5",
    chunk_size=512,
)
```

### Environment Variables (Zero Code)

```bash
export CHAOSCYPHER_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
```

### Advanced: `EngineSettings` for Full Control

For complete control over all settings, pass an `EngineSettings` instance:

```python
from chaoscypher_core import Engine, EngineSettings, LLMSettings

settings = EngineSettings(
    current_database="mydb",
    llm=LLMSettings(
        chat_provider="openai",
        openai_api_key="sk-...",
        openai_chat_model="gpt-4.1",
    ),
)

engine = Engine(database="mydb", settings=settings)
```

These same settings work for the standalone extraction pipeline -- just pass `settings` to `ChunkingService(settings)` instead of using the defaults.

---

## Advanced: Explicit Template Control

When you need to define property schemas, descriptions, or other template metadata, use the explicit `create_template` / `create_node` / `create_edge` methods instead of `add_node` / `add_edge`:

```python
from chaoscypher_core import Engine, TemplateCreate, NodeCreate, EdgeCreate

with Engine(database="demo") as engine:
    person = engine.create_template(
        TemplateCreate(name="Person", template_type="node")
    )
    alice = engine.create_node(
        NodeCreate(template_id=person.id, label="Alice")
    )
    bob = engine.create_node(
        NodeCreate(template_id=person.id, label="Bob")
    )

    knows = engine.create_template(
        TemplateCreate(name="knows", template_type="edge")
    )
    engine.create_edge(
        EdgeCreate(
            template_id=knows.id,
            source_node_id=alice.id,
            target_node_id=bob.id,
            label="knows",
        )
    )
```

<details>
<summary>Two-step chunking (inspect before persisting)</summary>

If you need to inspect chunks before storing them (e.g., for debugging or custom filtering), access the chunking service directly:

```python
from chaoscypher_core import Engine

async with Engine(database="mydb") as engine:
    # Step 1: Chunk the text (not yet persisted)
    chunks = await engine.chunking_service.create_chunks(text)
    print(f"{chunks.total_small_chunks} chunks in {chunks.total_groups} groups")

    # Step 2: Persist after inspection
    engine.chunking_service.store_chunks(chunks)
```

For most use cases, `engine.chunk_document(text)` or `engine.add_document("file.pdf")` handles both steps automatically.

</details>

---

## API Reference

### Chaos Cypher Facade

All methods are static -- no instantiation needed.

| Method | Sync variant | Description |
|--------|-------------|-------------|
| `configure()` | -- | Set global provider/API key |
| `reset()` | -- | Clear cached settings |
| `extract()` | `extract_sync()` | Extract entities from file or `text=` |
| `chat()` | `chat_sync()` | LLM chat |
| `embed()` | `embed_sync()` | Single embedding |
| `embed_batch()` | `embed_batch_sync()` | Batch embeddings (always `BatchEmbedResult`) |
| `search()` | `search_sync()` | Search a knowledge graph database |
| `chunk()` | `chunk_sync()` | Chunk file or `text=` for RAG |
| `add_document()` | `add_document_sync()` | Full file-to-graph pipeline |
| `add_documents()` | `add_documents_sync()` | Batch file-to-graph pipeline |
| `load()` | -- | Load file text (sync, no LLM) |

### Engine

Accepts `provider=`, `api_key=`, and all `configure()` aliases as constructor kwargs. Inherits `ChaosCypher.configure()` settings when no explicit config is given.

| Method | Sync variant | Description |
|--------|-------------|-------------|
| `add_node()` | -- | Create node (auto-creates template) |
| `add_edge()` | -- | Create edge (auto-creates template) |
| `search()` | `search_sync()` | Hybrid/semantic/keyword search |
| `chat()` | `chat_sync()` | LLM chat through engine provider |
| `embed()` | `embed_sync()` | Single embedding |
| `batch_embed()` | `batch_embed_sync()` | Batch embeddings |
| `add_document()` | `add_document_sync()` | Full file-to-graph pipeline |
| `add_documents()` | `add_documents_sync()` | Batch file-to-graph pipeline |
| `process_document()` | `process_document_sync()` | Process text (no file) to graph |
| `chunk_document()` | -- | Chunk and store text |
| `commit()` | -- | Extract + commit stored chunks |
| `index_source()` | -- | Generate embeddings for chunks |
| `get_stats()` | -- | Database statistics |
| `check_health()` | -- | LLM provider health check |

## Next Steps

- [Core Concepts](core-concepts.md) -- understand ports, adapters, and the dict-not-entities rule
- [Services](services.md) -- explore the full service catalog
- [Storage Adapters](storage-adapters.md) -- learn about the SQLite adapter internals
