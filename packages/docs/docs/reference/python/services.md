---
title: "Services API"
---

# Services API

Core business logic services that orchestrate operations across the knowledge graph platform.

## `chaoscypher_core.services.sources.engine.extraction.service`

Business logic for Extraction feature.

Service orchestrates entity extraction and template matching by delegating
to focused sub-modules: preprocessor (normalization), extractor (AI extraction,
deduplication, embeddings), and template_matcher (edge suggestions).

### `class ExtractionService`

Business logic for entity extraction.

This service is independent of the import workflow and can be used
to extract entities from any text content.

**Methods:**

#### `build_extraction_results(entities: list[dict[str, Any]], relationships: list[dict[str, Any]], generate_embeddings: bool, cached_embeddings: list[Any], detected_domain: str | None, forced_domain: str | None = None, extraction_depth: str = 'full') -> dict[str, Any]`

Public entry point for building extraction results.

Normalizes entities, generates template suggestions and embeddings,
and builds the final results dict.

Args:
    entities: Deduplicated entities.
    relationships: Remapped relationships.
    generate_embeddings: Whether to generate entity embeddings.
    cached_embeddings: Cached embeddings from semantic dedup.
    detected_domain: Auto-detected domain.
    forced_domain: User-forced domain override.
    extraction_depth: Depth for metadata.

Returns:
    Complete extraction results dictionary.

| Parameter | Type | Description |
|---|---|---|
| `entities` | `list[dict[str, Any]]` |  |
| `relationships` | `list[dict[str, Any]]` |  |
| `generate_embeddings` | `bool` |  |
| `cached_embeddings` | `list[Any]` |  |
| `detected_domain` | `str \| None` |  |
| `forced_domain` | `str \| None` |  |
| `extraction_depth` | `str` |  |

#### `extract(entities: list[dict[str, Any]], relationships: list[dict[str, Any]], domain: str | None = None, generate_embeddings: bool = True, edge_type_constraints: dict[str, dict[str, list[str]]] | None = None, filtering_config: FilteringConfig | None = None) -> dict[str, Any]`

Extract, deduplicate, and normalize entities and relationships.

Clean alias for finalize_distributed_extraction with simplified
parameter names.

Args:
    entities: Raw entities from chunk-level extraction.
    relationships: Raw relationships (indices relative to entities).
    domain: Detected or forced domain name (e.g., 'literary', 'scientific').
    generate_embeddings: Generate entity embeddings. Defaults to True.
    edge_type_constraints: See `finalize_distributed_extraction`.
    filtering_config: See `finalize_distributed_extraction`.

Returns:
    Dict with 'entities', 'relationships', 'suggested_templates',
    'suggested_edge_templates', 'metadata', and 'embeddings' keys.

| Parameter | Type | Description |
|---|---|---|
| `entities` | `list[dict[str, Any]]` |  |
| `relationships` | `list[dict[str, Any]]` |  |
| `domain` | `str \| None` |  |
| `generate_embeddings` | `bool` |  |
| `edge_type_constraints` | `dict[str, dict[str, list[str]]] \| None` |  |
| `filtering_config` | `FilteringConfig \| None` |  |

#### `finalize_distributed_extraction(raw_entities: list[dict[str, Any]], raw_relationships: list[dict[str, Any]], generate_embeddings: bool = True, file_info: dict[str, Any] | None = None, detected_domain: str | None = None, forced_domain: str | None = None, edge_type_constraints: dict[str, dict[str, list[str]]] | None = None, filtering_config: FilteringConfig | None = None) -> dict[str, Any]`

Finalize extraction from pre-extracted chunk results.

Used by distributed extraction (Cortex workers) where chunk extraction
happens in parallel via queue, then finalization aggregates results.

This performs all post-extraction steps:
- Entity deduplication (exact or semantic)
- Cross-chunk relationship filtering (type-constraint validation,
  relationship-limit enforcement) when `edge_type_constraints` or
  `filtering_config` is provided
- Relationship index remapping
- Template matching
- Node and edge template suggestions
- Embedding generation

Args:
    raw_entities: Aggregated entities from all chunks
    raw_relationships: Aggregated relationships (indices relative to raw_entities)
    generate_embeddings: Whether to generate entity embeddings
    file_info: Optional file metadata for template suggestions
    detected_domain: Domain detected during extraction (for edge templates)
    forced_domain: User-forced domain override
    edge_type_constraints: Domain edge-type constraints used by the
        cross-chunk type-constraint filter. When `None` (default),
        the filter is skipped — appropriate for callers that already
        ran cross-chunk filtering upstream (e.g. `extract_entities_from_groups`)
        or that don't have a domain in scope.
    filtering_config: Resolved FilteringConfig for cross-chunk filters.
        When `None` (default), cross-chunk filtering is skipped. The
        CLI extraction path threads this in so its pipeline matches
        the Cortex/Neuron worker path; the standalone `Engine`-level
        callers run filters upstream and pass `None` here.

Returns:
    Dictionary with:
        - entities: Deduplicated, template-matched entities
        - relationships: Remapped relationships
        - matched_templates: List of templates used
        - suggested_templates: Node template suggestions
        - suggested_edge_templates: Edge template suggestions
        - metadata: Processing metadata
        - embeddings: Optional embeddings data

| Parameter | Type | Description |
|---|---|---|
| `raw_entities` | `list[dict[str, Any]]` |  |
| `raw_relationships` | `list[dict[str, Any]]` |  |
| `generate_embeddings` | `bool` |  |
| `file_info` | `dict[str, Any] \| None` |  |
| `detected_domain` | `str \| None` |  |
| `forced_domain` | `str \| None` |  |
| `edge_type_constraints` | `dict[str, dict[str, list[str]]] \| None` |  |
| `filtering_config` | `FilteringConfig \| None` |  |

#### `from_engine(engine: Any) -> ExtractionService`

Create an ExtractionService from an Engine instance.

Convenience factory that wires dependencies from the engine's
pre-configured services.

Args:
    engine: Engine instance with llm_provider and graph_repository.

Returns:
    Configured ExtractionService.

| Parameter | Type | Description |
|---|---|---|
| `engine` | `Any` |  |

#### `get_domain_inverse_relationships(domain_name: str | None) -> dict[str, str]`

Public entry point for getting domain inverse relationship mappings.

Args:
    domain_name: Name of the domain (e.g., 'literary', 'historical')

Returns:
    Mapping of edge type to inverse edge type, or empty dict.

| Parameter | Type | Description |
|---|---|---|
| `domain_name` | `str \| None` |  |

#### `get_domain_normalization_rules(domain_name: str | None) -> dict[str, list[str]]`

Public entry point for getting domain type-normalization rules.

Used by the production finalizer to re-type generic entities
(`Item` -> `Class`) the same way `extract_entities_from_groups`
does. Workstream 3, Tasks 3.1+3.2.

Args:
    domain_name: Name of the domain (e.g., 'technical', 'literary').

Returns:
    Mapping of target type to keyword list, or empty dict.

| Parameter | Type | Description |
|---|---|---|
| `domain_name` | `str \| None` |  |

#### `get_domain_symmetric_relationships(domain_name: str | None) -> list[str]`

Public entry point for getting domain symmetric relationship types.

Symmetric relationships are bidirectional — (A, B) and (B, A) are
semantically identical and collapsed during deduplication.

Args:
    domain_name: Name of the domain (e.g., 'literary', 'historical')

Returns:
    List of symmetric relationship type names, or empty list.

| Parameter | Type | Description |
|---|---|---|
| `domain_name` | `str \| None` |  |

#### `get_domain_title_words(domain_name: str | None) -> frozenset[str] | None`

Public entry point for getting domain title words.

Args:
    domain_name: Name of the domain (e.g., 'literary', 'historical')

Returns:
    Frozenset of lowercase title words, or None if unavailable.

| Parameter | Type | Description |
|---|---|---|
| `domain_name` | `str \| None` |  |

#### `get_domain_type_compatibility(domain_name: str | None) -> dict[str, list[str]] | None`

Public entry point for getting domain type compatibility groups.

Args:
    domain_name: Name of the domain (e.g., 'literary', 'technical')

Returns:
    Dictionary of compatibility groups, or None if unavailable.

| Parameter | Type | Description |
|---|---|---|
| `domain_name` | `str \| None` |  |

**Attributes:**

- `embedding_service`
- `graph_repository`
- `llm_provider`
- `settings`

## `chaoscypher_core.services.search.engine.index`

Indexing Service for chaoscypher-engine.

Business logic for document indexing (RAG).
Generates embeddings for pre-chunked documents.

### `class IndexingService`

Business logic for document indexing (RAG).

Works with pre-chunked data from ChunkingService.
Responsible for generating embeddings and indexing chunks.

**Methods:**

#### `create_index(source_id: str, progress_callback: Callable[[int, int], None] | None = None, cancellation_check: Callable[[], Any] | None = None) -> dict[str, Any]`

Generate embeddings for pre-chunked data and prepare for indexing.

Workflow:
1. Get small chunks from ChunkingService (already created)
2. Delegate embedding generation + persistence to embed_chunks
3. Ready for commit to vector search index

Args:
    source_id: Source ID
    progress_callback: Optional callback(processed, total) called after each batch.
    cancellation_check: Optional async callable returning True if task should stop.

Returns:
    \{
        'chunks_count': int,
        'embedding_model': str,
        'embedding_dimensions': int
    \}

Raises:
    ValidationError: If no chunks are found for `source_id`.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `progress_callback` | `Callable[[int, int], None] \| None` |  |
| `cancellation_check` | `Callable[[], Any] \| None` |  |

#### `embed_chunks(chunks: list[dict[str, Any]], source_id: str, database_name: str, progress_callback: Callable[[int, int], None] | None = None, cancellation_check: Callable[[], Any] | None = None, expected_dimensions: int | None = None) -> int`

Embed a pre-fetched list of chunks and persist the vectors.

This is the shared "vector-index write path" used by both
`create_index` (fetches all chunks for a source) and the
incremental embedding sub-stage invoked by the indexing_handler
on resume (fetches only chunks where `embedded_at IS NULL`).

Accepting an explicit chunk list means the caller chooses the
resume semantics — this method stays oblivious to whether it is
running an initial pass or a restart continuation.

Args:
    chunks: List of chunk dicts. Each must have `id` and
        `content`. Typically the output of
        `get_chunks_by_source` or `list_unembedded_chunks`.
    source_id: Source these chunks belong to (used for logging).
    database_name: Active database name. Currently unused by this
        method because the repository is already bound to a
        database, but threaded through so the signature mirrors
        the adapter-level methods for future multi-database
        workers.
    progress_callback: Optional callback(processed, total) called
        after each embedding wave.
    cancellation_check: Optional async callable returning True to
        abort.
    expected_dimensions: Optional dimension recorded on the
        source's `SourceRow.embedding_dimensions` from a prior
        embedding pass. When set, every returned vector must
        match this length or a `ValidationError` is raised
        BEFORE any persistence. Catches the case where the
        operator changes the configured embedding model after a
        source has already been embedded — the per-source check
        is stricter than the global `settings.search.vector_dimensions`
        check because it reflects what the existing rows actually
        contain. Pass `None` for a first-pass embedding (no
        prior dimension on record).

Returns:
    Number of chunks successfully embedded (total minus any
    NotFound skips encountered during per-chunk persistence).

Raises:
    ValidationError: When `expected_dimensions` is provided and
        any returned vector's length does not equal it. The
        exception details carry `source_id`, `chunk_index`,
        `expected`, and `actual` so on-call can identify the
        mismatch without enabling debug logging.

| Parameter | Type | Description |
|---|---|---|
| `chunks` | `list[dict[str, Any]]` |  |
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `progress_callback` | `Callable[[int, int], None] \| None` |  |
| `cancellation_check` | `Callable[[], Any] \| None` |  |
| `expected_dimensions` | `int \| None` |  |

#### `from_adapter(adapter: SqliteAdapter, settings: EngineSettings, embedding_service: EmbeddingProviderProtocol | None = None) -> IndexingService`

Create IndexingService from a storage adapter.

Args:
    adapter: SqliteAdapter (or compatible) implementing IndexingProtocol.
    settings: Engine settings.
    embedding_service: Optional embedding provider override.

Returns:
    Configured IndexingService.

Example:
    from chaoscypher_core import IndexingService, SqliteAdapter, EngineSettings

    adapter = SqliteAdapter("app.db", "default")
    service = IndexingService.from_adapter(adapter, EngineSettings())

| Parameter | Type | Description |
|---|---|---|
| `adapter` | `SqliteAdapter` |  |
| `settings` | `EngineSettings` |  |
| `embedding_service` | `EmbeddingProviderProtocol \| None` |  |

#### `from_engine(engine: Any) -> IndexingService`

Create IndexingService from an Engine instance.

Args:
    engine: An Engine instance with storage_adapter and settings.

Returns:
    Configured IndexingService.

Example:
    service = IndexingService.from_engine(engine)
    result = await service.create_index(source_id)

| Parameter | Type | Description |
|---|---|---|
| `engine` | `Any` |  |

**Attributes:**

- `embedding_service`
- `repository`
- `settings`

## `chaoscypher_core.services.search.engine.search`

Search Service for chaoscypher-engine.

Business logic for search operations with node and chunk hydration.

### `class SearchService`

Service for search business logic with node and chunk hydration.

Handles keyword, semantic, and hybrid search across both:
- Graph nodes (entities in the knowledge graph)
- Document chunks (RAG indexed documents)

**Methods:**

#### `from_adapter(adapter: SqliteAdapter, settings: EngineSettings, search_repository: SearchRepositoryProtocol, graph_repository: GraphRepositoryProtocol | None = None, default_embedding_callback: Any = None) -> SearchService`

Create a SearchService from a storage adapter.

Wires the adapter into protocol slots (IndexingProtocol,
SourceStorageProtocol) that it already implements.  Graph repository
is created from the adapter session if not provided.

Args:
    adapter: SqliteAdapter (or compatible) implementing
        IndexingProtocol and SourceStorageProtocol.
    settings: Engine settings.
    search_repository: SearchRepository instance (required — needs
        SQLAlchemy engine that the adapter doesn't expose directly).
    graph_repository: Optional GraphRepository override.
    default_embedding_callback: Optional embedding callback for
        semantic search queries.

Returns:
    Configured SearchService.

Raises:
    OperationError: If `graph_repository` is not provided and
        `adapter.session` is None (i.e. `adapter.connect()` has
        not been called).

Example:
    from chaoscypher_core import SearchService, SqliteAdapter, EngineSettings
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository

    adapter = SqliteAdapter("app.db", "default")
    search_repo = SearchRepository(db_engine, 1024, "model-name")
    service = SearchService.from_adapter(
        adapter, EngineSettings(), search_repository=search_repo,
    )

| Parameter | Type | Description |
|---|---|---|
| `adapter` | `SqliteAdapter` |  |
| `settings` | `EngineSettings` |  |
| `search_repository` | `SearchRepositoryProtocol` |  |
| `graph_repository` | `GraphRepositoryProtocol \| None` |  |
| `default_embedding_callback` | `Any` |  |

#### `from_engine(engine: Any) -> SearchService`

Create a SearchService wired from an Engine instance.

Args:
    engine: Engine instance with search_repository, graph_repository,
        storage_adapter, and settings.

Returns:
    SearchService with all dependencies injected.

| Parameter | Type | Description |
|---|---|---|
| `engine` | `Any` |  |

#### `get_stats() -> dict[str, Any]`

Get search index statistics.

Returns:
    Dict with index stats (nodes_indexed, chunks_indexed, etc.)

#### `hybrid_search(query: str, limit: int = 10, embedding_provider_callback: Any = None, min_similarity: float = 0.55, include_disabled_sources: bool = False) -> dict[str, Any]`

Perform hybrid search (semantic with keyword fallback).

Args:
    query: Search query
    limit: Maximum results
    embedding_provider_callback: Optional callback for generating query embedding.
        Falls back to the default callback injected at construction time.
    min_similarity: Minimum similarity score to consider a result relevant
    include_disabled_sources: If True, include results from disabled sources

Returns:
    Dict with 'data' (list of results) and 'type' (search type)

| Parameter | Type | Description |
|---|---|---|
| `query` | `str` |  |
| `limit` | `int` |  |
| `embedding_provider_callback` | `Any` |  |
| `min_similarity` | `float` |  |
| `include_disabled_sources` | `bool` |  |

#### `keyword_search(query: str, limit: int = 10, include_disabled_sources: bool = False) -> dict[str, Any]`

Perform keyword search.

Args:
    query: Search query
    limit: Maximum results
    include_disabled_sources: If True, include results from disabled sources

Returns:
    Dict with 'data' (list of results) and 'type' (search type)

| Parameter | Type | Description |
|---|---|---|
| `query` | `str` |  |
| `limit` | `int` |  |
| `include_disabled_sources` | `bool` |  |

#### `rebuild_indexes() -> dict[str, Any]`

Rebuild keyword, vector, and chunk search indexes.

Rebuilds graph node indexes (FTS + vector) and re-indexes all
committed document chunk embeddings into the vector search index.

Returns:
    Dict with rebuild stats for nodes and chunks.

#### `rebuild_with_regeneration(indexing_service: Any = None) -> dict[str, Any]`

Regenerate all embeddings and rebuild search indexes.

When the embedding model or dimensions have changed, stored
embeddings are stale. This method re-runs the indexing pipeline
for each committed source to generate fresh embeddings with
the current model, then rebuilds the vector search index.

Args:
    indexing_service: IndexingService for regenerating chunk
        embeddings. Required for regeneration.

Returns:
    Dict with success, sources_regenerated, total_nodes,
    nodes_with_embeddings, chunks_indexed, message.

| Parameter | Type | Description |
|---|---|---|
| `indexing_service` | `Any` |  |

#### `semantic_search(query: str, limit: int = 10, embedding_provider_callback: Any = None, include_disabled_sources: bool = False) -> dict[str, Any]`

Perform semantic/vector search.

Args:
    query: Search query
    limit: Maximum results
    embedding_provider_callback: Optional callback for generating query embedding.
        Falls back to the default callback injected at construction time.
    include_disabled_sources: If True, include results from disabled sources

Returns:
    Dict with 'data' (list of results) and 'type' (search type)

| Parameter | Type | Description |
|---|---|---|
| `query` | `str` |  |
| `limit` | `int` |  |
| `embedding_provider_callback` | `Any` |  |
| `include_disabled_sources` | `bool` |  |

**Attributes:**

- `database_name`
- `graph_repository`
- `indexing_repository`
- `search_repository`
- `settings`
- `source_repository`
- `sources_repository`

## `chaoscypher_core.services.graph.management.node`

Node Service for chaoscypher-engine.

Business logic for node operations with search integration.

### `class NodeService`

Service for node business logic with search integration.

Orchestrates node CRUD operations across GraphRepository and SearchRepository.
Provides template validation and automatic search indexing.

**Methods:**

#### `create_node(node_create: NodeCreate) -> dict[str, Any]`

Create new node with template validation and search indexing.

Args:
    node_create: Node creation data

Returns:
    Created node dictionary

Raises:
    NotFoundError: If template not found

| Parameter | Type | Description |
|---|---|---|
| `node_create` | `NodeCreate` |  |

#### `delete_node(node_id: str) -> None`

Delete node and remove from search index.

Args:
    node_id: Node ID

Raises:
    NotFoundError: If node not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `from_adapter(adapter: SqliteAdapter, settings: EngineSettings, search_repository: SearchRepositoryProtocol | None = None) -> NodeService`

Create a NodeService from a storage adapter.

Wires the adapter into graph repository and (optionally) search
repository slots. For full search integration, pass a
SearchRepository explicitly.

Args:
    adapter: SqliteAdapter (or compatible) implementing
        GraphRepositoryProtocol.
    settings: Engine settings.
    search_repository: Optional SearchRepository for auto-indexing.
        When omitted, search indexing is skipped.

Returns:
    Configured NodeService.

Example:
    from chaoscypher_core import NodeService, SqliteAdapter, EngineSettings

    adapter = SqliteAdapter("app.db", "default")
    service = NodeService.from_adapter(adapter, EngineSettings())

| Parameter | Type | Description |
|---|---|---|
| `adapter` | `SqliteAdapter` |  |
| `settings` | `EngineSettings` |  |
| `search_repository` | `SearchRepositoryProtocol \| None` |  |

#### `from_engine(engine: Any) -> NodeService`

Create a NodeService wired from an Engine instance.

Args:
    engine: Engine instance with graph_repository, search_repository,
        and settings.

Returns:
    NodeService with all dependencies injected.

| Parameter | Type | Description |
|---|---|---|
| `engine` | `Any` |  |

#### `get_node(node_id: str) -> dict[str, Any]`

Get node by ID.

Args:
    node_id: Node ID

Returns:
    Node dictionary

Raises:
    NotFoundError: If node not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `list_nodes(template_id: str | None = None, source_ids: list[str] | None = None, page: int = 1, page_size: int = 50, minimal: bool = False, include_embedding: bool = True) -> dict[str, Any]`

List nodes with pagination.

Args:
    template_id: Filter by template (optional)
    source_ids: Filter by source document IDs (optional)
    page: Page number (1-indexed)
    page_size: Items per page
    minimal: If True, only load essential fields (excludes embedding, properties)
             for better performance with large graphs
    include_embedding: If True (default), include the embedding vector. List
             views that never use embeddings pass False to skip loading and
             serializing them. Ignored when minimal=True.

Returns:
    Dict with keys:
        - data: List of node dicts
        - pagination: Pagination metadata

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str \| None` |  |
| `source_ids` | `list[str] \| None` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |
| `minimal` | `bool` |  |
| `include_embedding` | `bool` |  |

#### `safe_delete_node_index(node_id: str) -> None`

Remove node from search index with error handling.

Args:
    node_id: Node ID

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `safe_index_node(node_id: str, node: Any) -> None`

Index node in search with error handling.

Args:
    node_id: Node ID
    node: Node object to index

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `node` | `Any` |  |

#### `update_node(node_id: str, node_update: NodeUpdate) -> dict[str, Any]`

Update node and refresh search index.

Args:
    node_id: Node ID
    node_update: Node update data

Returns:
    Updated node dictionary

Raises:
    NotFoundError: If node not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `node_update` | `NodeUpdate` |  |

#### `update_node_position(node_id: str, x: float, y: float) -> dict[str, Any]`

Update only node position (optimized for layout saving).

This operation updates search index but doesn't trigger other events
for performance reasons.

Args:
    node_id: Node ID
    x: X coordinate
    y: Y coordinate

Returns:
    Updated node dictionary

Raises:
    NotFoundError: If node not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `x` | `float` |  |
| `y` | `float` |  |

**Attributes:**

- `graph_repository`
- `search_repository`
- `settings`

## `chaoscypher_core.services.graph.management.edge`

Edge Service for chaoscypher-engine.

Business logic for edge operations - thin wrapper around GraphRepository.

### `class EdgeService`

Service for edge business logic.

Thin wrapper around GraphRepository that provides validation
and standardized error handling for edge operations.

**Methods:**

#### `create_edge(edge_create: EdgeCreate) -> dict[str, Any]`

Create new edge.

Args:
    edge_create: Edge creation data

Returns:
    Created edge dictionary

Raises:
    NotFoundError: If source/target node or template not found
    ValidationError: If template is not an edge template

| Parameter | Type | Description |
|---|---|---|
| `edge_create` | `EdgeCreate` |  |

#### `delete_edge(edge_id: str) -> None`

Delete edge by ID.

Args:
    edge_id: Edge ID

Raises:
    NotFoundError: If edge not found

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `get_edge(edge_id: str) -> dict[str, Any]`

Get edge by ID.

Args:
    edge_id: Edge ID

Returns:
    Edge dictionary

Raises:
    NotFoundError: If edge not found

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `list_edges(source_node_id: str | None = None, target_node_id: str | None = None, source_ids: list[str] | None = None, page: int = 1, page_size: int = 50, minimal: bool = False) -> dict[str, Any]`

List edges with pagination.

Args:
    source_node_id: Filter by source node (optional)
    target_node_id: Filter by target node (optional)
    source_ids: Filter by source document IDs (optional)
    page: Page number (1-indexed)
    page_size: Items per page
    minimal: If True, only load essential fields (excludes properties)
             for better performance with large graphs

Returns:
    Dict with keys:
        - data: List of edge dicts
        - pagination: Pagination metadata (total, page, page_size, etc.)

| Parameter | Type | Description |
|---|---|---|
| `source_node_id` | `str \| None` |  |
| `target_node_id` | `str \| None` |  |
| `source_ids` | `list[str] \| None` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |
| `minimal` | `bool` |  |

#### `update_edge(edge_id: str, edge_update: EdgeUpdate) -> dict[str, Any]`

Update existing edge.

Args:
    edge_id: Edge ID
    edge_update: Edge update data

Returns:
    Updated edge dictionary

Raises:
    NotFoundError: If edge not found

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |
| `edge_update` | `EdgeUpdate` |  |

**Attributes:**

- `graph_repository`

## `chaoscypher_core.services.graph.management.template`

Template Service for chaoscypher-engine.

Business logic for template operations - thin wrapper around GraphRepository.

### `class TemplateService`

Service for template business logic.

Thin wrapper around GraphRepository that provides validation
and standardized error handling for template operations.

**Methods:**

#### `create_template(template_create: TemplateCreate) -> dict[str, Any]`

Create new template.

Args:
    template_create: Template creation data

Returns:
    Created template dictionary

Raises:
    ValidationError: If name uses system prefix

| Parameter | Type | Description |
|---|---|---|
| `template_create` | `TemplateCreate` |  |

#### `delete_template(template_id: str, force: bool = False) -> None`

Delete template.

Args:
    template_id: Template ID
    force: Force delete even if in use

Raises:
    NotFoundError: If template not found

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |
| `force` | `bool` |  |

#### `get_template(template_id: str) -> dict[str, Any]`

Get template by ID.

Args:
    template_id: Template ID

Returns:
    Template dictionary

Raises:
    NotFoundError: If template not found

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |

#### `list_templates(template_type: str | None = None, page: int = 1, page_size: int = 50, source_id: str | None = None) -> dict[str, Any]`

List templates with pagination.

Args:
    template_type: Filter by type (node/edge, optional)
    page: Page number (1-indexed)
    page_size: Items per page
    source_id: Filter by source ID (optional)

Returns:
    Dict with keys:
        - data: List of template dicts
        - pagination: Pagination metadata

| Parameter | Type | Description |
|---|---|---|
| `template_type` | `str \| None` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |
| `source_id` | `str \| None` |  |

#### `update_template(template_id: str, template_update: TemplateUpdate) -> dict[str, Any]`

Update template.

Args:
    template_id: Template ID
    template_update: Template update data

Returns:
    Updated template dictionary

Raises:
    NotFoundError: If template not found
    ValidationError: If name uses system prefix

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |
| `template_update` | `TemplateUpdate` |  |

**Attributes:**

- `graph_repository`

## `chaoscypher_core.services.sources.engine.commit.service`

Source processing Commit Service.

Orchestrates the source processing commit process - converting analyzed entities
and relationships into permanent graph nodes and edges.

Extracted from commit_service.py for SRP compliance.

### `class SourceCommitService`

Orchestrates the source processing commit process.

Coordinates:
- Template creation from suggestions
- Entity node creation with embeddings
- Relationship edge creation
- Source record creation
- Citation tracking
- Chunk promotion and indexing

**Methods:**

#### `commit(file_id: str, commit_data: dict, file_info: dict[str, Any], auto_enable: bool = True) -> dict[str, Any]`

Public commit entry point with retry-on-db-lock.

Structured into three phases:

1. **PREP** (outside transaction): DB reads and LLM-adjacent calls are
   allowed here.  Chunk data is pre-fetched so the write phase only
   performs fast local writes.

2. **WRITE** (inside `adapter.transaction()`): All DB writes from
   `start_commit` through `complete_commit` are grouped into a
   single atomic transaction.  No LLM calls happen in this phase.
   If any write fails the transaction rolls back and the source row
   returns to its pre-commit state (`status='extracted'`), allowing
   the worker retry machinery to re-dispatch cleanly.

3. **POST-TRANSACTION** (outside transaction): Template embedding runs
   after the transaction commits.  It is non-fatal — templates are
   already created; they just won't be semantically searchable until a
   background reindex runs if this step fails.

The actual commit logic lives in `_commit_impl`; this wrapper retries
the whole idempotent operation if `SQLITE_BUSY` fires inside the
`adapter.transaction()` block. `SafeSession` retries the final
commit call, but busy errors can also occur earlier in the
transactional write sequence.

Args:
    file_id: Import file ID
    commit_data: Commit data with entities, relationships, templates
    file_info: Import file info dict
    auto_enable: Whether to enable the source immediately (visible in graph/search)

Returns:
    ImportCommitResult dictionary with created node/edge/template IDs

| Parameter | Type | Description |
|---|---|---|
| `file_id` | `str` |  |
| `commit_data` | `dict` |  |
| `file_info` | `dict[str, Any]` |  |
| `auto_enable` | `bool` |  |

#### `from_engine(engine: Any) -> SourceCommitService`

Create a SourceCommitService wired from an Engine instance.

Args:
    engine: Engine instance with storage_adapter, graph_repository,
        search_repository, and settings.

Returns:
    SourceCommitService with all dependencies injected.

| Parameter | Type | Description |
|---|---|---|
| `engine` | `Any` |  |

**Attributes:**

- `adapter`: `_CommitAdapterProtocol`
- `database_name`
- `entity_handler`
- `graph_repository`
- `indexing_repository`
- `relationship_handler`
- `reload_callback`
- `search_repository`
- `settings`
- `source_repository`
- `sources_repository`
- `template_handler`

### `class SourcesProtocol`

Combined protocol for SourceCommitService — covers CRUD and citations.

**Bases:** `SourceStorageProtocol, CitationStorageProtocol, Protocol`

### `drop_orphan_entities(entities: list[dict], relationships: list[dict], enabled: bool) -> tuple[list[dict], list[dict], int]`

Filter entities not referenced by any relationship, when enabled.

Honors `FilteringConfig.protect_orphans`. Returns
`(kept_entities, remapped_relationships, dropped_count)`
preserving input order.

The upstream extraction pipeline emits relationships keyed by
integer indices into the `entities` list — each relationship
has `source: int` and `target: int` whose values position into
`entities`. The downstream consumers in this commit pipeline
(`commit/relation.py:157` and `commit/service.py:1120`) resolve
endpoints via those indices, so this filter — which runs *before*
node creation, when no entity IDs exist yet — must use the same
contract.

When the filter drops entity at index `k`, every entity after it
shifts down by one. Without remapping, relationships into kept-but-
shifted entities silently disappear at commit time (the index they
reference is now out of range, or — worse — points at the wrong
surviving entity). The fix borrows
`EntityProcessor.remap_relationship_indices` (the canonical remap
pattern already used by dedup and type-rescue).

Malformed endpoints (non-integer `source`/`target`) are dropped
by `remap_relationship_indices` so a typo in one relationship
cannot poison the surviving edge set.

Args:
    entities: Entities pending commit (in extraction order — index
        position is the join key).
    relationships: Relationships pending commit, keyed by integer
        `source`/`target` indices into `entities`.
    enabled: When False, returns `(entities, relationships, 0)`
        unchanged.

Returns:
    Tuple of `(kept_entities, remapped_relationships, dropped_count)`.
    `dropped_count` is the number of orphan entities removed; it
    feeds the `ORPHAN_ENTITIES_FILTERED` quality counter at the
    caller. Relationships into removed entities are filtered out by
    the canonical remap helper (so the returned list is always a
    subset of the input list).

| Parameter | Type | Description |
|---|---|---|
| `entities` | `list[dict]` |  |
| `relationships` | `list[dict]` |  |
| `enabled` | `bool` |  |

### `normalize_relationship_endpoints(entities: list[dict], relationships: list[dict]) -> list[dict]`

Convert string entity-id `source`/`target` endpoints to integer indices.

The commit contract (`drop_orphan_entities` and `relation.py`'s
`_resolve_node_ids` index path) expects each relationship's `source` /
`target` to be an INTEGER index into `entities`. The in-memory
finalizer path honors that. But the relational-store reload path —
`list_source_relationships` → `_relationship_row_to_dict` (migration
0042) — projects `source`/`target` as STRING entity IDs
(`source_entity_id` / `target_entity_id`). Feeding those straight to
commit made the integer check in `drop_orphan_entities` fail for every
relationship, so the referenced set was empty and 100% of entities were
dropped as false orphans (empty graph for every committed source via the
CLI `source add` and recovery re-commit paths).

This maps id-keyed endpoints to the entity's position in `entities`.
Integer endpoints pass through unchanged, so the finalizer path is a
no-op. An endpoint id not present in `entities` is left as-is — a single
dangling reference must not silently re-index the whole relationship.

Args:
    entities: Commit entities in order (position is the index contract).
    relationships: Relationships that may key endpoints by string id.

Returns:
    A new relationship list with string-id `source`/`target` rewritten
    to integer indices; every other key (including `from`/`to` names)
    is preserved.

| Parameter | Type | Description |
|---|---|---|
| `entities` | `list[dict]` |  |
| `relationships` | `list[dict]` |  |

## `chaoscypher_core.utils.chunk`

Chunking Service for chaoscypher-engine.

Hierarchical document chunking:
1. Small chunks (~900 chars) for RAG retrieval
2. Grouped chunks (4x small, ~900 tokens) for entity extraction

Research-based defaults (GraphRAG paper, RAG best practices 2025):
- 900-char small chunks (~225 tokens) for balanced RAG retrieval and extraction
- 4 chunks per group (~900 tokens after overlap) optimal for entity extraction
- 150-char overlap (~16%) within optimal 10-20% range

Single source of truth for all document chunking.

### `class ChunkingService`

Hierarchical document chunking service.

Creates two levels of chunks:
- Small chunks: Optimal for RAG retrieval (~900 chars, sentence boundaries)
- Grouped chunks: Optimal for extraction (4 small chunks combined, ~900 tokens)

Uses fixed group_size based on GraphRAG research showing 600-900 token chunks
are optimal for entity detection.

**Methods:**

#### `create_chunks(full_text: str, source_id: str | None = None, analysis_depth: str = 'full', store: bool | None = None, original_text: str | None = None, location_index: LocationIndex | None = None) -> ChunksResult`

Create hierarchical chunks from document text with filtering.

Optionally persists chunks to storage after creation.  When *store*
is `None` (default), chunks are stored automatically if a repository
is available.  Pass `store=False` to inspect or transform chunks
before writing.

Process:
1. Split text into ALL small chunks (~900 chars, sentence boundaries)
2. Create ALL hierarchical groups (4 small chunks per group)
3. Filter based on analysis_depth (quick=5 groups / full=all)
4. (Phase 5a) Recompute char offsets against `original_text` when
   provided so citation anchors reference the raw upload, not the
   post-cleaner text.

Args:
    full_text: Full document text (post-normalization).
    source_id: ID of the source. Auto-generated UUID if not provided.
    analysis_depth: 'quick' | 'full'.
    store: Persist chunks to storage after creation. Defaults to True
        when a repository is available, False otherwise. Set to False
        to inspect chunks before storing.
    original_text: Raw loader output *before* normalization.  When
        supplied, each chunk's `char_start` / `char_end` are
        recomputed against this text via substring search (method
        `'exact'`) or rapidfuzz fuzzy match (method `'fuzzy'`).
        Chunks that cannot be located receive NULL offsets and method
        `'none'`.  When `None` the existing offset values computed
        against the cleaned text are kept and tagged `'exact'` (the
        pre-Phase-5a behaviour — slightly inaccurate but consistent
        with what was shipped before this phase).
    location_index: Optional per-document location index built by
        the loader. Each boundary maps a char range to a
        `page_number` and/or `section`. Coordinates are in
        the loader-content coordinate system (≈ raw-upload). The
        lookup runs after Phase 5a so chunk char_start aligns
        with the index. When None, page_number and section
        stay None on every chunk.

Returns:
    ChunksResult with small_chunks, hierarchical_groups, and counts.

| Parameter | Type | Description |
|---|---|---|
| `full_text` | `str` |  |
| `source_id` | `str \| None` |  |
| `analysis_depth` | `str` |  |
| `store` | `bool \| None` |  |
| `original_text` | `str \| None` |  |
| `location_index` | `LocationIndex \| None` |  |

#### `get_hierarchical_groups(source_id: str, analysis_depth: str) -> list[dict[str, Any]]`

Get hierarchical groups for entity extraction.

Args:
    source_id: Source ID
    analysis_depth: 'quick' | 'full'

Returns:
    Subset of groups based on analysis depth

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `analysis_depth` | `str` |  |

#### `get_small_chunks(source_id: str) -> list[dict[str, Any]]`

Get all small chunks for a source (for RAG indexing).

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `process(text: str, analysis_depth: str = 'full', file_info: dict[str, Any] | None = None, embedding_service: Any = None) -> ExtractionResult`

Chunk text and extract entities in one call.

Convenience method that combines `create_chunks` and
entity extraction into a single call.  Useful for standalone
extraction without a database.

Args:
    text: Document text to process.
    analysis_depth: Extraction depth (`"full"` or `"quick"`).
    file_info: Optional file metadata for domain detection.
    embedding_service: Embedding provider for semantic deduplication.
        When `None` (default), one is lazily constructed from
        `self.settings` so semantic dedup runs by default. Pass an
        explicit instance to inject (e.g., for tests) or pass an
        already-constructed provider to share state across calls.

Returns:
    ExtractionResult with `entities`, `relationships`, `domain`,
    `domain_confidence`, and other extraction results. Call
    `model_dump_json()` for JSON output.

Example:
    >>> service = ChunkingService(settings=EngineSettings())
    >>> result = await service.process("Your document text here...")
    >>> print(result.model_dump_json(indent=2))

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |
| `analysis_depth` | `str` |  |
| `file_info` | `dict[str, Any] \| None` |  |
| `embedding_service` | `Any` |  |

#### `store_chunks(chunks_result: Any, database_name: str | None = None) -> None`

Persist chunks to storage with database metadata.

Accepts the ChunksResult from `create_chunks` directly.
Stamps storage fields (database_name, embedding placeholders, status,
created_at) onto chunks, then calls repository.store_chunks_and_groups().

Args:
    chunks_result: ChunksResult from create_chunks().
    database_name: Database name for storage metadata. If None,
        uses `settings.current_database`.

Raises:
    RuntimeError: If no repository was provided to the constructor.

| Parameter | Type | Description |
|---|---|---|
| `chunks_result` | `Any` |  |
| `database_name` | `str \| None` |  |

**Attributes:**

- `group_overlap`
- `group_size`
- `max_chunk_size`
- `min_chunk_size`
- `normalize_newlines`
- `normalize_remove_structural_noise`
- `quick_mode_max_groups`
- `repository`
- `respect_boundaries`
- `settings`
- `small_chunk_overlap`
- `small_chunk_size`

### `class LocationBoundary`

One char-range entry in a LocationIndex.

Either `page_number` or `section` (or both) is set per entry; the
unset field is `None`. Each entry's `[start_char, end_char)` range
is half-open — `end_char` is exclusive.

Coordinates match the loader's `content` field (pre-normalization),
which approximates raw-upload coordinates. The chunker's lookup runs
after Phase 5a (`_recompute_chunk_offsets`) so the coordinate
systems align when `original_text` is provided.

**Bases:** `TypedDict`

**Attributes:**

- `end_char`: `int`
- `page_number`: `int | None`
- `section`: `str | None`
- `start_char`: `int`

### `build_pdf_location_index(page_texts: list[str], separator: str = '\n\n') -> LocationIndex`

Build a LocationIndex covering joined per-page text.

Each entry maps the char range of one page (in the joined text) to
its 1-based page_number. `section` is always None for PDF pages.

This is the single source of truth for "how many chars does each page
occupy in the joined content". Used by:
- PDF loader, to emit the initial location_index alongside _page_texts
- Orchestrators (indexing_handler, CLI), to REBUILD the index from
  the current _page_texts before chunking — vision_finalizer mutates
  _page_texts in place after the loader runs (appending visual-content
  descriptions), so the loader's original location_index goes stale
  whenever vision processing fires. Rebuilding closes that gap.

| Parameter | Type | Description |
|---|---|---|
| `page_texts` | `list[str]` |  |
| `separator` | `str` |  |

### `merge_location_indexes(docs_with_indexes: list[tuple[str, LocationIndex | None]], separator: str = '\n\n') -> LocationIndex`

Merge per-document location indexes into one covering the joined text.

The orchestrator joins multiple loader documents into a single
`full_text` using `separator` (default `"\n\n"` — matches
indexing_handler._extract_text). Each document's location_index is
in its own local coordinates; this helper shifts them to align with
the joined text.

Documents whose loader didn't emit a location_index contribute
nothing to the merge but still advance the cumulative offset
(their content still occupies space in the joined text).

| Parameter | Type | Description |
|---|---|---|
| `docs_with_indexes` | `list[tuple[str, LocationIndex \| None]]` |  |
| `separator` | `str` |  |

## `chaoscypher_core.bootstrap`

Bootstrap - Unified dependency injection for Chaos Cypher.

Provides a single entry point for initializing the service layer,
regardless of whether running in web, CLI, or worker context.

This module uses lazy imports to keep startup fast.

Usage:
    from chaoscypher_core import Engine

    with Engine("./data/databases/default", initialize_db=True) as engine:
        # Graph services
        nodes = engine.node_service.list_nodes()
        templates = engine.template_service.list_templates()

        # Pipeline services
        chunks = await engine.chunking_service.create_chunks(
            source_id="src_001", full_text="..."
        )
        await engine.indexing_service.create_index(source_id="src_001")
        results = engine.search_service.keyword_search("query")

        # LLM and extraction (lazy - initialized on first access)
        response = await engine.llm_provider.chat(messages=[...])
        extraction = await engine.extraction_service.finalize_distributed_extraction(...)
        commit = await engine.commit_service.commit(...)

### `class Engine`

Chaos Cypher Engine - Single entry point for all services.

Wires up storage adapters, repositories, and services with proper
dependency injection. Use as a context manager or call close() when done.

Args:
    data_dir: Path to database directory (e.g., "./data/databases/default").
    database: Database name shorthand (inferred from data_dir if not provided).
    settings: Optional pre-configured EngineSettings. When provided, used
        instead of creating a default instance. `current_database` and
        `paths.data_dir` are still set from the other arguments.
    initialize_db: When True, create tables before connecting. Useful for
        CLI and first-run scenarios where the database may not exist yet.

Attributes:
    database_name: Name of the current database.
    data_dir: Path to the database directory.
    settings: Engine settings instance.
    storage_adapter: SQLite storage adapter.
    graph_repository: Knowledge graph repository.
    search_repository: Search/vector repository.
    node_service: Node CRUD service.
    edge_service: Edge CRUD service.
    template_service: Template CRUD service.
    workflow_service: Workflow CRUD service.
    chunking_service: Document chunking service.
    indexing_service: Embedding generation service.
    search_service: Keyword, semantic, and hybrid search service.
    llm_provider: Queue-free LLM provider (lazy, initialized on first access).
    extraction_service: Entity extraction service (lazy, initialized on first access).
    commit_service: Source commit service (lazy, initialized on first access).

Convenience methods return domain models with attribute access:
    add_node / add_edge: Quick graph building with get-or-create templates.
    create_template / get_template / list_templates / update_template / delete_template
    create_node / get_node / list_nodes / update_node / delete_node
    create_edge / get_edge / list_edges / update_edge / delete_edge
    chunk_document: Chunk text and store for RAG search.
    commit: Extract entities from chunks and write to graph.
    process_document / add_document / add_documents: Full extraction pipeline.
    search: Hybrid, semantic, or keyword search.
    index_source: Generate embeddings for a source's chunks.
    rebuild_indexes: Rebuild all search indexes.

Synchronous wrappers (for scripts, notebooks, non-async contexts):
    search_sync, chat_sync, embed_sync, batch_embed_sync,
    add_document_sync, add_documents_sync, process_document_sync

Example:
    # Minimal — database name + optional inline configuration
    with Engine(database="demo", provider="openai", api_key="sk-...") as engine:
        alice = engine.add_node("Person", "Alice")
        bob = engine.add_node("Person", "Bob")
        engine.add_edge("knows", alice, bob)
        results = engine.search_sync("people")

**Methods:**

#### `add_document(filepath: str | Path, source_id: str | None = None, analysis_depth: AnalysisDepth = 'full', on_progress: ProgressCallback | None = None, auto_confirm: bool = True, forced_domain: str | None = None) -> ProcessingResult`

Load a file and process it through the full extraction pipeline.

Convenience method that combines Loaders.load_text() with
process_document(). Loads any supported file type, then chunks,
indexes, extracts entities, and commits to the graph.

Args:
    filepath: Path to the document file. Supports PDF, text, CSV,
        JSON, audio, video, image, and archive formats.
    source_id: Identifier for this source. Auto-generated if omitted.
    analysis_depth: Extraction depth — 'full' (default) or 'quick'.
    on_progress: Unified callback invoked after each pipeline stage.
        Receives `(stage, result)` where stage is `"chunking"`,
        `"indexing"`, or `"extraction"`.
    auto_confirm: Bypass the domain-confirmation gate (default
        `True`). Forwarded to `process_document`; the MCP
        server-extraction path passes `False` to opt into the gate.
    forced_domain: Explicit domain choice (suppresses parking).

Returns:
    ProcessingResult with source_id and lists of created node,
    edge, and template IDs. When the gate parks the source, `status`
    is `awaiting_confirmation` and no entities were extracted.

Example:
    with Engine("./data/databases/demo") as engine:
        result = await engine.add_document("paper.pdf")
        print(f"Created \{len(result.nodes)\} nodes")

| Parameter | Type | Description |
|---|---|---|
| `filepath` | `str \| Path` |  |
| `source_id` | `str \| None` |  |
| `analysis_depth` | `AnalysisDepth` |  |
| `on_progress` | `ProgressCallback \| None` |  |
| `auto_confirm` | `bool` |  |
| `forced_domain` | `str \| None` |  |

#### `add_document_sync(filepath: str | Path, source_id: str | None = None, analysis_depth: AnalysisDepth = 'full', on_progress: ProgressCallback | None = None) -> ProcessingResult`

Synchronous wrapper for `add_document`.

Args:
    filepath: Path to the document file.
    source_id: Identifier for this source. Auto-generated if omitted.
    analysis_depth: Extraction depth — 'full' or 'quick'.
    on_progress: Callback invoked after each pipeline stage.

Returns:
    ProcessingResult with source_id and created entity IDs.

Example:
    with Engine(database="demo") as engine:
        result = engine.add_document_sync("paper.pdf")
        print(f"Created \{len(result.nodes)\} nodes")

| Parameter | Type | Description |
|---|---|---|
| `filepath` | `str \| Path` |  |
| `source_id` | `str \| None` |  |
| `analysis_depth` | `AnalysisDepth` |  |
| `on_progress` | `ProgressCallback \| None` |  |

#### `add_documents(paths: str | list[str | Path], on_document_complete: Callable[[str, ProcessingResult], None] | None = None) -> list[ProcessingResult]`

Load and process multiple documents.

Accepts a glob pattern (e.g., `"docs/*.pdf"`) or a list of file
paths. Documents are processed sequentially.

Args:
    paths: Glob pattern string or list of file paths.
    on_document_complete: Optional callback invoked after each
        document (receives filename and ProcessingResult).

Returns:
    List of ProcessingResult models, one per document.

Example:
    results = await engine.add_documents("papers/*.pdf")
    print(f"Processed \{len(results)\} documents")

| Parameter | Type | Description |
|---|---|---|
| `paths` | `str \| list[str \| Path]` |  |
| `on_document_complete` | `Callable[[str, ProcessingResult], None] \| None` |  |

#### `add_documents_sync(paths: str | list[str | Path], on_document_complete: Callable[[str, ProcessingResult], None] | None = None) -> list[ProcessingResult]`

Synchronous wrapper for `add_documents`.

Args:
    paths: Glob pattern string or list of file paths.
    on_document_complete: Callback invoked after each document.

Returns:
    List of ProcessingResult models, one per document.

Example:
    with Engine(database="demo") as engine:
        results = engine.add_documents_sync(["doc1.pdf", "doc2.pdf"])
        print(f"Processed \{len(results)\} documents")

| Parameter | Type | Description |
|---|---|---|
| `paths` | `str \| list[str \| Path]` |  |
| `on_document_complete` | `Callable[[str, ProcessingResult], None] \| None` |  |

#### `add_edge(template_name: str, source: Node | str, target: Node | str, label: str | None = None, properties: dict[str, Any] | None = None, source_id: str | None = None) -> Edge`

Create an edge with get-or-create template semantics.

If an edge template with `template_name` doesn't exist, it is
created automatically. Accepts Node models or string IDs for
source and target.

Args:
    template_name: Name of the edge template (e.g., 'knows').
    source: Source node (Node model or node ID string).
    target: Target node (Node model or node ID string).
    label: Edge label. Defaults to `template_name` if omitted.
    properties: Optional edge properties dict.
    source_id: Optional source document ID.

Returns:
    Created Edge model with attribute access.

Example:
    engine.add_edge("knows", alice, bob)
    engine.add_edge("works_at", alice, "node_id_123", label="employed by")

| Parameter | Type | Description |
|---|---|---|
| `template_name` | `str` |  |
| `source` | `Node \| str` |  |
| `target` | `Node \| str` |  |
| `label` | `str \| None` |  |
| `properties` | `dict[str, Any] \| None` |  |
| `source_id` | `str \| None` |  |

#### `add_node(template_name: str, label: str, properties: dict[str, Any] | None = None, source_id: str | None = None) -> Node`

Create a node with get-or-create template semantics.

If a node template with `template_name` doesn't exist, it is
created automatically. Subsequent calls with the same name reuse
the existing template (cached per Engine instance).

Args:
    template_name: Name of the node template (e.g., 'Person').
    label: Node label.
    properties: Optional node properties dict.
    source_id: Optional source document ID.

Returns:
    Created Node model with attribute access.

Example:
    alice = engine.add_node("Person", "Alice", properties=\{"role": "Engineer"\})
    print(alice.id, alice.label)

| Parameter | Type | Description |
|---|---|---|
| `template_name` | `str` |  |
| `label` | `str` |  |
| `properties` | `dict[str, Any] \| None` |  |
| `source_id` | `str \| None` |  |

#### `batch_embed(texts: list[str], kwargs: Any = {}) -> BatchEmbedResult`

Generate vector embeddings for multiple texts.

Args:
    texts: List of texts to embed.
    **kwargs: Forwarded to embedding provider's batch_embed().

Returns:
    BatchEmbedResult with embeddings list, counts, and provider.

| Parameter | Type | Description |
|---|---|---|
| `texts` | `list[str]` |  |
| `kwargs` | `Any` |  |

#### `batch_embed_sync(texts: list[str], kwargs: Any = {}) -> BatchEmbedResult`

Synchronous wrapper for `batch_embed`.

Args:
    texts: List of texts to embed.
    **kwargs: Forwarded to embedding provider.

Returns:
    BatchEmbedResult with embeddings list, counts, and provider.

| Parameter | Type | Description |
|---|---|---|
| `texts` | `list[str]` |  |
| `kwargs` | `Any` |  |

#### `chat(messages: str | list[dict[str, Any]], stream: bool = False, kwargs: Any = {}) -> LLMChatResponse`

Send a chat message to the configured LLM provider.

Accepts a plain string (auto-wrapped as a user message) or a full
message list for multi-turn conversations.

Args:
    messages: A string prompt or list of message dicts
        (`[{"role": "user", "content": "..."}]`).
    stream: Whether to stream the response.
    **kwargs: Forwarded to LLMProvider.chat() (temperature,
        max_tokens, enable_thinking, etc.).

Returns:
    LLMChatResponse with content, tool_calls, usage, and provider info.

Example:
    response = await engine.chat("What is a knowledge graph?")
    print(response.content)

| Parameter | Type | Description |
|---|---|---|
| `messages` | `str \| list[dict[str, Any]]` |  |
| `stream` | `bool` |  |
| `kwargs` | `Any` |  |

#### `chat_sync(messages: str | list[dict[str, Any]], stream: bool = False, kwargs: Any = {}) -> LLMChatResponse`

Synchronous wrapper for `chat`.

Args:
    messages: A string prompt or list of message dicts.
    stream: Whether to stream the response.
    **kwargs: Forwarded to LLMProvider.chat().

Returns:
    LLMChatResponse with content, tool_calls, usage, and provider info.

Example:
    with Engine(database="demo") as engine:
        response = engine.chat_sync("What is a knowledge graph?")
        print(response.content)

| Parameter | Type | Description |
|---|---|---|
| `messages` | `str \| list[dict[str, Any]]` |  |
| `stream` | `bool` |  |
| `kwargs` | `Any` |  |

#### `check_health() -> HealthReport`

Check health of configured LLM providers.

Verifies that chat and embedding providers are reachable
and functioning correctly.

Returns:
    HealthReport with chat and embedding health results.

Example:
    health = await engine.check_health()
    if health.chat.status == "healthy":
        print(f"Chat OK (\{health.chat.response_time_ms\}ms)")

#### `chunk_document(text: str, source_id: str | None = None, analysis_depth: AnalysisDepth = 'full') -> ChunkingResult`

Chunk document text and store for RAG search.

Splits text into small RAG chunks and hierarchical groups, then
persists them to storage. Use the returned `source_id` for
subsequent `index_source()` or `commit()` calls.

Args:
    text: Document text to chunk.
    source_id: Identifier for this source. Auto-generated if omitted.
    analysis_depth: 'full' (all chunks) or 'quick' (sampled subset).

Returns:
    ChunkingResult with source_id, chunk counts, and analysis depth.

Example:
    chunks = await engine.chunk_document(text)
    index = await engine.index_source(chunks.source_id)

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |
| `source_id` | `str \| None` |  |
| `analysis_depth` | `AnalysisDepth` |  |

#### `close() -> None`

Disconnect adapters and cleanup resources.

Safe to call multiple times - subsequent calls are no-ops.

#### `commit(source_id: str, filename: str = 'document.txt', analysis_depth: AnalysisDepth = 'full') -> ProcessingResult`

Extract entities from stored chunks and commit to the knowledge graph.

Orchestrates entity extraction, deduplication, template matching,
and graph write for a source that has already been chunked via
`chunk_document()`.

Internally reconstructs document text from stored chunks, runs
the extraction pipeline, and commits the results.

Args:
    source_id: Source identifier (from `chunk_document().source_id`).
    filename: Original filename for domain detection and metadata.
    analysis_depth: Extraction depth — 'full' (default) or 'quick'.

Returns:
    ProcessingResult with lists of created node, edge, and template IDs.

Example:
    chunks = await engine.chunk_document(text)
    await engine.index_source(chunks.source_id)
    result = await engine.commit(chunks.source_id)

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `filename` | `str` |  |
| `analysis_depth` | `AnalysisDepth` |  |

#### `create_edge(edge_create: EdgeCreate) -> Edge`

Create an edge between two nodes.

Args:
    edge_create: Edge creation data.

Returns:
    Created Edge with attribute access (e.g., `edge.id`).

Raises:
    NotFoundError: If source/target node not found.
    ValidationError: If source equals target.

| Parameter | Type | Description |
|---|---|---|
| `edge_create` | `EdgeCreate` |  |

#### `create_node(node_create: NodeCreate) -> Node`

Create a node with template validation and search indexing.

Args:
    node_create: Node creation data.

Returns:
    Created Node with attribute access (e.g., `node.id`).

Raises:
    NotFoundError: If template not found.

| Parameter | Type | Description |
|---|---|---|
| `node_create` | `NodeCreate` |  |

#### `create_template(template_create: TemplateCreate) -> Template`

Create a template and return a Template model.

Args:
    template_create: Template creation data.

Returns:
    Created Template with attribute access (e.g., `template.id`).

| Parameter | Type | Description |
|---|---|---|
| `template_create` | `TemplateCreate` |  |

#### `delete_edge(edge_id: str) -> None`

Delete an edge.

Args:
    edge_id: Edge identifier.

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `delete_node(node_id: str) -> None`

Delete a node and remove from search index.

Args:
    node_id: Node identifier.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `delete_template(template_id: str) -> None`

Delete a template.

Args:
    template_id: Template identifier.

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |

#### `embed(text: str, kwargs: Any = {}) -> EmbedResult`

Generate a vector embedding for text.

Args:
    text: Text to embed.
    **kwargs: Forwarded to embedding provider's embed().

Returns:
    EmbedResult with embedding vector, provider, and usage.

Example:
    result = await engine.embed("quantum entanglement")
    print(f"Dimensions: \{len(result.embedding)\}")

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |
| `kwargs` | `Any` |  |

#### `embed_sync(text: str, kwargs: Any = {}) -> EmbedResult`

Synchronous wrapper for `embed`.

Args:
    text: Text to embed.
    **kwargs: Forwarded to embedding provider.

Returns:
    EmbedResult with embedding vector, provider, and usage.

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |
| `kwargs` | `Any` |  |

#### `get_edge(edge_id: str) -> Edge`

Get an edge by ID.

Args:
    edge_id: Edge identifier.

Returns:
    Edge model.

Raises:
    NotFoundError: If edge not found.

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `get_node(node_id: str) -> Node`

Get a node by ID.

Args:
    node_id: Node identifier.

Returns:
    Node model.

Raises:
    NotFoundError: If node not found.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `get_stats() -> DatabaseStats`

Get database statistics.

Returns:
    DatabaseStats model with node, edge, template counts.

#### `get_template(template_id: str) -> Template`

Get a template by ID.

Args:
    template_id: Template identifier.

Returns:
    Template model.

Raises:
    NotFoundError: If template not found.

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |

#### `index_source(source_id: str) -> IndexingResult`

Generate embeddings for all chunks of a source document.

Wraps `indexing_service.create_index()` and returns a typed
result model.

Args:
    source_id: Source document identifier.

Returns:
    IndexingResult with chunks_count, embedding_model, and
    embedding_dimensions.

Raises:
    NotFoundError: If no chunks exist for the given source_id.
        Call `chunk_document()` first, or use `add_document()`
        for the full pipeline.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `list_edges(source_node_id: str | None = None, target_node_id: str | None = None, page: int = 1, page_size: int = 50) -> PaginatedResult`

List edges with pagination.

Args:
    source_node_id: Filter by source node (optional).
    target_node_id: Filter by target node (optional).
    page: Page number (1-based).
    page_size: Items per page.

Returns:
    PaginatedResult containing Edge models.

| Parameter | Type | Description |
|---|---|---|
| `source_node_id` | `str \| None` |  |
| `target_node_id` | `str \| None` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |

#### `list_nodes(template_id: str | None = None, source_ids: list[str] | None = None, page: int = 1, page_size: int = 50) -> PaginatedResult`

List nodes with pagination.

Args:
    template_id: Filter by template (optional).
    source_ids: Filter by source document IDs (optional).
    page: Page number (1-based).
    page_size: Items per page.

Returns:
    PaginatedResult containing Node models.

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str \| None` |  |
| `source_ids` | `list[str] \| None` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |

#### `list_templates(template_type: str | None = None, page: int = 1, page_size: int = 50) -> PaginatedResult`

List templates with pagination.

Args:
    template_type: Filter by 'node' or 'edge' (optional).
    page: Page number (1-based).
    page_size: Items per page.

Returns:
    PaginatedResult containing Template models.

| Parameter | Type | Description |
|---|---|---|
| `template_type` | `str \| None` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |

#### `process_document(text: str, source_id: str | None = None, filename: str = 'document.txt', analysis_depth: AnalysisDepth = 'full', on_progress: ProgressCallback | None = None, auto_confirm: bool = True, forced_domain: str | None = None) -> ProcessingResult`

Process a document through the full extraction pipeline.

Chunks the text, stores and indexes chunks for RAG search,
extracts entities and relationships using AI, and commits
them to the knowledge graph.

Args:
    text: Document text to process. To load from a file first,
        use `Loaders.load_text(filepath)` then pass the result.
    source_id: Identifier for this source document. Auto-generated
        if not provided.
    filename: Original filename (used for domain detection and
        source metadata).
    analysis_depth: Extraction depth — 'full' (default, all chunks)
        or 'quick' (samples ~5 chunk groups, ~5x faster).
    on_progress: Unified callback invoked after each pipeline stage.
        Receives `(stage, result)` where stage is `"chunking"`,
        `"indexing"`, or `"extraction"`.
    auto_confirm: Whether to bypass the domain-confirmation gate.
        Defaults to `True` so the direct-SDK path extracts
        immediately as before (no source row is parked). The MCP
        server-extraction path forwards `False` to opt INTO the
        gate, which persists `confirmation_required=True` and parks
        an auto-detected source at `awaiting_confirmation` between
        the index and extraction stages.
    forced_domain: Explicit human domain choice. When set, the gate
        always proceeds (a forced domain is never parked).

Returns:
    ProcessingResult model with `source_id`, `nodes`, `edges`,
    `templates` listing the IDs of created graph entities. When the
    gate parks the source, the result's `status` is
    `awaiting_confirmation` and `nodes`/`edges`/`templates`
    are empty (extraction did not run).

Example:
    with Engine("./data/databases/demo", initialize_db=True) as engine:
        result = await engine.process_document(text, filename="paper.pdf")
        print(f"Created \{len(result.nodes)\} nodes")

        # With progress tracking:
        def on_progress(stage, result):
            print(f"[\{stage\}] done")

        result = await engine.process_document(
            text, filename="paper.pdf", on_progress=on_progress
        )

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |
| `source_id` | `str \| None` |  |
| `filename` | `str` |  |
| `analysis_depth` | `AnalysisDepth` |  |
| `on_progress` | `ProgressCallback \| None` |  |
| `auto_confirm` | `bool` |  |
| `forced_domain` | `str \| None` |  |

#### `process_document_sync(text: str, source_id: str | None = None, filename: str = 'document.txt', analysis_depth: AnalysisDepth = 'full', on_progress: ProgressCallback | None = None) -> ProcessingResult`

Synchronous wrapper for `process_document`.

Args:
    text: Document text to process.
    source_id: Identifier for this source. Auto-generated if omitted.
    filename: Original filename for domain detection.
    analysis_depth: Extraction depth — 'full' or 'quick'.
    on_progress: Callback invoked after each pipeline stage.

Returns:
    ProcessingResult with source_id and created entity IDs.

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |
| `source_id` | `str \| None` |  |
| `filename` | `str` |  |
| `analysis_depth` | `AnalysisDepth` |  |
| `on_progress` | `ProgressCallback \| None` |  |

#### `rebuild_indexes() -> RebuildResult`

Rebuild all keyword, vector, and chunk search indexes.

Rebuilds graph node indexes (FTS + vector) and re-indexes all
committed document chunk embeddings into the vector search index.

Returns:
    RebuildResult with total_nodes, nodes_with_embeddings,
    and chunks_indexed counts.

#### `search(query: str, limit: int = 10, mode: SearchMode = 'hybrid') -> list[EngineSearchResult]`

Search the knowledge graph and document chunks.

Convenience method that runs hybrid search (semantic + keyword
fallback) by default. Returns flat EngineSearchResult models with
consistent fields regardless of result type.

Args:
    query: Search query string.
    limit: Maximum number of results.
    mode: Search mode — 'hybrid' (default), 'semantic', or 'keyword'.

Returns:
    List of EngineSearchResult models sorted by relevance score.

Example:
    results = await engine.search("quantum entanglement")
    for r in results:
        print(f"\{r.label\} (\{r.score:.2f\})")

| Parameter | Type | Description |
|---|---|---|
| `query` | `str` |  |
| `limit` | `int` |  |
| `mode` | `SearchMode` |  |

#### `search_sync(query: str, limit: int = 10, mode: SearchMode = 'hybrid') -> list[EngineSearchResult]`

Synchronous wrapper for `search`.

Runs the async search in a new event loop. For use in scripts,
notebooks, and non-async contexts.

Args:
    query: Search query string.
    limit: Maximum number of results.
    mode: Search mode — 'hybrid' (default), 'semantic', or 'keyword'.

Returns:
    List of EngineSearchResult models sorted by relevance score.

Example:
    with Engine(database="demo") as engine:
        results = engine.search_sync("quantum entanglement")
        for r in results:
            print(f"\{r.label\} (\{r.score:.2f\})")

| Parameter | Type | Description |
|---|---|---|
| `query` | `str` |  |
| `limit` | `int` |  |
| `mode` | `SearchMode` |  |

#### `update_edge(edge_id: str, edge_update: EdgeUpdate) -> Edge`

Update an edge.

Args:
    edge_id: Edge identifier.
    edge_update: Fields to update.

Returns:
    Updated Edge model.

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |
| `edge_update` | `EdgeUpdate` |  |

#### `update_node(node_id: str, node_update: NodeUpdate) -> Node`

Update a node.

Args:
    node_id: Node identifier.
    node_update: Fields to update.

Returns:
    Updated Node model.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `node_update` | `NodeUpdate` |  |

#### `update_template(template_id: str, template_update: TemplateUpdate) -> Template`

Update a template.

Args:
    template_id: Template identifier.
    template_update: Fields to update.

Returns:
    Updated Template model.

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |
| `template_update` | `TemplateUpdate` |  |

**Attributes:**

- `chunking_service`: `ChunkingService`
- `commit_service`: `SourceCommitService` — Source commit service for writing extraction results to the graph.

Lazily initialized on first access. Orchestrates template creation,
node/edge creation, citation tracking, and search indexing.

Returns:
    SourceCommitService instance wired with engine dependencies.
- `data_dir`: `Path`
- `database_name`: `str`
- `edge_service`: `EdgeService`
- `embedding_provider`: `EmbeddingProviderProtocol` — Convenience alias for `embedding_service`.

Named to match the `EmbeddingProviderProtocol` port terminology
used by services migrated in Phase 2. Returns the same instance
as `embedding_service`.
- `embedding_service`: `EmbeddingProviderProtocol`
- `extraction_service`: `ExtractionService` — Entity extraction service for deduplication and template matching.

Lazily initialized on first access. Requires LLM provider for
AI-powered operations (embedding generation, deduplication).

Returns:
    ExtractionService instance wired with engine dependencies.
- `graph_repository`: `GraphRepository`
- `indexing_service`: `IndexingService`
- `llm_provider`: `LLMProvider` — Queue-free LLM provider for chat, embeddings, and tool execution.

Lazily initialized on first access to avoid startup cost for
graph-only usage. Uses empty managers dict (no tool execution
support). For tool execution, create an LLMProvider manually
with appropriate managers.

Returns:
    LLMProvider instance wired with engine settings.
- `node_service`: `NodeService`
- `retry_policy`: `RetryPolicyPort` — Shared `RetryPolicyPort` instance for SQLite-lock-sensitive work.

Lazily constructs a `DbLockRetryPolicy` on first access and
caches it. Services that accept a `RetryPolicyPort` via DI
receive this instance when constructed through the Engine.

Returns:
    The shared retry policy.
- `search_repository`: `SearchRepository`
- `search_service`: `SearchService`
- `settings`: `EngineSettings`
- `storage_adapter`: `SqliteAdapter`
- `template_service`: `TemplateService`
- `workflow_service`: `WorkflowService`
