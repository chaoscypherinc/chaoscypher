# ChaosCypher Core

**Standalone AI knowledge graph library using Hexagonal Architecture (Ports & Adapters)**

Version: 0.1.0

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)

## Quick Start

### Zero-boilerplate facade (no database, no setup)

```python
from chaoscypher_core import ChaosCypher

# Configure once (optional — defaults to Ollama, env vars auto-detected)
ChaosCypher.configure(provider="openai", api_key="sk-...")

# Extract entities from any document
result = ChaosCypher.extract_sync("paper.pdf")
print(f"{len(result.entities)} entities, {len(result.relationships)} relationships")

# Chat with an LLM
response = ChaosCypher.chat_sync("Explain knowledge graphs")
print(response.content)

# Embed text (single or batch)
single = ChaosCypher.embed_sync("quantum entanglement")
batch = ChaosCypher.embed_batch_sync(["text one", "text two"])

# Process documents into a knowledge graph
result = ChaosCypher.add_document_sync("paper.pdf", database="demo")
results = ChaosCypher.add_documents_sync(["doc1.pdf", "doc2.pdf"])

# Extract from raw text (no file needed)
result = await ChaosCypher.extract(text="Alice knows Bob. Bob works at Acme.")
chunks = await ChaosCypher.chunk(text="Long document content...")
```

A short alias `CC` is also available: `from chaoscypher_core import CC`.

Every method has an async variant (`extract`, `chat`, `embed`, `embed_batch`,
`search`, `add_document`, `add_documents`, `chunk`) and a sync variant with
a `_sync` suffix.

### Build a knowledge graph (with persistent storage)

```python
from chaoscypher_core import Engine

# Inline configuration — same kwargs as ChaosCypher.configure()
with Engine(database="demo", provider="openai", api_key="sk-...") as engine:
    # Quick graph building with auto-created templates
    alice = engine.add_node("Person", "Alice", properties={"role": "Engineer"})
    bob = engine.add_node("Person", "Bob")
    engine.add_edge("knows", alice, bob)

    # Process a document into the graph
    result = engine.add_document_sync("paper.pdf")
    print(f"Extracted {len(result.nodes)} nodes")

    # Search the graph (sync and async)
    results = engine.search_sync("quantum entanglement")
    results = await engine.search("quantum entanglement", mode="semantic")

    # Chat and embed (sync wrappers for scripts/notebooks)
    response = engine.chat_sync("Summarize this graph")
    embedding = engine.embed_sync("quantum physics")
    embeddings = engine.batch_embed_sync(["text one", "text two"])

    stats = engine.get_stats()
    print(f"Graph: {stats.nodes} nodes, {stats.edges} edges")
```

`Engine` also accepts `data_dir=` for an explicit path instead of a database name.

## Overview

A reusable, framework-agnostic library providing core AI-powered knowledge graph capabilities:

- **Entity Extraction:** AI-powered extraction from documents with template matching and deduplication
- **Document Loading:** Auto-detect file type (PDF, text, HTML, RST, DOCX, XLSX, PPTX, EPUB, CSV, JSON, JSONL, audio, video, image, archives)
- **RAG Pipeline:** Chunking, vector embeddings, and hybrid search (keyword + semantic)
- **Graph Management:** Node, edge, and template CRUD with search index synchronization
- **Graph Analytics:** Centrality, clustering, path analysis, community detection
- **Workflow Execution:** LangGraph-based workflow engine with tool calling
- **LLM Integration:** Ollama, OpenAI, Anthropic, and Gemini providers

## Architecture: Hexagonal (Ports & Adapters)

```
┌─────────────────────────────────────────────────────────────┐
│                     PORTS (Interfaces)                       │
│  ports/  - Python Protocols defining contracts               │
│  - GraphRepositoryProtocol, SearchRepositoryProtocol, etc.   │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │
┌─────────────────────────────┼───────────────────────────────┐
│                        CORE (Business Logic)                 │
│  services/  - Domain-organized business rules                │
│  - graph/, search/, sources/, workflows/, chat/, export/     │
│                                                              │
│  models.py  - Pure Pydantic DTOs (no ORM dependencies)       │
│  exceptions.py - Domain-specific exceptions                  │
└──────────────────────────────────────────────────────────────┘
                              ▲
                              │
┌─────────────────────────────┼───────────────────────────────┐
│                     ADAPTERS (Implementations)               │
│  adapters/sqlite/  - SQLite storage (default)                │
│  adapters/llm/     - LLM providers (Ollama, OpenAI, etc.)    │
│  repos/graph/      - Graph repository (SQLite-backed)        │
│  repos/search/     - Search repository (sqlite-vec + FTS5)   │
└──────────────────────────────────────────────────────────────┘
```

**Why Hexagonal?**

1. **Framework Independence:** No FastAPI, SQLModel, or Docker dependencies in core
2. **Testability:** Easy to mock protocol interfaces for unit testing
3. **Portability:** Embeddable in web apps, CLIs, Jupyter notebooks, or scripts
4. **Dual Deployment:** Supports async (Docker + Valkey) and sync (standalone) modes

## Import Strategy

**Namespace class (recommended for most users):**

```python
from chaoscypher_core import ChaosCypher  # or CC

# One import gives you IDE autocomplete for every method
result = ChaosCypher.extract_sync("paper.pdf")
response = ChaosCypher.chat_sync("Explain knowledge graphs")
embedding = ChaosCypher.embed_sync("quantum entanglement")
batch = ChaosCypher.embed_batch_sync(["text one", "text two"])
results = ChaosCypher.search_sync("quantum", database="demo")
```

**Granular imports (organized by tier):**

```python
from chaoscypher_core import (
    # --- Primary API (start here) ---
    ChaosCypher, CC, Engine,
    # Async convenience functions
    extract, chat, embed, embed_batch, search, chunk,
    add_document, add_documents,
    # Sync convenience functions
    extract_sync, chat_sync, embed_sync, embed_batch_sync, search_sync, chunk_sync,
    add_document_sync, add_documents_sync,

    # --- Configuration ---
    EngineSettings, LLMSettings,

    # --- Models (input DTOs) ---
    NodeCreate, NodeUpdate, NodePosition,
    EdgeCreate, EdgeUpdate,
    TemplateCreate, TemplateUpdate,
    PropertyDefinition, PropertyType,
    AnalysisDepth, SearchMode, ProgressCallback, ProgressStage,

    # --- Models (output DTOs) ---
    Node, Edge, Template,
    ExtractionResult, ProcessingResult, LLMChatResponse,
    EmbedResult, BatchEmbedResult, EngineSearchResult,
    ChunksResult, ChunkingResult, IndexingResult, RebuildResult,
    PaginatedResult, DatabaseStats, DatabaseInfo,
    HealthReport, HealthResult, TokenUsage, ToolResult, SourceStatus,

    # --- Services (for Engine direct usage) ---
    NodeService, EdgeService, TemplateService,
    IndexingService, SearchService,
    ExtractionService, SourceCommitService,
    ChunkingService, Loaders,

    # --- Advanced (adapters, protocols, factories) ---
    LLMProvider, BaseLLMProvider, ProviderFactory, SqliteAdapter,
    create_embedding_provider, ToolExecutionContext, generate_id,
    # Protocols (for custom adapter authors)
    GraphRepositoryProtocol, SearchRepositoryProtocol,
    ChunkingProtocol, IndexingProtocol, EmbeddingProviderProtocol,
    SourcesProtocol, SourceStorageProtocol,
)
```

All public APIs are available as top-level imports. Deep imports are only needed for internal/private APIs.

## Directory Structure

```
chaoscypher_core/
├── __init__.py              # Public API exports
├── models.py                # Pure Pydantic DTOs
├── settings.py              # EngineSettings configuration
├── exceptions.py            # Exception hierarchy
├── bootstrap.py             # Engine class (single entry point)
│
├── ports/                   # PORTS: Protocol definitions (contracts)
│   ├── graph.py             # GraphRepositoryProtocol
│   ├── search.py            # SearchRepositoryProtocol
│   ├── chunk.py             # ChunkingProtocol
│   ├── index.py             # IndexingProtocol
│   ├── embedding.py         # EmbeddingProviderProtocol
│   ├── llm.py               # LLM provider protocols
│   ├── db.py                # DatabaseProtocol
│   └── storage_*.py         # Granular storage protocols, one file per concern
│                            # (storage_sources, storage_chats, storage_tools,
│                            #  storage_triggers, storage_workflows, …)
│
├── services/                # CORE: Business logic (domain-organized)
│   ├── graph/               # Graph CRUD (NodeService, EdgeService, TemplateService)
│   │   ├── engine/          # Analytics (centrality, clustering, paths)
│   │   └── management/      # Node, edge, template, source management
│   ├── search/              # RAG pipeline
│   │   └── engine/          # IndexingService, SearchService
│   ├── sources/             # Document processing
│   │   ├── engine/          # Extraction, commit, deduplication
│   │   │   └── extraction/utils/post_extraction.py  # Shared structural-filter + type-normalize (Cortex/CLI/MCP parity)
│   │   ├── loaders/         # File type loaders (PDF, text, HTML, RST, DOCX, XLSX, PPTX, EPUB, CSV, JSON, JSONL, audio, video, image)
│   │   ├── normalizer/      # Content cleaning (cleaners return CleanerResult; OCRCleaner overrides applies_to)
│   │   └── management/      # Source CRUD
│   ├── workflows/           # Workflow orchestration
│   │   ├── engine/          # LangGraph executor
│   │   ├── management/      # Workflow CRUD
│   │   ├── tools/           # Tool plugins
│   │   └── triggers/        # Event triggers
│   ├── chat/                # Conversational AI
│   ├── export/              # Graph data export
│   ├── quality/             # Entity quality scoring + per-source quality counters (counters.py)
│   ├── compose/             # Graph merge/resolve
│   ├── lexicon/             # Vocabulary management
│   ├── presets/             # Preset configurations
│   └── package/             # Import/export packages
│
├── adapters/                # ADAPTERS: External integrations
│   ├── sqlite/              # SQLite storage adapter (default)
│   │   ├── adapter.py       # SqliteAdapter (implements all storage protocols)
│   │   ├── models.py        # SQLModel entity definitions
│   │   └── mixins/          # Protocol implementations (14 focused mixins)
│   ├── llm/                 # LLM provider system
│   │   ├── factory.py       # ProviderFactory (cached provider creation)
│   │   ├── provider.py      # LLMProvider (queue-free direct access)
│   │   └── providers/       # Ollama, OpenAI, Anthropic, Gemini
│   └── web/                 # Web/HTTP integrations
│
├── repos/                   # DATA ACCESS: Repository implementations
│   ├── graph/               # GraphRepository (SQLite-backed)
│   ├── search/              # SearchRepository (sqlite-vec + FTS5)
│   └── extraction/          # ExtractionRepository
│
├── plugins/                 # Plugin system (base classes, discovery, registry)
├── utils/                   # Utilities (ID generation, chunking, logging)
│   ├── encoding.py          # detect_encoding(path) — text-shaped loader pre-read
│   └── normalization_default.py  # resolve_normalization_default(filename) — CSV/JSON default off
└── data/                    # Static data files
```

## API Reference

### ChaosCypher Facade

Static namespace class for one-import convenience. No instantiation needed.

| Method | Sync variant | Returns |
|--------|-------------|---------|
| `extract(source, *, text=)` | `extract_sync` | `ExtractionResult` |
| `chat(messages)` | `chat_sync` | `LLMChatResponse` |
| `embed(text)` | `embed_sync` | `EmbedResult \| BatchEmbedResult` |
| `embed_batch(texts)` | `embed_batch_sync` | `BatchEmbedResult` |
| `search(query, database=)` | `search_sync` | `list[EngineSearchResult]` |
| `chunk(source, *, text=)` | `chunk_sync` | `ChunksResult` |
| `add_document(filepath)` | `add_document_sync` | `ProcessingResult` |
| `add_documents(paths)` | `add_documents_sync` | `list[ProcessingResult]` |
| `load(filepath)` | (sync only) | `str` |
| `configure(provider=, api_key=)` | (sync only) | `None` |

`extract()` and `chunk()` accept `text=` for explicit text input, skipping file detection.

`embed_batch()` / `embed_batch_sync()` always return `BatchEmbedResult` (no union type).

### Engine

Persistent graph engine with full service access. Accepts the same `provider=`, `api_key=`, and configuration kwargs as `ChaosCypher.configure()`.

```python
Engine(database="mydb", provider="openai", api_key="sk-...")
Engine(data_dir="./data/databases/mydb")            # explicit path
Engine(database="mydb", settings=my_engine_settings) # pre-built settings
```

**Convenience methods (return Pydantic models):**

| Method | Returns | Notes |
|--------|---------|-------|
| `add_node(template, label)` | `Node` | Auto-creates template |
| `add_edge(template, source, target)` | `Edge` | Auto-creates template |
| `add_document(filepath)` | `ProcessingResult` | Full pipeline (async) |
| `add_documents(paths)` | `list[ProcessingResult]` | Batch pipeline (async) |
| `search(query)` | `list[EngineSearchResult]` | Async |
| `chat(messages)` | `LLMChatResponse` | Async |
| `embed(text)` | `EmbedResult` | Async |
| `batch_embed(texts)` | `BatchEmbedResult` | Async |

**Sync wrappers (for scripts and notebooks):**

`search_sync`, `chat_sync`, `embed_sync`, `batch_embed_sync`,
`add_document_sync`, `add_documents_sync`, `process_document_sync`

### Advanced: Direct Service Access

For fine-grained control, Engine exposes the underlying services and models.
Use `TemplateCreate`, `NodeCreate`, and `EdgeCreate` DTOs when you need full
control over IDs, properties, and template configuration:

```python
from chaoscypher_core import Engine, TemplateCreate, NodeCreate, EdgeCreate

with Engine(database="demo") as engine:
    # Create a template with property definitions
    t = engine.create_template(TemplateCreate(
        name="Person",
        template_type="node",
        properties={"role": {"type": "string"}},
    ))

    # Create nodes with explicit template ID
    alice = engine.create_node(NodeCreate(
        template_id=t.id, label="Alice", properties={"role": "Engineer"},
    ))
    bob = engine.create_node(NodeCreate(
        template_id=t.id, label="Bob", properties={"role": "Designer"},
    ))

    # Direct service access (returns dicts, not models)
    nodes = engine.node_service.list_nodes()
    templates = engine.template_service.list_templates()
```

## Storage Protocols

All storage protocols are defined in `chaoscypher_core.ports` and return `dict[str, Any]`:

| Protocol | Purpose |
|----------|---------|
| `GraphRepositoryProtocol` | Node, edge, and template CRUD |
| `SearchRepositoryProtocol` | Keyword, vector, and hybrid search |
| `ChunkingProtocol` | Hierarchical document chunking |
| `IndexingProtocol` | Chunk embedding storage and retrieval |
| `WorkflowStorageProtocol` | Workflow definitions, steps, statistics |
| `SourceStorageProtocol` | Source document lifecycle |
| `ChatStorageProtocol` | Chat conversations and messages |
| `ToolStorageProtocol` | Tool registry |
| `TriggerStorageProtocol` | Event triggers |
| `LLMMetricsStorageProtocol` | LLM call metrics and cost tracking |
| `SourcesProtocol` | Source and citation management |

The default implementation for all protocols is `SqliteAdapter` (via 14 focused mixins following Interface Segregation Principle).

## Plugin Protocols

In addition to storage protocols, the core library exposes plugin protocols for extension points with a shared metadata contract:

- `PluginMetadata` (`plugins/base.py`) — unified Pydantic descriptor for all plugin
  types (loaders, tools, cleaners, archive handlers, LLM providers). Carries
  `plugin_id / name / description / version / priority / applies_to / origin / tags`.
- `CleanerProtocol` (`services/sources/normalizer/cleaners/base.py`) — `clean(content, metadata) -> CleanerResult`
  carrying `(content, ops, lines_removed, paragraphs_deduplicated, chars_removed)`. Tuple
  unpacking still works via `CleanerResult.__iter__` for back-compat. Cleaners may
  optionally implement `applies_to(metadata) -> bool` to gate themselves by document
  metadata (`OCRCleaner` uses this to fire only on OCR-derived content).
- `ArchiveHandler` (`services/sources/loaders/archive/handlers/base.py`) — extended in PR 1
  to carry `metadata`, `can_handle() -> int` specificity score, and `find_root()` for
  nested-docs discovery.

## Testing

Protocol-based design enables clean mocking. All protocols are top-level exports:

```python
from unittest.mock import Mock
from chaoscypher_core import ExtractionService, GraphRepositoryProtocol

def test_extraction():
    service = ExtractionService(
        graph_repository=Mock(spec=GraphRepositoryProtocol),
        llm_provider=Mock(),
        settings=Mock(),
    )
    # Test business logic with mocked dependencies
```

## Design Principles

- **KISS:** Simple, focused services with clear responsibilities
- **DRY:** Shared logic in utils/, protocol-based reuse
- **SOLID:** Interface segregation, dependency inversion, single responsibility
- **No Technical Debt:** Clean breaks over gradual migrations, delete dead code immediately

## Documentation

For complete documentation including tutorials, API reference, and architecture guides, see the docs site or `docs/developer-guide/` in the repository.

## License

See main project LICENSE
