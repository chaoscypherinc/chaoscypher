---
id: services
title: Services
description: Domain services in chaoscypher-core for RAG, graph management, entity extraction, and source commit — with guidance on when to use Engine methods vs direct service calls.
---

# Services

The `chaoscypher_core` package provides domain services for RAG, graph management, entity extraction, and source commit operations. Each service follows a consistent pattern: accept protocol-typed repositories in the constructor, return plain dicts from public methods.

:::tip[Which API should I use?]

- **One-off extraction or chat?** Use `ChaosCypher.extract_sync("file.pdf")` — no setup needed.
- **Building or querying a graph?** Use `Engine` convenience methods — `engine.add_node()`, `engine.search()`, `engine.add_document()`.
- **Writing a custom storage backend?** Use the underlying services directly (see collapsible sections below).

The Engine convenience methods return Pydantic models with attribute access. The underlying service methods return dicts. Stick with Engine methods unless you're building a custom adapter.

:::

:::warning[Data Type Boundaries]

Storage protocols return `dict[str, Any]` -- always use dict access (`data["key"]`) on storage results. See the [Core Concepts](core-concepts.md) page for the full boundary rules.

:::

## Using Services via Engine

For most use cases, access services through `Engine`. It wires all dependencies and offers convenience methods that return domain models with clean attribute access:

```python
from chaoscypher_core import Engine, NodeCreate, TemplateCreate

with Engine("./data/databases/demo") as engine:
    # Convenience methods return models (attribute access: .id, .label)
    person = engine.create_template(TemplateCreate(name="Person", template_type="node"))
    alice = engine.create_node(NodeCreate(template_id=person.id, label="Alice"))
    print(f"Created node {alice.id}: {alice.label}")

    # Service methods return dicts (for advanced/framework use)
    result = engine.node_service.list_nodes()
    print(f"Total nodes: {result['pagination']['total']}")
```

The sections below document both the Engine convenience methods (recommended for scripts, CLI, notebooks) and the underlying service constructors (for advanced use cases like custom storage backends or framework integration).

## Service Access Cheat Sheet

| Task | Engine method | Returns |
|------|--------------|---------|
| Quick-add a node | `engine.add_node("Person", "Alice")` | `Node` |
| Quick-add an edge | `engine.add_edge("knows", alice, bob)` | `Edge` |
| Load a file into the graph | `await engine.add_document("paper.pdf")` | `ProcessingResult` |
| Load multiple files | `await engine.add_documents("papers/*.pdf")` | `list[ProcessingResult]` |
| Process text in memory | `await engine.process_document(text)` | `ProcessingResult` |
| Chunk text for RAG | `await engine.chunk_document(text)` | `ChunkingResult` |
| Extract + commit chunks | `await engine.commit(source_id)` | `ProcessingResult` |
| Search the graph | `await engine.search("quantum")` | `list[EngineSearchResult]` |
| Index a source for RAG | `await engine.index_source("src_001")` | `IndexingResult` |
| Rebuild all indexes | `engine.rebuild_indexes()` | `RebuildResult` |
| Create a node (explicit) | `engine.create_node(NodeCreate(...))` | `Node` |
| Create an edge (explicit) | `engine.create_edge(EdgeCreate(...))` | `Edge` |
| List nodes | `engine.list_nodes()` | `PaginatedResult` |
| Get database stats | `engine.get_stats()` | `DatabaseStats` |
| Check LLM health | `await engine.check_health()` | `HealthReport` |
| Chat completion | `await engine.chat(messages)` | `LLMChatResponse` |
| Single embedding | `await engine.embed(text)` | `EmbedResult` |
| Batch embeddings | `await engine.batch_embed(texts)` | `BatchEmbedResult` |

All return values are Pydantic models with attribute access and `.model_dump()` / `.model_dump_json()`.

## Document Loading

### Loaders

Auto-detects file type from extension and returns plain text. Wraps the `LoaderRegistry` for a simple one-call interface.

**Import:**

```python
from chaoscypher_core import Loaders
```

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `load_text` | `(file_path, settings=None) -> str` | Load any supported document and return text |

Supports PDF, text, CSV, JSON, audio, video, image, and archive formats.

**Example:**

```python
from chaoscypher_core import Loaders

# Auto-detect file type from extension
text = Loaders.load_text("paper.pdf")
print(text[:200])
```

---

## RAG Services

### ChunkingService

Splits document text into hierarchical chunks for RAG retrieval and entity extraction.

**Import:**

```python
from chaoscypher_core import ChunkingService
```

:::tip[Engine shortcut]

If using `Engine`, access this service via `engine.chunking_service`.

:::

**Constructor:**

```python
ChunkingService(
    settings: EngineSettings | None = None,       # Optional; defaults to EngineSettings() (Ollama on localhost)
    repository: ChunkingProtocol | None = None,   # Optional chunk storage implementation
)
```

When `settings` is `None`, `ChunkingService` uses default `EngineSettings` (Ollama on `localhost:11434`). When `repository` is `None`, `create_chunks()` and `process()` work as pure processors -- useful for standalone extraction without a database. `store_chunks()`, `get_small_chunks()`, and `get_hierarchical_groups()` require a repository.

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `process` | `async (text, analysis_depth="full", file_info=None) -> ExtractionResult` | Chunk and extract entities in one call |
| `create_chunks` | `async (full_text, source_id=None, analysis_depth="full", store=None) -> ChunksResult` | Create hierarchical chunks (`source_id` auto-generates if omitted; `store` auto-persists when repository available) |
| `store_chunks` | `(chunks_result: ChunksResult, database_name=None) -> None` | Persist chunks to storage (accepts `create_chunks()` result) |
| `get_small_chunks` | `(source_id) -> list[dict]` | Retrieve stored small chunks (requires repository) |
| `get_hierarchical_groups` | `(source_id, analysis_depth) -> list[dict]` | Retrieve chunk groups for extraction (requires repository) |

**Example:**

```python
from chaoscypher_core import ChunkingService

# Standalone -- chunk + extract in one call (no database needed)
result = await ChunkingService().process("Your document text here...")
print(f"{len(result.entities)} entities, {len(result.relationships)} relationships")

# With Engine -- chunk, inspect, then persist
chunks = await engine.chunking_service.create_chunks("...")
print(f"{chunks.total_small_chunks} chunks in {chunks.total_groups} groups")
engine.chunking_service.store_chunks(chunks)
```

---

### IndexingService

Generates vector embeddings for document chunks, enabling semantic search.

**Via Engine (recommended):**

```python
result = await engine.index_source("src_001")
print(f"Indexed {result.chunks_count} chunks with {result.embedding_model}")
```

Returns an `IndexingResult` model with `chunks_count`, `embedding_model`, and `embedding_dimensions`.

<details>
<summary>Manual wiring (adapter authors)</summary>

**From an adapter (recommended for custom backends):**

```python
from chaoscypher_core import IndexingService, EngineSettings

service = IndexingService.from_adapter(adapter, EngineSettings())
```

**From an engine:**

```python
service = IndexingService.from_engine(engine)
```

**Full constructor (for completely custom repositories):**

```python
IndexingService(
    repository: IndexingProtocol,  # Chunk storage with embedding support
    embedding_service: EmbeddingProviderProtocol,  # Embedding provider
    settings: EngineSettings,
)
```

All protocols are available as top-level imports: `from chaoscypher_core import IndexingProtocol, EmbeddingProviderProtocol`.

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_index` | `async (source_id) -> dict` | Generate embeddings for all chunks of a source |

**Return value from `create_index`:**

```python
{
    "chunks_count": 42,
    "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
    "embedding_dimensions": 1024,
}
```

</details>

---

### SearchService

Performs keyword, semantic, and hybrid search across graph nodes and document chunks.

**Via Engine (recommended):**

```python
# Hybrid search (default) -- returns list[EngineSearchResult]
results = await engine.search("quantum entanglement", limit=5)
for r in results:
    print(f"{r.label} (score: {r.score:.2f}, type: {r.result_type})")

# Keyword or semantic mode
results = await engine.search("quantum", mode="keyword")
results = await engine.search("quantum", mode="semantic")
```

Returns a `list[EngineSearchResult]` with attribute access (`label`, `score`, `result_type`, `content`, `source`, `template_id`).

To rebuild all search indexes:

```python
result = engine.rebuild_indexes()
print(f"Reindexed {result.total_nodes} nodes, {result.chunks_indexed} chunks")
```

<details>
<summary>Manual wiring (adapter authors)</summary>

**From an adapter (recommended for custom backends):**

```python
from chaoscypher_core import SearchService, EngineSettings

service = SearchService.from_adapter(
    adapter, EngineSettings(), search_repository=search_repo,
)
```

**From an engine:**

```python
from chaoscypher_core import SearchService
service = SearchService.from_engine(engine)
```

**Full constructor (for completely custom repositories):**

```python
from chaoscypher_core import (
    SearchService, SearchRepositoryProtocol, GraphRepositoryProtocol,
    IndexingProtocol, SourceStorageProtocol,
)

SearchService(
    search_repository: SearchRepositoryProtocol,
    graph_repository: GraphRepositoryProtocol,
    indexing_repository: IndexingProtocol,
    source_repository: SourceStorageProtocol,
    settings: EngineSettings | None = None,
    default_embedding_callback: Callable | None = None,
)
```

All protocols are available as top-level imports from `chaoscypher_core`.

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `keyword_search` | `(query, limit=10, include_disabled_sources=False) -> dict` | Full-text keyword search |
| `semantic_search` | `async (query, limit=10, embedding_provider_callback=None, ...) -> dict` | Vector similarity search |
| `hybrid_search` | `async (query, limit=10, embedding_provider_callback=None, min_similarity=0.55, ...) -> dict` | Semantic with keyword fallback |
| `get_stats` | `() -> dict` | Index statistics |
| `rebuild_indexes` | `() -> dict` | Rebuild all search indexes |

All search methods return a dict with `data` (list of result dicts) and `type` (search type string).

</details>

#### Search Modes

The `engine.search()` method accepts a `mode` parameter:

| Mode | Algorithm | Best for |
|------|-----------|----------|
| `"hybrid"` (default) | Combines keyword + semantic scoring | General queries — balances exact matches with meaning |
| `"semantic"` | Vector similarity only | Conceptual queries where exact terms don't matter |
| `"keyword"` | FTS5 full-text only | Exact phrase matching and known-item lookup |

```python
# Default hybrid search
results = await engine.search("neural network architecture")

# Semantic only — finds conceptually related content
results = await engine.search("brain-inspired computing", mode="semantic")

# Keyword only — exact phrase matching
results = await engine.search("transformers attention mechanism", mode="keyword")
```

---

## Graph Services

### NodeService

CRUD operations for graph nodes with automatic search index synchronization.

**Via Engine (recommended):**

```python
from chaoscypher_core import NodeCreate

# Create a node (returns Node model)
node = engine.create_node(NodeCreate(
    template_id=person.id,
    label="Ada Lovelace",
    properties={"born": "1815", "field": "mathematics"},
))
print(f"Created {node.id}: {node.label}")

# List nodes (returns PaginatedResult)
result = engine.list_nodes(page=1, page_size=20)
for node in result.data:
    print(f"{node.label} ({node.template_id})")
print(f"Total: {result.total}")
```

<details>
<summary>Manual wiring (adapter authors)</summary>

**From an adapter (recommended for custom backends):**

```python
from chaoscypher_core import NodeService, EngineSettings

service = NodeService.from_adapter(adapter, EngineSettings())
```

**From an engine:**

```python
service = NodeService.from_engine(engine)
```

**Full constructor (for completely custom repositories):**

```python
from chaoscypher_core import NodeService, GraphRepositoryProtocol, SearchRepositoryProtocol

NodeService(
    graph_repository: GraphRepositoryProtocol,
    search_repository: SearchRepositoryProtocol,
    settings: EngineSettings,
)
```

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `list_nodes` | `(template_id=None, source_ids=None, page=1, page_size=50, minimal=False) -> dict` | Paginated node listing |
| `get_node` | `(node_id) -> dict` | Get node by ID |
| `create_node` | `(node_create: NodeCreate) -> dict` | Create node (validates template, auto-indexes) |
| `update_node` | `(node_id, node_update: NodeUpdate) -> dict` | Update node (re-indexes) |
| `delete_node` | `(node_id) -> None` | Delete node (removes from index) |

</details>

---

### EdgeService

CRUD operations for graph edges (relationships between nodes).

**Via Engine (recommended):**

```python
from chaoscypher_core import EdgeCreate

# Create an edge (returns Edge model)
edge = engine.create_edge(EdgeCreate(
    template_id=knows.id,
    source_node_id=alice.id,
    target_node_id=bob.id,
    label="collaborated with",
    properties={"context": "Analytical Engine"},
))

# List edges (returns PaginatedResult)
result = engine.list_edges(source_node_id=alice.id, page=1)
```

<details>
<summary>Manual wiring (adapter authors)</summary>

**Import:**

```python
from chaoscypher_core import EdgeService
```

**Constructor:**

```python
EdgeService(
    graph_repository: GraphRepositoryProtocol,
)
```

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `list_edges` | `(source_node_id=None, target_node_id=None, source_ids=None, page=1, page_size=50, minimal=False) -> dict` | Paginated edge listing |
| `get_edge` | `(edge_id) -> dict` | Get edge by ID |
| `create_edge` | `(edge_create: EdgeCreate) -> dict` | Create edge (validates nodes and template) |
| `update_edge` | `(edge_id, edge_update: EdgeUpdate) -> dict` | Update edge |
| `delete_edge` | `(edge_id) -> None` | Delete edge |

</details>

---

## Extraction Services

### ExtractionService

Performs entity deduplication, normalization, template matching, and embedding generation. This is an internal pipeline stage — most users should use `engine.add_document()` or `engine.process_document()` instead.

**Via Engine (recommended):**

```python
# Full pipeline — handles chunking, extraction, and commit automatically
result = await engine.add_document("paper.pdf")
print(f"Created {len(result.nodes)} nodes, {len(result.edges)} edges")
```

<details>
<summary>Direct access (adapter authors)</summary>

For custom extraction pipelines that need access to intermediate results:

```python
results = await engine.extraction_service.extract(
    entities=entities_from_chunks,
    relationships=relationships_from_chunks,
    generate_embeddings=True,
    domain="historical",
)
```

**Key methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `extract` | `async (entities, relationships, *, domain=None, generate_embeddings=True) -> dict` | Finalize extraction: dedup, normalize, suggest templates, embed |
| `build_extraction_results` | `async (entities, relationships, ...) -> dict` | Build results from already-deduplicated data |

**Manual wiring:**

```python
from chaoscypher_core import ExtractionService
service = ExtractionService.from_engine(engine)
```

For full control:

```python
from chaoscypher_core import ExtractionService, LLMProvider
llm_provider = LLMProvider(settings=engine.settings)
service = ExtractionService(
    graph_repository=engine.graph_repository,
    llm_provider=llm_provider,
    settings=engine.settings,
    embedding_service=engine.embedding_service,  # required keyword-only
)
```

</details>

---

## Commit Services

### SourceCommitService

Orchestrates writing extraction results into the graph database. This is an internal pipeline stage — most users should use `engine.add_document()` or `engine.commit(source_id)` instead.

**Via Engine (recommended):**

```python
# Full pipeline
result = await engine.add_document("paper.pdf")

# Or commit after manual chunking
result = await engine.commit(source_id)
```

<details>
<summary>Direct access (adapter authors)</summary>

For custom commit workflows:

```python
result = await engine.commit_service.commit(
    file_id="source_001",
    commit_data=extraction_results,
    file_info={"filename": "research_paper.pdf"},
    auto_enable=True,
)
```

The `commit` method is idempotent -- calling it again for the same source cleans up previous graph data first.

**Manual wiring:**

```python
from chaoscypher_core import SourceCommitService
service = SourceCommitService.from_engine(engine)
```

</details>

---

## Utilities

### Database Statistics

```python
stats = engine.get_stats()
print(f"Nodes: {stats.nodes}, Edges: {stats.edges}, Templates: {stats.templates}")
```

### Health Checks

```python
health = await engine.check_health()
if health.chat.status == "healthy":
    print(f"Chat OK ({health.chat.response_time_ms}ms)")
else:
    print(f"Chat unhealthy: {health.chat.error}")
```

### Index Rebuilding

Rebuild search indexes after bulk imports or embedding model changes:

```python
result = engine.rebuild_indexes()
print(f"{result.total_nodes} nodes, {result.nodes_with_embeddings} with embeddings, {result.chunks_indexed} chunks indexed")
```

### Direct Service Access

For advanced use cases, access underlying services directly:

```python
# Direct LLM access
response = await engine.llm_provider.chat("Explain knowledge graphs")

# Direct embedding access
embedding = await engine.embedding_service.embed("sample text")
```

## Protocol Summary

Each service depends on one or more protocol interfaces from `chaoscypher_core.ports`:

| Protocol | Module | Used By |
|----------|--------|---------|
| `ChunkingProtocol` | `ports.chunk` | ChunkingService (optional, for persistence) |
| `IndexingProtocol` | `ports.index` | IndexingService, SearchService, SourceCommitService |
| `GraphRepositoryProtocol` | `ports.graph` | NodeService, EdgeService, SearchService, ExtractionService, SourceCommitService |
| `SearchRepositoryProtocol` | `ports.search` | NodeService, SearchService, SourceCommitService |
| `SourceStorageProtocol` | `ports.storage_sources` | SearchService, SourceCommitService |

The default implementation for all storage protocols is `SqliteAdapter`. See [Storage Adapters](storage-adapters.md) for details.
