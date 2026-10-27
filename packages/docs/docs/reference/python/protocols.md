---
title: "Protocols"
---

# Protocols

Port interfaces (Python Protocols) that define contracts for storage, graph, search, and other operations. These are the hexagonal architecture boundaries that adapters must implement.

## `chaoscypher_core.ports.chunk`

Chunking protocol interface for chaoscypher-engine.

Defines Protocol for hierarchical chunk operations.
Used by ExtractionService and ChunkingService for smart document chunking.
Main app implements this via an adapter that wraps its chunking repository.

### `class ChunkingProtocol`

Interface for chunking operations.

Provides access to hierarchical chunk groups for
smart entity extraction. Hierarchical groups combine
small RAG chunks into larger semantic units.

Used by:
- ExtractionService: Prefer hierarchical chunking over legacy text splitting
- ChunkingService: Store and retrieve chunks for import files

**Bases:** `Protocol`

**Methods:**

#### `get_hierarchical_groups(source_id: str) -> list[dict[str, Any]]`

Get hierarchical chunk groups for a source.

Hierarchical groups combine small chunks into larger
semantic units for better entity extraction. Each group
represents a semantic section (paragraph, heading + content, etc.)

Args:
    source_id: Source identifier

Returns:
    List of group dicts with keys:
        - id, group_index, small_chunk_ids
        - combined_content, char_start, char_end, token_count

Notes:
    - Groups are ordered by group_index
    - Each group references 3+ small chunks
    - Used by ExtractionService for entity extraction

Example:

```
groups = chunking_repo.get_hierarchical_groups("source_123")

if groups:
    print(f"Using {len(groups)} hierarchical groups")
    for group in groups:
        text = group['combined_content']
        chunk_ids = group['small_chunk_ids']
        print(f"Group from {len(chunk_ids)} chunks: {len(text)} chars")
else:
    print("No hierarchical groups - using legacy chunking")
```

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `get_small_chunks(source_id: str) -> list[dict[str, Any]]`

Get all small chunks for a source (for RAG indexing).

Args:
    source_id: Source identifier

Returns:
    List of chunk dictionaries with keys:
        - id, chunk_index, content, embedding
        - embedding_model, embedding_dimensions, status

Notes:
    - Only returns chunks with chunk_type='small' in metadata
    - Ordered by chunk_index
    - Used by indexing service to generate embeddings

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `store_chunks_and_groups(small_chunks: list[dict[str, Any]], hierarchical_groups: list[dict[str, Any]], batch_size: int = 500) -> None`

Store small chunks and hierarchical group metadata.

Args:
    small_chunks: List of chunk dictionaries with keys:
        - id, source_id, database_name, chunk_index
        - content, embedding, char_start, char_end
        - chunk_metadata, status, created_at
    hierarchical_groups: List of group dictionaries with keys:
        - id, group_index, small_chunk_ids
        - combined_content, char_start, char_end, token_count
    batch_size: Number of chunks to insert per batch

Notes:
    - Implementation may store groups in chunk metadata
    - Chunks are stored in 'staged' status (not searchable yet)
    - No embeddings are generated (done at index time)

| Parameter | Type | Description |
|---|---|---|
| `small_chunks` | `list[dict[str, Any]]` |  |
| `hierarchical_groups` | `list[dict[str, Any]]` |  |
| `batch_size` | `int` |  |

#### `update_chunk_status(source_id: str, status: str) -> int`

Update status for all chunks of a source.

Args:
    source_id: Source identifier
    status: New status ('staged', 'indexed', 'committed')

Returns:
    Number of chunks updated

Notes:
    - Changes status for all chunks (small + groups)
    - Used during import lifecycle transitions
    - 'committed' status makes chunks searchable

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `status` | `str` |  |

## `chaoscypher_core.ports.db`

Database protocol interface for chaoscypher-engine.

Defines Protocol for database metadata operations.
Main app implements this via an adapter that wraps its database repository.

### `class DatabaseProtocol`

Interface for database metadata operations.

Provides access to database configuration and paths.
Used by services that need database directory paths
(e.g., for partitioning graph data).

**Bases:** `Protocol`

**Methods:**

#### `get_database(database_name: str) -> DatabaseInfo`

Get database metadata.

Args:
    database_name: Name of the database

Returns:
    DatabaseInfo object with name, path, etc.

Raises:
    ValueError: If database not found

Example:

```
db_info = database_repo.get_database("my_database")
print(f"Database path: {db_info.path}")
# path might be: /data/databases/my_database
```

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

## `chaoscypher_core.ports.embedding`

Embedding Provider Protocol for chaoscypher-engine.

Defines the Protocol interface that all embedding providers must implement.
This enables the engine to work with multiple embedding backends (Ollama, OpenAI, Gemini)
through a unified interface.

`EmbeddingHealthStatus` is defined here (not in the adapter layer) because it is
part of the port's vocabulary — the return type of `check_health` belongs to the
contract, not to any specific backend implementation.

Architecture:
    - EmbeddingProviderProtocol defines the contract for embedding operations
    - Concrete providers (OllamaEmbeddingProvider, OpenAIEmbeddingProvider, etc.) implement it
    - Consumer code depends on the protocol, not concrete implementations
    - EmbeddingHealthStatus is the canonical health-check return type at the port level

Example:

```
from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol

async def index_document(provider: EmbeddingProviderProtocol, text: str) -> list[float]:
    result = await provider.embed(text)
    return result.embedding
```

### `class EmbeddingHealthStatus`

Health status of an embedding provider.

Returned by embedding providers to indicate whether the provider
is operational and report diagnostic details.

This type is part of the port's vocabulary: it is the return type of
`EmbeddingProviderProtocol.check_health` and belongs at the ports layer,
not in any adapter-specific module.

Attributes:
    healthy: Whether the provider is operational.
    provider: Provider type identifier (e.g., "ollama", "openai").
    model: Model name currently configured.
    dimensions: Embedding vector dimensions (0 if unknown).
    message: Optional human-readable status message.
    response_time_ms: Optional response time in milliseconds.

**Bases:** `BaseModel`

**Attributes:**

- `dimensions`: `int`
- `healthy`: `bool`
- `message`: `str | None`
- `model`: `str`
- `model_config`
- `provider`: `str`
- `response_time_ms`: `int | None`

### `class EmbeddingProviderProtocol`

Interface for embedding generation providers.

All embedding providers must implement this protocol to provide
single-text embedding, batch embedding, and health check capabilities.

Protocol-based design allows any class with matching methods
to satisfy this interface (structural typing).

Implementations:
    - OllamaEmbeddingProvider: Local Ollama server
    - OpenAIEmbeddingProvider: OpenAI API
    - GeminiEmbeddingProvider: Google Gemini API

**Bases:** `Protocol`

**Methods:**

#### `batch_embed(texts: list[str], batch_size: int = 64) -> BatchEmbedResult`

Generate embedding vectors for multiple texts.

All-or-nothing: every implementation embeds the full batch or raises.
There is no partial-success return — a caller either gets one vector
per input text or an `LLMError`, never a mix of the two.

Args:
    texts: List of input texts to embed.
    batch_size: Number of texts to process per batch.

Returns:
    BatchEmbedResult with embedding vectors, total count, and
    provider name.

Raises:
    LLMError: If the batch embedding request fails.

| Parameter | Type | Description |
|---|---|---|
| `texts` | `list[str]` |  |
| `batch_size` | `int` |  |

#### `check_health() -> EmbeddingHealthStatus`

Check the health and availability of the embedding provider.

Returns:
    EmbeddingHealthStatus with health state, provider info, model details,
    and optional diagnostics.

#### `embed(text: str) -> EmbedResult`

Generate an embedding vector for a single text.

Args:
    text: Input text to embed.

Returns:
    EmbedResult with embedding vector, provider name, and optional token usage.

Raises:
    LLMError: If the embedding request fails.

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |

**Attributes:**

- `model_name`: `str` — The model identifier used by this provider.
- `provider_type`: `str` — Return the provider type identifier (e.g., 'ollama', 'openai', 'gemini').
- `query_prefix`: `str` — Text the provider puts before a query when it embeds one; empty when it puts none.

Part of what decides a query's vector besides its text, so the
benchmark's query-vector cache keys on it
(`chaoscypher_core.benchmark.pins.query_vector_identity`). Every
provider defines it; none uses a prefix today.

## `chaoscypher_core.ports.graph`

Graph repository interface for chaoscypher-engine.

Defines Protocol for graph data access (nodes, edges, templates).
Main app implements this via an adapter class that wraps its GraphRepository.

### `class GraphRepositoryProtocol`

Interface for knowledge graph operations.

Implementations provide access to the knowledge graph
for node, edge, and template operations.

Protocol-based design allows any class with matching methods
to satisfy this interface (structural typing).

**Bases:** `Protocol`

**Methods:**

#### `absorb_edge(keep_id: str, drop_id: str) -> None`

Fold a moved or duplicate edge into its new edge: memberships and citations.

Same rules as `absorb_node`, for `RelationshipCitation.edge_id`.

| Parameter | Type | Description |
|---|---|---|
| `keep_id` | `str` |  |
| `drop_id` | `str` |  |

#### `absorb_node(keep_id: str, drop_id: str) -> None`

Fold a merged-away node into the kept one: memberships and entity citations.

`drop_id`'s memberships move to `keep_id`; where both hold one for the
same source, the kept row stays and the dropped snapshot is appended to its
`absorbed` list. Every `SourceCitation` naming `drop_id` names
`keep_id` after. Call before deleting `drop_id`. Joins the caller's
transaction; nothing is committed.

| Parameter | Type | Description |
|---|---|---|
| `keep_id` | `str` |  |
| `drop_id` | `str` |  |

#### `assign_source_to_nodes(node_ids: list[str], source_id: str, database_name: str, readings: Mapping[str, dict[str, Any]] | None = None) -> int`

Link nodes to `source_id`: back-fill the home where missing, record the membership.

The CCX importer creates nodes before it knows their source (the
node->source link lives in the citation records, imported last), so it
stamps `graph_nodes.source_id` here once citations resolve. Nodes
that already carry a source id keep it (first link wins). Every node
gains `source_id` as a member, its snapshot `readings[node_id]`
(what the source wrote: the package's own entity), else the node's
own pre-merge reading (its home's snapshot), never a merged row.

Args:
    node_ids: Node ids to link.
    source_id: Source id to assign to those nodes.
    database_name: Database that owns the nodes.
    readings: Per node id, the record `source_id` wrote for it
        (`node_create_snapshot`).

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `list[str]` |  |
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `readings` | `Mapping[str, dict[str, Any]] \| None` |  |

#### `assign_source_to_templates(template_ids: list[str], source_id: str, database_name: str) -> int`

Back-fill `source_id` on templates that lack one; return rows changed.

Templates are source-owned: a source's release deletes the ones nothing
else uses (D6). The CCX
importer creates templates before the source exists (FK order), so it
links them here once the source lands. Templates that already carry a
source id are left untouched.

Args:
    template_ids: Template ids to link.
    source_id: Source id to assign.
    database_name: Database that owns the templates.

| Parameter | Type | Description |
|---|---|---|
| `template_ids` | `list[str]` |  |
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `clear_node_embeddings(node_ids: list[str]) -> int`

Clear the stored embedding of each node; return rows changed.

For a node whose text changed under a vector a caller cannot refresh
at once: an indexing step that embeds only vectorless nodes then
re-embeds it.

Args:
    node_ids: The nodes whose `embedding` is cleared.

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `list[str]` |  |

#### `count_edges(source_node_id: str | None = None, target_node_id: str | None = None, source_ids: list[str] | None = None, include_disabled_sources: bool = True) -> int`

Count edges, optionally filtered by source/target node or source document.

Args:
    source_node_id: Optional source node ID filter
    target_node_id: Optional target node ID filter
    source_ids: Optional list of source document IDs to filter by
    include_disabled_sources: When False, also drops edges from disabled
        sources (mirrors `list_edges`) for pagination totals.

Returns:
    Count of edges

| Parameter | Type | Description |
|---|---|---|
| `source_node_id` | `str \| None` |  |
| `target_node_id` | `str \| None` |  |
| `source_ids` | `list[str] \| None` |  |
| `include_disabled_sources` | `bool` |  |

#### `count_edges_per_node(node_ids: list[str]) -> dict[str, int]`

Return total incident edge count for each node ID.

Counts both incoming and outgoing edges for the given nodes in a
single pair of grouped queries (one per direction). Useful for
list/search projections that need a per-hit "connections" number
without a round-trip per node.

Args:
    node_ids: Node IDs to count edges for. Empty input returns `{}`.

Returns:
    `{node_id: total_incident_edges}` for every input ID. Nodes
    with no edges still appear with a count of `0`.

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `list[str]` |  |

#### `count_nodes(include_disabled_sources: bool = True) -> int`

Count total nodes.

Args:
    include_disabled_sources: When False, excludes nodes from disabled
        sources so the count matches `list_nodes` (used for pagination
        totals). Defaults True (true storage total).

Returns:
    Count of nodes

| Parameter | Type | Description |
|---|---|---|
| `include_disabled_sources` | `bool` |  |

#### `count_nodes_by_source(source_ids: list[str], include_disabled_sources: bool = True) -> int`

Count nodes from specific source documents.

Args:
    source_ids: List of source document IDs
    include_disabled_sources: When False, also drops nodes from disabled
        sources (mirrors `list_nodes`).

Returns:
    Count of nodes from those sources

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `list[str]` |  |
| `include_disabled_sources` | `bool` |  |

#### `count_nodes_by_template(template_ids: list[str], exclude: bool = False, include_disabled_sources: bool = True) -> int`

Count nodes with specific template IDs (or excluding them).

Args:
    template_ids: List of template IDs
    exclude: If True, count nodes NOT in template_ids
    include_disabled_sources: When False, also drops nodes from disabled
        sources (mirrors `list_nodes`).

Returns:
    Count of nodes

| Parameter | Type | Description |
|---|---|---|
| `template_ids` | `list[str]` |  |
| `exclude` | `bool` |  |
| `include_disabled_sources` | `bool` |  |

#### `count_templates(*, database_name: str | None = None, template_type: str | None = None, source_id: str | None = None, include_disabled_sources: bool = True) -> int`

Count GraphTemplate rows.

Args:
    database_name: Database scope. Defaults to the repo's bound database.
    template_type: Optional filter by template_type ('node' or 'edge').
    source_id: Optional filter by source_id.
    include_disabled_sources: When False, excludes templates from disabled
        sources (mirrors `list_templates`) for pagination totals; ignored
        when `source_id` is given.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str \| None` |  |
| `template_type` | `str \| None` |  |
| `source_id` | `str \| None` |  |
| `include_disabled_sources` | `bool` |  |

#### `count_templates_by_system(is_system: bool) -> int`

Count user or system templates.

Args:
    is_system: True to count system templates, False for user templates

Returns:
    Count of templates

| Parameter | Type | Description |
|---|---|---|
| `is_system` | `bool` |  |

#### `create_edge(edge_create: EdgeCreate, custom_id: str | None = None, *, person_made: bool = False) -> Edge`

Create a new edge between two nodes.

Args:
    edge_create: Edge creation data
    custom_id: The row's id; None generates one.
    person_made: A person makes it (the create tools, the API, the SDK, the
        CLI): no release deletes or rewrites it (migration 0025). Never request
        input: only the person paths pass it.

Returns:
    Created Edge object with generated ID

Note:
    This is a raw write: implementations do not check that the
    source/target nodes or the template exist. Use
    `EdgeService.create_edge` for validated creation (it raises
    `NotFoundError` / `ValidationError`). On the SQLite adapter
    a dangling endpoint or template surfaces as the backend's
    `sqlalchemy.exc.IntegrityError`, not `ValueError`.

Example:

```
from chaoscypher_core.models import EdgeCreate

edge_create = EdgeCreate(
    template_id="knows",
    source_node_id="person_123",
    target_node_id="person_456",
    label="knows",
    properties={"since": "2020"}
)
created_edge = graph_repo.create_edge(edge_create)
```

| Parameter | Type | Description |
|---|---|---|
| `edge_create` | `EdgeCreate` |  |
| `custom_id` | `str \| None` |  |
| `person_made` | `bool` |  |

#### `create_edges_batch(edge_creates: list[EdgeCreate]) -> list[Edge]`

Create multiple edges in batch.

Args:
    edge_creates: List of edge creation data

Returns:
    List of created Edge objects

| Parameter | Type | Description |
|---|---|---|
| `edge_creates` | `list[EdgeCreate]` |  |

#### `create_node(node_create: NodeCreate, custom_id: str | None = None, *, person_made: bool = False) -> Node`

Create a new node in the graph.

Args:
    node_create: Node creation data
    custom_id: The row's id; None generates one.
    person_made: A person makes it (the create tools, the API, the SDK, the
        CLI): no release deletes or rewrites it (migration 0025). Never request
        input: only the person paths pass it.

Returns:
    Created Node object with generated ID

Note:
    This is a raw write: implementations do not validate that
    `template_id` exists or that required properties are set.
    Use `NodeService.create_node` for validated creation (it
    raises `NotFoundError`). On the SQLite adapter a dangling
    `template_id` surfaces as the backend's
    `sqlalchemy.exc.IntegrityError`, not `ValueError`.

Example:

```
from chaoscypher_core.models import NodeCreate

node_create = NodeCreate(
    template_id="person",
    label="Alice Smith",
    properties={"age": 30, "email": "alice@example.com"}
)
created_node = graph_repo.create_node(node_create)
print(f"Created node with ID: {created_node.id}")
```

| Parameter | Type | Description |
|---|---|---|
| `node_create` | `NodeCreate` |  |
| `custom_id` | `str \| None` |  |
| `person_made` | `bool` |  |

#### `create_nodes_batch(node_creates: list[NodeCreate]) -> list[Node]`

Create multiple nodes in batch.

Args:
    node_creates: List of node creation data

Returns:
    List of created Node objects

| Parameter | Type | Description |
|---|---|---|
| `node_creates` | `list[NodeCreate]` |  |

#### `create_template(template_create: TemplateCreate, custom_id: str | None = None, is_system: bool = False) -> Template`

Create a new template.

Args:
    template_create: Template creation data
    custom_id: Optional custom ID (if None, auto-generated)
    is_system: Whether this is a system template

Returns:
    Created Template object

| Parameter | Type | Description |
|---|---|---|
| `template_create` | `TemplateCreate` |  |
| `custom_id` | `str \| None` |  |
| `is_system` | `bool` |  |

#### `create_templates_batch(template_creates: list[TemplateCreate]) -> list[Template]`

Create multiple templates in batch.

Args:
    template_creates: List of template creation data

Returns:
    List of created Template objects

| Parameter | Type | Description |
|---|---|---|
| `template_creates` | `list[TemplateCreate]` |  |

#### `delete_edge(edge_id: str) -> bool`

Delete an edge by ID.

Args:
    edge_id: Edge ID to delete

Returns:
    True if edge was deleted, False if not found

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `delete_edges_batch(*, edge_ids: list[str]) -> int`

Delete GraphEdge rows by ID list.

Args:
    edge_ids: IDs to delete.

Returns:
    Number of rows deleted.

| Parameter | Type | Description |
|---|---|---|
| `edge_ids` | `list[str]` |  |

#### `delete_node(node_id: str) -> bool`

Delete a node by ID.

Args:
    node_id: Node ID to delete

Returns:
    True if node was deleted, False if not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `delete_nodes_batch(*, node_ids: list[str]) -> int`

Delete GraphNode rows by ID list. Returns count.

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `list[str]` |  |

#### `delete_template(template_id: str, force: bool = False) -> bool`

Delete a template by ID.

Args:
    template_id: Template ID to delete
    force: If True, delete even if template is in use

Returns:
    True if template was deleted, False if not found

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |
| `force` | `bool` |  |

#### `delete_templates_batch(*, template_ids: list[str]) -> int`

Delete GraphTemplate rows by ID list. Returns count.

| Parameter | Type | Description |
|---|---|---|
| `template_ids` | `list[str]` |  |

#### `discard_failed_work() -> None`

Roll back what a failed call left in this repository's session.

For a catch that answers a failure and goes on: the next call must not find the
session needing a rollback. Inside a transaction block, the block rolls back.

#### `drop_edge_citations(edge_id: str) -> None`

Delete an edge's relationship citations (a merge turned it into a self-loop).

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `edge_members(edge_id: str) -> dict[str, dict[str, Any]]`

Map source_id to the snapshot that source wrote for this edge.

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `edges_closed_by(source_id: str) -> list[Edge]`

The edges `source_id` closed (`closed_by_source_id`).

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `edges_from(node_id: str, label: str) -> list[Edge]`

Every edge from `node_id` with `label`, open or closed.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `label` | `str` |  |

#### `edges_with_true_properties(keys: Sequence[str], carry: Sequence[str] = (), ids: Sequence[str] | None = None) -> dict[str, dict[str, Any]]`

The edges whose properties hold JSON `true` under any of `keys`, with `carry` values.

The edge counterpart of `nodes_with_true_properties`, for the
projections that load no edge properties (the canvas's `minimal`
listing, PageRank's `list_edges_minimal`) and must still read the
pipeline's edge flags (`name_identity.PIPELINE_EDGE_FLAG_KEYS`: a
capped edge is drawn thin, an unevidenced one dashed). `true`
exactly: a string `"true"` or a number is no flag.

Args:
    keys: Top-level property keys read as flags.
    carry: Top-level property keys whose values a flagged edge also
        returns when it holds them (the canvas shows
        `evidence_reason` beside `unevidenced`).
    ids: When set, only these edges are read (as for
        `nodes_with_true_properties`). None reads every edge.

Returns:
    Edge id -> `{key: True}` for each flag it holds, in `keys`
    order, plus each `carry` key it holds; edges holding no flag
    are absent.

| Parameter | Type | Description |
|---|---|---|
| `keys` | `Sequence[str]` |  |
| `carry` | `Sequence[str]` |  |
| `ids` | `Sequence[str] \| None` |  |

#### `ensure_default_templates_exist(default_templates_provider: Callable[[], list[dict[str, Any]]] | None = None) -> int`

Insert each template the provider gives that the database lacks; return how many.

A template is present when a row has its id; a present one is never rewritten.

Args:
    default_templates_provider: Returns the template definitions
        (`templates/default_templates.py`'s shape); None inserts nothing.

Returns:
    How many were inserted.

| Parameter | Type | Description |
|---|---|---|
| `default_templates_provider` | `Callable[[], list[dict[str, Any]]] \| None` |  |

#### `export_graph(max_items: int = 100000) -> dict[str, Any]`

Export all graph data (nodes, edges, templates) for CCX package creation.

Args:
    max_items: Maximum nodes/edges to export.

Returns:
    Dict with `nodes`, `edges`, `templates` lists of model_dump()s.

| Parameter | Type | Description |
|---|---|---|
| `max_items` | `int` |  |

#### `export_graph_records(*, source_ids: list[str] | None = None, max_items: int = 100000) -> dict[str, list[dict[str, Any]]]`

Export graph nodes + edges as dicts carrying the persisted `ccx_iri`.

Unlike `export_graph` (which returns Pydantic-model dumps that
drop the `ccx_iri` stable-identity column), this projects the ORM
rows directly so the CCX 3.0 exporter can key identity on `ccx_iri`.

Args:
    source_ids: When given, restrict to nodes/edges of these sources;
        edges survive only when BOTH endpoints survive the node filter.
    max_items: Maximum nodes/edges to export per entity type.

Returns:
    `{"nodes": [node dicts], "edges": [edge dicts]}` including `ccx_iri`.

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `list[str] \| None` |  |
| `max_items` | `int` |  |

#### `find_orphaned_edges_by_source_node(*, database_name: str) -> list[str]`

Return IDs of edges whose source_node_id has no matching GraphNode.

Args:
    database_name: Database to scope to.

Returns:
    List of GraphEdge IDs (may be empty).

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `find_orphaned_edges_by_target_node(*, database_name: str) -> list[str]`

Return IDs of edges whose target_node_id has no matching GraphNode.

Args:
    database_name: Database to scope to.

Returns:
    List of GraphEdge IDs.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `find_orphaned_nodes_by_source(*, database_name: str) -> list[str]`

Return IDs of nodes whose source_id references a missing SourceRow and no source attests.

Nodes with `source_id IS NULL` are NOT considered orphaned, nor is a
node with any source membership (a merged node another source attests).

Args:
    database_name: Database to scope to.

Returns:
    List of GraphNode IDs.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `find_orphaned_templates_by_source(*, database_name: str) -> list[str]`

Return IDs of unused non-system templates whose source_id references a missing SourceRow.

System templates (`is_system=True`) are never considered orphaned,
nor is a template a node or edge still uses.

Args:
    database_name: Database to scope to.

Returns:
    List of GraphTemplate IDs.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `get_edge(edge_id: str) -> Edge | None`

Get an edge by ID.

Args:
    edge_id: Edge ID to retrieve

Returns:
    Edge object or None if not found

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |

#### `get_edge_by_ccx_iri(ccx_iri: str, database_name: str) -> dict[str, Any] | None`

Look up an edge by its stable CCX IRI.

Returns the ORM-row dict (so `ccx_iri` survives — the `Edge` model
has no such field) or `None` when no row in `database_name` carries
that IRI. Lookup primitive for the CCX 3.0 importer's upsert-by-IRI.

Args:
    ccx_iri: The CCX 3.0 stable IRI to match.
    database_name: Database that owns the edge.

| Parameter | Type | Description |
|---|---|---|
| `ccx_iri` | `str` |  |
| `database_name` | `str` |  |

#### `get_node(node_id: str, *, include_embedding: bool = True) -> Node | None`

Get a node by ID.

Args:
    node_id: Unique node identifier
    include_embedding: Default True. False leaves the 1024-float
        embedding out of the query (the `get_nodes_batch`
        projection contract); existence checks must pass False.

Returns:
    Node object or None if not found

Example:

```
node = graph_repo.get_node("person_123")
if node:
    print(f"Found node: {node.label}")
```

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `include_embedding` | `bool` |  |

#### `get_node_by_ccx_iri(ccx_iri: str, database_name: str) -> dict[str, Any] | None`

Look up a node by its stable CCX IRI.

Returns the ORM-row dict (so the `ccx_iri` column survives — the
`Node` model has no such field) or `None` when no row in
`database_name` carries that IRI. The lookup primitive the CCX 3.0
importer uses for upsert-by-IRI.

Args:
    ccx_iri: The CCX 3.0 stable IRI to match.
    database_name: Database that owns the node.

| Parameter | Type | Description |
|---|---|---|
| `ccx_iri` | `str` |  |
| `database_name` | `str` |  |

#### `get_nodes_batch(node_ids: list[str], *, include_embedding: bool = True) -> list[Node]`

Get multiple nodes by ID in a single operation.

Args:
    node_ids: List of node IDs to retrieve
    include_embedding: When False, the embedding column is neither
        queried nor decoded and `Node.embedding` is `None` —
        use for hydration paths that never read the vector.

Returns:
    List of Node objects (may be less than requested if some not found)

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `list[str]` |  |
| `include_embedding` | `bool` |  |

#### `get_template(template_id: str) -> Template | None`

Get a template by ID.

Args:
    template_id: Template ID to retrieve

Returns:
    Template object or None if not found

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |

#### `get_template_usage_counts(template_ids: list[str] | None = None) -> dict[str, dict[str, int]]`

Get usage counts (nodes and edges) for templates.

Args:
    template_ids: Optional list of template IDs to check (None = all)

Returns:
    Dict mapping template_id to \{"nodes": count, "edges": count\}

| Parameter | Type | Description |
|---|---|---|
| `template_ids` | `list[str] \| None` |  |

#### `list_edges(source_node_id: str | None = None, target_node_id: str | None = None, source_ids: list[str] | None = None, skip: int = 0, limit: int = 100, include_disabled_sources: bool = False, minimal: bool = False, with_nodes: bool = False) -> list[Edge] | list[EdgeWithNodes]`

List edges, optionally filtered by source/target node or source document.

Args:
    source_node_id: Optional source node ID filter
    target_node_id: Optional target node ID filter
    source_ids: Optional list of source document IDs to filter by
    skip: Number of results to skip
    limit: Maximum number of results
    include_disabled_sources: If False (default), excludes edges from disabled sources
    minimal: If True, only load essential fields
    with_nodes: If True, batch-load source_node and target_node for each edge
                and return EdgeWithNodes instances.

Returns:
    List of Edge objects, or EdgeWithNodes when with_nodes=True.

| Parameter | Type | Description |
|---|---|---|
| `source_node_id` | `str \| None` |  |
| `target_node_id` | `str \| None` |  |
| `source_ids` | `list[str] \| None` |  |
| `skip` | `int` |  |
| `limit` | `int` |  |
| `include_disabled_sources` | `bool` |  |
| `minimal` | `bool` |  |
| `with_nodes` | `bool` |  |

#### `list_edges_by_node(node_ids: Sequence[str], *, outgoing: bool, limit_per_node: int, include_disabled_sources: bool = False) -> dict[str, list[Edge]]`

Each node's edges out of it (`outgoing`) or into it, as `list_edges` lists them.

For every id in `node_ids`, exactly what `list_edges` returns
with `limit=limit_per_node` and the id as `source_node_id` (as
`target_node_id` when not `outgoing`), in its order. Every id is a
key; a node with no edges maps to `[]`. An adapter answers in as few
queries as it can: the graph context reads every seed's edges in both
directions.

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `Sequence[str]` |  |
| `outgoing` | `bool` |  |
| `limit_per_node` | `int` |  |
| `include_disabled_sources` | `bool` |  |

#### `list_nodes(template_id: str | None = None, source_ids: list[str] | None = None, skip: int = 0, limit: int = 100, include_disabled_sources: bool = False, minimal: bool = False, include_embedding: bool = True) -> list[Node]`

List all nodes, optionally filtered by template, source, and enabled status.

Args:
    template_id: Optional template ID to filter by (None = all nodes)
    source_ids: Optional list of source document IDs to filter by
    skip: Number of results to skip (for pagination)
    limit: Maximum number of results to return (default 100)
    include_disabled_sources: If False (default), excludes nodes from disabled sources
    minimal: If True, only load essential fields
    include_embedding: If True (default), embeddings are loaded with the
        nodes. Display/list callers that never read embeddings should pass
        False to avoid loading and serializing them. Ignored when minimal=True.

Returns:
    List of Node objects (empty list if none found)

Example:

```
# Get all nodes
all_nodes = graph_repo.list_nodes()

# Get nodes of specific type
people = graph_repo.list_nodes(template_id="person")

# Get nodes from specific sources
source_nodes = graph_repo.list_nodes(source_ids=["src_1", "src_2"])
```

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str \| None` |  |
| `source_ids` | `list[str] \| None` |  |
| `skip` | `int` |  |
| `limit` | `int` |  |
| `include_disabled_sources` | `bool` |  |
| `minimal` | `bool` |  |
| `include_embedding` | `bool` |  |

#### `list_nodes_without_embeddings(*, after_id: str | None = None, limit: int = 10000) -> list[Node]`

Nodes with no embedding vector, ordered by id, after `after_id` when given.

Args:
    after_id: Keyset cursor: only ids after it; None starts at the beginning.
    limit: Maximum number of results.

| Parameter | Type | Description |
|---|---|---|
| `after_id` | `str \| None` |  |
| `limit` | `int` |  |

#### `list_templates(template_type: str | None = None, include_disabled_sources: bool = False, source_id: str | None = None, skip: int = 0, limit: int | None = None) -> list[Template]`

List templates (node and edge types).

Args:
    template_type: Optional filter by type ("node" or "edge")
    include_disabled_sources: If False (default), hide templates from disabled sources
    source_id: Optional filter by source ID
    skip: Number of results to skip (for SQL-level pagination)
    limit: Maximum number of results (None returns all)

Returns:
    List of Template objects (both node and edge templates)

Example:

```
templates = graph_repo.list_templates()
node_templates = [t for t in templates if t.template_type == "node"]
edge_templates = [t for t in templates if t.template_type == "edge"]
```

| Parameter | Type | Description |
|---|---|---|
| `template_type` | `str \| None` |  |
| `include_disabled_sources` | `bool` |  |
| `source_id` | `str \| None` |  |
| `skip` | `int` |  |
| `limit` | `int \| None` |  |

#### `member_edge_ids(source_id: str) -> list[str]`

The edges `source_id` attests, in membership order.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `member_node_ids(source_id: str) -> list[str]`

The nodes `source_id` attests, in membership order.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `member_source_ids() -> set[str]`

Every source that attests some node: the graph's sources, by membership.

#### `memory_source_fields(source_ids: Iterable[str]) -> dict[str, dict[str, Any]]`

Each source's `source_type`, `memory_by`, `valid_from` (a date or None), `created_at`.

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `Iterable[str]` |  |

#### `node_member_sources(node_ids: Iterable[str]) -> dict[str, set[str]]`

Map each of `node_ids` that has members to the ids of its member sources.

One batched read (no snapshots): for a caller that only needs to know
who attests a set of nodes.

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `Iterable[str]` |  |

#### `node_members(node_id: str) -> dict[str, dict[str, Any]]`

Map source_id to the snapshot that source wrote for this node.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |

#### `nodes_attested_by(source_ids: Collection[str], limit: int) -> list[str]`

At most `limit` nodes `source_ids` attest, most memberships among them first.

Ties go by node id. A scoped memory's seed reads its scope's nodes with it.

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `Collection[str]` |  |
| `limit` | `int` |  |

#### `nodes_with_true_properties(keys: Sequence[str], ids: Sequence[str] | None = None) -> dict[str, list[str]]`

The nodes whose properties hold JSON `true` under any of `keys`.

For a projection that loads no properties (the canvas's `minimal`
listing) and must still show the pipeline's flags
(`name_identity.PIPELINE_FLAG_KEYS`). `true` exactly: a string
`"true"` or a number is no flag.

Args:
    keys: Top-level property keys.
    ids: When set, only these nodes are read (the ids a capped,
        source-scoped listing returned), so the cost follows what
        is shown, not the whole graph. None reads every node.

Returns:
    Node id -> the keys it holds `true` under, in `keys` order;
    nodes holding none are absent.

| Parameter | Type | Description |
|---|---|---|
| `keys` | `Sequence[str]` |  |
| `ids` | `Sequence[str] \| None` |  |

#### `open_edges(node_id: str, label: str) -> list[Edge]`

The edges from `node_id` with `label` whose `valid_to` is unset.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `label` | `str` |  |

#### `reattest_edges(rows: Sequence[tuple[str, EdgeCreate]]) -> set[str]`

`reattest_nodes` for edges.

| Parameter | Type | Description |
|---|---|---|
| `rows` | `Sequence[tuple[str, EdgeCreate]]` |  |

#### `reattest_nodes(rows: Sequence[tuple[str, NodeCreate]]) -> set[str]`

Give each `(node_id, create)` its source's membership when that source has none.

The snapshot is the record `create` writes. Returns the ids that
gained a membership. Joins the caller's transaction; nothing is committed.

| Parameter | Type | Description |
|---|---|---|
| `rows` | `Sequence[tuple[str, NodeCreate]]` |  |

#### `record_edge_sources(rows: Sequence[tuple[str, str, dict[str, Any]]]) -> None`

Record `(edge_id, source_id, snapshot)` memberships; same rules as nodes.

| Parameter | Type | Description |
|---|---|---|
| `rows` | `Sequence[tuple[str, str, dict[str, Any]]]` |  |

#### `record_node_sources(rows: Sequence[tuple[str, str, dict[str, Any]]]) -> None`

Record `(node_id, source_id, snapshot)` memberships.

A source writing a node again replaces its own snapshot. Joins the
caller's transaction; nothing is committed.

| Parameter | Type | Description |
|---|---|---|
| `rows` | `Sequence[tuple[str, str, dict[str, Any]]]` |  |

#### `rehome(nodes: Mapping[str, str], edges: Mapping[str, str], *, entity_types: Mapping[str, str] | None = None) -> None`

Re-home each node (edge) id to the source it maps to.

A survivor of a release is re-homed to a member that still attests it:
its `source_id`, a node's `entity_type` (`entity_types`), and its
template, moved to that source's template of the same type and name
when there is one. Joins the caller's transaction; nothing is committed.

| Parameter | Type | Description |
|---|---|---|
| `nodes` | `Mapping[str, str]` |  |
| `edges` | `Mapping[str, str]` |  |
| `entity_types` | `Mapping[str, str] \| None` |  |

#### `release_all_sources() -> None`

Release every source of this database in one pass (the reset paths).

#### `release_source(source_id: str) -> ReleasedRows`

Take a source out of the graph: its memberships, and what only it attested.

Candidates are the nodes and edges the source is a member of, plus the
rows homed at it. Each loses the source's membership; a candidate no
source attests any more is deleted (an edge also goes with a deleted
endpoint); the surviving candidates are reported as touched. The
source's templates follow D6: deleted when unused, else re-homed to a
member source of a surviving user (unowned only when no user has a
member). Joins the caller's transaction; never deletes the source row
or its citations.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `reopen_edges_closed_by(source_id: str) -> int`

Reopen the edges `source_id` closed (`valid_to` and `closed_by_source_id` unset); how many.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `repoint_edge(edge_id: str, *, source_node_id: str, target_node_id: str) -> None`

Move an edge to new ends in place, keeping its id, `created_at` and `ccx_iri`.

Joins the caller's transaction: nothing is committed here.

Raises:
    NotFoundError: No edge of this database has the id.

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |
| `source_node_id` | `str` |  |
| `target_node_id` | `str` |  |

#### `set_edge_validity(edge_id: str, *, valid_from: date | None, valid_to: date | None, closed_by_source_id: str | None) -> None`

Write an edge's validity window; joins the caller's transaction.

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |
| `valid_from` | `date \| None` |  |
| `valid_to` | `date \| None` |  |
| `closed_by_source_id` | `str \| None` |  |

#### `source_valid_from(source_ids: Iterable[str]) -> dict[str, date | None]`

Each source's `valid_from` (a non-memory source maps to None).

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `Iterable[str]` |  |

#### `update_edge(edge_id: str, edge_update: EdgeUpdate) -> Edge | None`

Update an existing edge.

Args:
    edge_id: Edge ID to update
    edge_update: Edge update data

Returns:
    Updated Edge object or None if not found

| Parameter | Type | Description |
|---|---|---|
| `edge_id` | `str` |  |
| `edge_update` | `EdgeUpdate` |  |

#### `update_node(node_id: str, node_update: NodeUpdate) -> Node | None`

Update an existing node.

Args:
    node_id: Node ID to update
    node_update: Node update data

Returns:
    Updated Node object or None if not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `node_update` | `NodeUpdate` |  |

#### `update_node_embeddings_batch(embeddings: dict[str, list[float]]) -> int`

Persist embeddings for many nodes in one transaction; return rows changed.

Used to re-embed an imported source's nodes without N per-node writes.

Args:
    embeddings: Mapping of node id to its embedding vector (list[float]).

| Parameter | Type | Description |
|---|---|---|
| `embeddings` | `dict[str, list[float]]` |  |

#### `update_node_position(node_id: str, x: float, y: float) -> Node | None`

Update only the node's position.

Args:
    node_id: Node ID to update
    x: X coordinate
    y: Y coordinate

Returns:
    Updated Node object or None if not found

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `x` | `float` |  |
| `y` | `float` |  |

#### `update_template(template_id: str, template_update: TemplateUpdate) -> Template | None`

Update an existing template.

Args:
    template_id: Template ID to update
    template_update: Template update data

Returns:
    Updated Template object or None if not found

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |
| `template_update` | `TemplateUpdate` |  |

#### `upsert_edge_by_ccx_iri(ccx_iri: str, edge_create: EdgeCreate, database_name: str, source_id: str | None = None) -> dict[str, Any]`

Idempotently create or update an edge keyed by CCX IRI.

SELECT by `(database_name, ccx_iri)`; update the existing row's
label / properties / endpoints when found (incoming-wins, no
duplicate), else create a new edge carrying the given `ccx_iri`.
`EdgeCreate` has no `ccx_iri` field, so the IRI is set on the ORM
row directly. Used by the CCX 3.0 importer for idempotent re-import.

Args:
    ccx_iri: Stable CCX IRI used as the merge key.
    edge_create: Edge payload (template_id, endpoints, label, ...).
    database_name: Database to scope the upsert to.
    source_id: Optional source id override for the created/updated row.

Returns:
    The created or updated edge row dict (including `ccx_iri`).

| Parameter | Type | Description |
|---|---|---|
| `ccx_iri` | `str` |  |
| `edge_create` | `EdgeCreate` |  |
| `database_name` | `str` |  |
| `source_id` | `str \| None` |  |

#### `upsert_edges_batch(edge_creates: list[EdgeCreate], *, reattested: set[str] | None = None) -> tuple[list[Edge], int]`

Idempotently create or reuse edges by stable content key.

Used on the commit path; mirror of `upsert_nodes_batch`,
`reattested` included.

Args:
    edge_creates: List of edge creation data.
    reattested: Filled with the reused ids that gained a membership.

Returns:
    Tuple of:
    - List of Edge objects (created or pre-existing) in input order.
    - Count of rows actually inserted (not counting dedup reuses).

| Parameter | Type | Description |
|---|---|---|
| `edge_creates` | `list[EdgeCreate]` |  |
| `reattested` | `set[str] \| None` |  |

#### `upsert_node_by_ccx_iri(ccx_iri: str, node_create: NodeCreate, database_name: str, source_id: str | None = None) -> dict[str, Any]`

Idempotently create or update a node keyed by CCX IRI.

SELECT by `(database_name, ccx_iri)`; update the existing row's
label / properties / entity_type when found (incoming-wins, no
duplicate), else create a new node carrying the given `ccx_iri`.
`NodeCreate` has no `ccx_iri` field, so the IRI is set on the ORM
row directly. Used by the CCX 3.0 importer for idempotent re-import.

Args:
    ccx_iri: Stable CCX IRI used as the merge key.
    node_create: Node payload (template_id, label, entity_type, ...).
    database_name: Database to scope the upsert to.
    source_id: Optional source id override for the created/updated row.

Returns:
    The created or updated node row dict (including `ccx_iri`).

| Parameter | Type | Description |
|---|---|---|
| `ccx_iri` | `str` |  |
| `node_create` | `NodeCreate` |  |
| `database_name` | `str` |  |
| `source_id` | `str \| None` |  |

#### `upsert_nodes_batch(node_creates: list[NodeCreate], *, reattested: set[str] | None = None) -> tuple[list[Node], int]`

Idempotently create or reuse nodes by stable content key.

Used on the commit path: re-dispatched commits observe pre-existing
rows via a bulk SELECT-by-id and leave them untouched. A reused row
the incoming source does not attest (one that outlived the source's
release under another member) gets the source's membership back with
the reading it writes now, and its id is added to `reattested` for
the caller to rebuild (#795).

Args:
    node_creates: List of node creation data.
    reattested: Filled with the reused ids that gained a membership.

Returns:
    Tuple of:
    - List of Node objects (created or pre-existing) in input order.
    - Count of rows actually inserted (not counting dedup reuses).

| Parameter | Type | Description |
|---|---|---|
| `node_creates` | `list[NodeCreate]` |  |
| `reattested` | `set[str] \| None` |  |

#### `upsert_template(template_create: TemplateCreate, is_system: bool = False) -> tuple[Template, bool]`

Idempotently create a template by stable content key.

Args:
    template_create: Template to create (or reuse).
    is_system: Whether this is a system template.

A reused template owned by another source (or by none) since a
release is handed back to the source its stable id names.

Returns:
    Tuple of:
    - Template Pydantic model with a stable .id.
    - True if the template was newly inserted, False if pre-existing.

| Parameter | Type | Description |
|---|---|---|
| `template_create` | `TemplateCreate` |  |
| `is_system` | `bool` |  |

#### `upsert_templates_batch(template_creates: list[TemplateCreate]) -> tuple[list[Template], int]`

Idempotently create a batch of templates by stable content key.

Args:
    template_creates: Templates to create or reuse.

Returns:
    Tuple of:
    - List of Template objects in input order (created or pre-existing).
    - Count of rows actually inserted (not counting dedup reuses).

| Parameter | Type | Description |
|---|---|---|
| `template_creates` | `list[TemplateCreate]` |  |

#### `write_locked() -> AbstractContextManager[None]`

A unit of work that holds the database's writer lock from its first statement.

For a read-then-write that must not interleave with another writer:
what is read inside is what the writes inside build on. The rows the
session cached are reloaded when the lock is taken, so what is read
inside is the database as of the lock. Commits on a
clean exit and rolls back on an exception; inside a caller's
transaction it joins it (taking the lock now if the transaction has
not written yet) and commits nothing.

## `chaoscypher_core.ports.index`

Indexing protocol interface for chaoscypher-engine.

Defines Protocol for document chunk indexing operations.
Used by IndexingService for RAG embedding generation.
Main app implements this via an adapter that wraps its indexing repository.

### `class IndexingProtocol`

Interface for document chunk indexing operations.

Handles storage and retrieval of document chunks with embeddings
for RAG (Retrieval-Augmented Generation) indexing.

Used by:
- IndexingService: Generate and store embeddings for document chunks
- SearchService: Retrieve chunks for search results

**Bases:** `Protocol`

**Methods:**

#### `get_chunk_by_id(chunk_id: str) -> dict[str, Any] | None`

Get a single chunk by UUID with metadata.

Args:
    chunk_id: Chunk UUID

Returns:
    Chunk dictionary with keys, or None if not found:
        - id, source_id, database_name, chunk_index
        - content, embedding, embedding_model, embedding_dimensions
        - page_number, section, start_time, end_time, chunk_metadata,
          status, created_at

Notes:
    - Used by SearchService to hydrate chunk results
    - Returns None if chunk not found

| Parameter | Type | Description |
|---|---|---|
| `chunk_id` | `str` |  |

#### `get_chunks_by_ids_batch(chunk_ids: list[str], *, include_embeddings: bool = False) -> list[dict[str, Any]]`

Fetch multiple chunks by UUID in one query.

Batch sibling of `get_chunk_by_id`, minus the heavy columns:
`embedding` (~5KB per chunk) and `raw_content` are excluded
from the returned dicts by default; `include_embeddings=True`
adds `embedding` back (`raw_content` is always excluded).

Args:
    chunk_ids: Chunk UUIDs.
    include_embeddings: If True, include the embedding column
        (slower — only for callers that consume the vectors).

Returns:
    Chunk dictionaries for every id that exists, in input order;
    missing ids are silently absent.

Notes:
    - Used by SearchService and the tool handlers to hydrate a
      page of chunk results in one round trip instead of one
      SELECT per chunk.

| Parameter | Type | Description |
|---|---|---|
| `chunk_ids` | `list[str]` |  |
| `include_embeddings` | `bool` |  |

#### `get_chunks_by_source(source_id: str, page: int = 1, page_size: int = 50, status: str | None = None, include_embeddings: bool = False) -> tuple[list[dict[str, Any]], int]`

Get all chunks for a source with pagination, ordered by chunk_index.

Args:
    source_id: Source identifier
    page: Page number (1-indexed)
    page_size: Number of items per page
    status: Optional status filter
    include_embeddings: If True, include all columns (slower, for export)

Returns:
    Tuple of (chunks list as dicts, total count).
    Chunk dicts contain keys:
        - id: Chunk UUID
        - source_id: Source ID
        - database_name: Database name
        - chunk_index: Sequential index
        - content: Text content
        - embedding: Base64-encoded embedding bytes (may be None)
        - embedding_model: Model name (may be None)
        - embedding_dimensions: Vector dimensions (may be None)
        - page_number: Optional page number
        - section: Optional section name
        - start_time / end_time: Media position in seconds for
          transcribed audio / video chunks (None otherwise)
        - chunk_metadata: Optional metadata dict
        - status: 'staged' | 'indexed' | 'committed'
        - created_at: Creation datetime

Notes:
    - Ordered by chunk_index for sequential processing
    - May include chunks without embeddings (status='staged')
    - Used by IndexingService to get chunks for embedding generation

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `page` | `int` |  |
| `page_size` | `int` |  |
| `status` | `str \| None` |  |
| `include_embeddings` | `bool` |  |

#### `increment_source_counter(*, source_id: str, database_name: str, column: str, n: int) -> None`

Atomically increment a numeric counter column on a source row.

Best-effort: `services.quality.counters` swallows errors so the
UPDATE may silently no-op for unknown sources.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `column` | `str` |  |
| `n` | `int` |  |

#### `update_chunk_embedding(chunk_id: str, embedding: str, embedding_model: str, embedding_dimensions: int, status: str) -> None`

Update a chunk with its generated embedding.

Args:
    chunk_id: Chunk UUID
    embedding: Base64-encoded embedding bytes
    embedding_model: Model name that generated the embedding
    embedding_dimensions: Vector dimensions (e.g., 1024)
    status: New status (typically 'indexed' = has embedding, not yet committed to vector search index)

Notes:
    - Called by IndexingService after generating embeddings
    - Status progression: staged → indexed → committed
    - 'indexed' means has embedding but not yet in vector search index
    - 'committed' means indexed in sqlite-vec and searchable

Deprecated:
    Prefer `update_chunk_embeddings_batch` for multi-chunk
    writes — this single-row method costs one full-row SELECT plus
    one real COMMIT per call. No production multi-chunk caller
    remains (the CLI's `index_file` embedding wave was the last
    and now uses the batch form); kept for isolated single-chunk
    writers.

| Parameter | Type | Description |
|---|---|---|
| `chunk_id` | `str` |  |
| `embedding` | `str` |  |
| `embedding_model` | `str` |  |
| `embedding_dimensions` | `int` |  |
| `status` | `str` |  |

#### `update_chunk_embeddings_batch(embeddings_by_chunk: dict[str, str], *, embedding_model: str, embedding_dimensions: int, status: str | None) -> list[str]`

Batch form of `update_chunk_embedding` — one UPDATE, one commit.

Args:
    embeddings_by_chunk: Mapping of chunk UUID → base64-encoded
        embedding bytes.
    embedding_model: Model name that generated the embeddings.
    embedding_dimensions: Vector dimensions (e.g., 1024).
    status: New status for every updated chunk, or None to leave
        each chunk's status as it is.

Returns:
    The subset of chunk ids that do not exist (skipped) — callers
    keep the per-chunk accounting `update_chunk_embedding`
    expressed by raising `NotFoundError`.

Notes:
    - Chunk-side sibling of `update_node_embeddings_batch`: an
      embedding wave is up to `batching.embedding_wave_size`
      (default 2,000) chunks, so per-chunk single-row writes cost
      one wide SELECT plus one COMMIT each.

| Parameter | Type | Description |
|---|---|---|
| `embeddings_by_chunk` | `dict[str, str]` |  |
| `embedding_model` | `str` |  |
| `embedding_dimensions` | `int` |  |
| `status` | `str \| None` |  |

#### `update_chunk_source(chunk_id: str, source_id: str) -> None`

Link a chunk to a source record (promote from staging).

Args:
    chunk_id: Chunk UUID
    source_id: Source record ID

Notes:
    - Called during commit to promote chunks to permanent storage

| Parameter | Type | Description |
|---|---|---|
| `chunk_id` | `str` |  |
| `source_id` | `str` |  |

#### `update_chunk_status(source_id: str, status: str) -> int`

Update status for all chunks of a source.

Args:
    source_id: Source identifier
    status: New status ('staged' | 'indexed' | 'committed' | 'rejected')

Returns:
    Number of chunks updated

Notes:
    - Used during commit process to mark chunks as committed
    - Status progression: staged → indexed → committed

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `status` | `str` |  |

#### `update_source_columns(*, source_id: str, database_name: str, updates: dict[str, Any]) -> None`

Apply a partial column update to a source row.

Used by quality-counter helpers (`mark_search_indexing_*`,
`set_loader_encoding`) without going through full `update_source`.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `updates` | `dict[str, Any]` |  |

## `chaoscypher_core.ports.search`

Search repository interface for chaoscypher-engine.

Defines Protocol for search operations (keyword, vector, semantic, hybrid).
Vector search uses sqlite-vec stored in app.db for WAL-mode concurrency safety.

Tracks the active embedding model name and vector dimensions in a
`search_metadata` table.  When the configured model or dimensions
change, sets `needs_full_reindex` so callers can trigger background
re-embedding.  Per-item dimension mismatches during indexing are queued
via `schedule_reindex()` and flushed asynchronously by the caller.

Main implementation: chaoscypher_core.adapters.sqlite.repos.SearchRepository

### `class SearchHits`

A search's `(item_id, score)` hits, best first, and what it found before its `k` cut.

`candidates_seen` is how many distinct items the search's arms found
that it would rank (for the hybrid search: keyword hits and vector hits
at or above the similarity floor), counted before the cut to `k`; None
when the search does not say. A search whose candidates did not reach
`k` read everything its scope holds that answers the query
(`scope_exhausted`): a shorter result is the scope running out,
not a fault. Until 2026-10-06 the GraphRAG handler compared its hybrid
arm with the number of chunks in the scope, which ignores the floor and
keyword misses, so ordinary low-relevance shortfalls were recorded as
`hybrid_search:scope_short` (audit wave 6).

**Bases:** `list[tuple[str, float]]`

**Attributes:**

- `candidates_seen`
- `k`
- `scope_exhausted`: `bool | None` — Whether the candidates fell short of `k` (everything that answers was read); None when unknown.

### `class SearchRepositoryProtocol`

Interface for search operations.

Implementations provide keyword search (fulltext) and vector similarity
search (semantic) over graph nodes and document chunks.

Search methods:
- keyword_search: Fast full-text search (no LLM needed)
- vector_search: Direct vector similarity (embedding provided)
- semantic_search: Text-to-embedding-to-vector search (async, needs callback)
- hybrid_search: Keyword and vector rankings fused by reciprocal rank (async)

**Bases:** `Protocol`

**Methods:**

#### `count_in_scope(item_type: str, source_ids: Iterable[str] | None = None) -> int`

How many items of `item_type` (`"chunk"`, `"node"`) a source scope holds that may answer.

A switched-off source's items are never counted (`source_visible`).

Args:
    item_type: `"chunk"` or `"node"`.
    source_ids: The scope; None counts every item.

Returns:
    The number of items a search in that scope could return.

| Parameter | Type | Description |
|---|---|---|
| `item_type` | `str` |  |
| `source_ids` | `Iterable[str] \| None` |  |

#### `delete_node(node_id: str, *, session: TransactionalSession | None = None) -> None`

Remove a node from both keyword and vector indexes.

See `index_node` for the `session` contract.

Args:
    node_id: Node ID to remove
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `session` | `TransactionalSession \| None` |  |

#### `delete_nodes_batch(node_ids: list[str], *, session: TransactionalSession | None = None) -> int`

Remove multiple nodes from both keyword and vector indexes.

Used for idempotent commit: cleans up previously indexed nodes
before re-committing. See `index_node` for the `session`
contract.

Args:
    node_ids: List of node IDs to remove from search indexes.
    session: Optional caller session to share a transaction with.

Returns:
    Number of nodes removed.

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `list[str]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `flush_reindex(batch_embed_fn: Callable[[list[str]], Any], *, session: TransactionalSession | None = None) -> int`

Re-embed and index all queued items.

See `index_node` for the `session` contract.

Args:
    batch_embed_fn: Async callable taking list of texts,
        returning list of embedding vectors.
    session: Optional caller session to share a transaction with.

Returns:
    Number of items re-indexed.

| Parameter | Type | Description |
|---|---|---|
| `batch_embed_fn` | `Callable[[list[str]], Any]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `flush_reindex_with_service(embedding_service: Any, *, session: TransactionalSession | None = None) -> int`

Convenience wrapper that flushes using an embedding provider.

See `index_node` for the `session` contract.

Args:
    embedding_service: Embedding provider implementing
        `EmbeddingProviderProtocol` with `batch_embed(texts)`
        method returning an object with `.embeddings` attribute.
    session: Optional caller session to share a transaction with.

Returns:
    Number of items re-indexed.

| Parameter | Type | Description |
|---|---|---|
| `embedding_service` | `Any` |  |
| `session` | `TransactionalSession \| None` |  |

#### `get_index_stats() -> dict[str, Any]`

Get statistics about the search indexes.

Returns:
    Dict with index statistics

#### `hybrid_search(query_text: str, k: int = 10, embedding_provider_callback: Callable[[str], Any] | None = None, min_similarity: float = 0.55, item_type: str | None = None, source_ids: Iterable[str] | None = None) -> SearchHits`

Perform hybrid search: keyword and vector rankings fused by reciprocal rank.

Strategy:
- Short queries (< 3 chars): keyword only
- Otherwise: both arms search `item_type` and their rankings are fused

Args:
    query_text: Text to search for
    k: Number of results to return (default 10)
    embedding_provider_callback: Async callback for generating embeddings
    min_similarity: Minimum similarity score (0-1) to accept semantic results
    item_type: "node", "chunk", or None for both.
    source_ids: A source scope applied inside both arms, before their cut;
        None for every source.

Returns:
    The (node_id, score) hits, best first, with how many candidates
    both arms found before the cut (`SearchHits`).

| Parameter | Type | Description |
|---|---|---|
| `query_text` | `str` |  |
| `k` | `int` |  |
| `embedding_provider_callback` | `Callable[[str], Any] \| None` |  |
| `min_similarity` | `float` |  |
| `item_type` | `str \| None` |  |
| `source_ids` | `Iterable[str] \| None` |  |

#### `index_embeddings_batch(embeddings: list[tuple[str, list[float]]], item_type: str = 'node', text_lookup: dict[str, str] | None = None, *, session: TransactionalSession | None = None) -> int`

Batch index embeddings.

See `index_node` for the `session` contract.

Args:
    embeddings: List of (item_id, embedding) tuples
    item_type: Type of items ("node", "chunk", "template")
    text_lookup: Optional mapping of item_id to source text for
        re-embedding items with dimension mismatches.
    session: Optional caller session to share a transaction with.

Returns:
    Number of embeddings indexed

| Parameter | Type | Description |
|---|---|---|
| `embeddings` | `list[tuple[str, list[float]]]` |  |
| `item_type` | `str` |  |
| `text_lookup` | `dict[str, str] \| None` |  |
| `session` | `TransactionalSession \| None` |  |

#### `index_node(node: Node, *, session: TransactionalSession | None = None) -> None`

Index a node for full-text and vector search.

When `session` is passed, the write joins the caller's
transaction: no auto-commit, and exceptions propagate so the
caller can roll back. When `session` is None, opens a
standalone connection with best-effort semantics (errors logged,
not raised) to preserve historical behavior.

Args:
    node: Node to index
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `node` | `Node` |  |
| `session` | `TransactionalSession \| None` |  |

#### `index_node_embedding(node_id: str, embedding: list[float], *, session: TransactionalSession | None = None) -> None`

Index a single node's embedding for vector search.

See `index_node` for the `session` contract.

Args:
    node_id: ID of the node
    embedding: Embedding vector
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` |  |
| `embedding` | `list[float]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `index_nodes_batch(nodes: list[Node], *, session: TransactionalSession | None = None) -> None`

Index multiple nodes in batch.

See `index_node` for the `session` contract.

Args:
    nodes: List of Node objects to index
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `nodes` | `list[Node]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `index_template(template_id: str, embedding: list[float], *, session: TransactionalSession | None = None) -> None`

Index a template embedding for semantic search.

See `index_node` for the `session` contract.

Args:
    template_id: Template ID to index
    embedding: Embedding vector for the template
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `template_id` | `str` |  |
| `embedding` | `list[float]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `keyword_search(query: str, limit: int = 10, item_type: str | None = 'node', source_ids: Iterable[str] | None = None) -> list[tuple[str, float]]`

Perform full-text keyword search (any significant word or quoted phrase matches).

Args:
    query: Search query string
    limit: Maximum number of results (default 10)
    item_type: "node" (default), "chunk", or None for both.
    source_ids: A source scope applied before the `limit`; None for every source.
        A switched-off source's items never match (`source_visible`).

Returns:
    List of (node_id, score) tuples, sorted by relevance descending.

| Parameter | Type | Description |
|---|---|---|
| `query` | `str` |  |
| `limit` | `int` |  |
| `item_type` | `str \| None` |  |
| `source_ids` | `Iterable[str] \| None` |  |

#### `rebuild_node_fulltext(nodes: Iterable[Node | Mapping[str, Any]] | None = None, *, session: TransactionalSession | None = None) -> int`

Make the node full-text index hold exactly the graph's nodes (labels and current aliases).

Rows of nodes no longer in the graph are deleted; vectors are untouched.
Called by a full reindex; a caller that knows which nodes it changed
(the identity resolver) takes `refresh_node_fulltext`. The nodes are read from the graph inside
the rewrite's own transaction (a list read earlier can miss a node
committed since); `nodes` is checked against it, and used only by
an index with no graph behind it.

Args:
    nodes: The nodes the caller expects the graph to hold (graph nodes,
        or mappings with `id`, `label` and `properties`), or None.
    session: Optional caller session to share a transaction with.

Returns:
    The number of rows the index holds afterwards.

| Parameter | Type | Description |
|---|---|---|
| `nodes` | `Iterable[Node \| Mapping[str, Any]] \| None` |  |
| `session` | `TransactionalSession \| None` |  |

#### `refresh_node_fulltext(node_ids: Iterable[str], *, session: TransactionalSession | None = None) -> int`

Rewrite the full-text rows of `node_ids` from the graph as it is now; nothing else.

The scoped form of `rebuild_node_fulltext` for a caller that
knows which nodes it changed (the identity resolver): each id still in
the graph gets its row rewritten from the node as stored, an id no
longer there loses its row, and no other row is read or written.
Vectors are untouched.

Args:
    node_ids: The nodes whose rows follow the graph.
    session: Optional caller session to share a transaction with.

Returns:
    The number of rows written (ids still in the graph).

| Parameter | Type | Description |
|---|---|---|
| `node_ids` | `Iterable[str]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `reindex_all_nodes(nodes: list[Node], *, session: TransactionalSession | None = None) -> None`

Reindex all nodes (useful after bulk import or index corruption).

See `index_node` for the `session` contract.

Args:
    nodes: List of all nodes to reindex
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `nodes` | `list[Node]` |  |
| `session` | `TransactionalSession \| None` |  |

#### `remove_embedding(item_id: str, item_type: str, *, session: TransactionalSession | None = None) -> None`

Remove an embedding from the per-type vector index.

See `index_node` for the `session` contract.

Args:
    item_id: ID of item to remove (may include prefix like "chunk:xxx")
    item_type: Type of item ("node", "chunk", "template") — selects
        which per-type vec0 table to delete from.
    session: Optional caller session to share a transaction with.

| Parameter | Type | Description |
|---|---|---|
| `item_id` | `str` |  |
| `item_type` | `str` |  |
| `session` | `TransactionalSession \| None` |  |

#### `schedule_reindex(item_id: str, text: str, item_type: str) -> None`

Queue an item for async re-embedding.

Args:
    item_id: ID of the item
    text: Source text to re-embed
    item_type: Type of item ("node", "chunk", "template")

| Parameter | Type | Description |
|---|---|---|
| `item_id` | `str` |  |
| `text` | `str` |  |
| `item_type` | `str` |  |

#### `semantic_search(query_text: str, k: int = 10, embedding_provider_callback: Callable[[str], Any] | None = None, item_type: str | None = None) -> list[tuple[str, float]]`

Perform semantic search using query text.

Generates embedding for the query via callback, then performs vector search.

Args:
    query_text: Text to search for
    k: Number of results to return (default 10)
    embedding_provider_callback: Async callback that takes query text
        and returns dict with "embedding" key containing the vector.
        Example: async def(text: str) -> \{"embedding": [...]\}
    item_type: Optional filter ("node", "chunk", "template"); None
        searches nodes and chunks.

Returns:
    List of (node_id, similarity_score) tuples

| Parameter | Type | Description |
|---|---|---|
| `query_text` | `str` |  |
| `k` | `int` |  |
| `embedding_provider_callback` | `Callable[[str], Any] \| None` |  |
| `item_type` | `str \| None` |  |

#### `template_semantic_search(query_embedding: list[float], k: int = 10, min_similarity: float = 0.5) -> list[tuple[str, float]]`

Perform semantic search over templates.

Args:
    query_embedding: Query embedding vector
    k: Number of results to return
    min_similarity: Minimum similarity score (0-1) to include (default 0.5)

Returns:
    List of (template_id, similarity_score) tuples

| Parameter | Type | Description |
|---|---|---|
| `query_embedding` | `list[float]` |  |
| `k` | `int` |  |
| `min_similarity` | `float` |  |

#### `vector_search(query_embedding: list[float], k: int = 10, item_type: str | None = None, source_ids: Iterable[str] | None = None) -> list[tuple[str, float]]`

Find k nearest neighbors to query embedding.

Args:
    query_embedding: Query embedding vector (must match dimensionality
                    of indexed vectors, typically 1024)
    k: Number of results to return (default 10)
    item_type: Optional filter by item type ("node", "chunk", "template");
        None searches nodes and chunks (templates only by name).
    source_ids: A source scope applied before the `k` cut (chunks
        and nodes; templates come from no source); None for every source.
        A switched-off source's items never match (`source_visible`).

Returns:
    List of (node_id, similarity_score) tuples,
    sorted by similarity descending (highest first).

    Similarity scores in range [0.0, 1.0] where:
    - 1.0 = perfect match (identical vectors)
    - 0.0 = no similarity (orthogonal vectors)

Notes:
    - Empty list if no nodes have embeddings
    - May return fewer than k results if not enough nodes exist

| Parameter | Type | Description |
|---|---|---|
| `query_embedding` | `list[float]` |  |
| `k` | `int` |  |
| `item_type` | `str \| None` |  |
| `source_ids` | `Iterable[str] \| None` |  |

**Attributes:**

- `has_pending_reindex`: `bool` — Check if there are items queued for re-embedding.

Returns:
    True if the reindex queue is non-empty.
- `needs_full_reindex`: `bool` — Whether the embedding model or dimensions changed since last init.

When True, every per-type vec0 table is stale and should be
re-embedded with the current model.

Returns:
    True if a full reindex is needed.

### `attesting_sources(item: object) -> list[str]`

The sources that attest a graph row: its members when it has any, else its home source.

A merged node is attested by every source it was merged from
(`member_source_ids`, which the repository's node loads fill), and its
`source_id` is only its home (#795, D7). A row with no members, or one
loaded without them, reads its `source_id` as before; a row naming no
source returns `[]`. The SQL form is
`chaoscypher_core.adapters.sqlite.repos.source_visibility.attested_by_sql`.

| Parameter | Type | Description |
|---|---|---|
| `item` | `object` |  |

### `has_no_source(source_id: str | None) -> bool`

Whether an item names no source: NULL and the empty string read alike.

An item with no source (a manual or legacy node) is in every source
scope, and no switched-off source hides it. Until 2026-10-06 the search's
node scope read `''` as no source while the graph listings read NULL
only (audit wave 6). The SQL form is
`chaoscypher_core.adapters.sqlite.repos.source_visibility.no_source_sql`;
a test pins the two alike.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str \| None` |  |

### `item_in_source_scope(item: object, scope: Container[str]) -> bool`

Whether a graph row is in a source scope: a person made it, it names no source, or a source in the scope attests it.

| Parameter | Type | Description |
|---|---|---|
| `item` | `object` |  |
| `scope` | `Container[str]` |  |

### `item_visible(item: object, disabled: Container[str]) -> bool`

Whether a graph row may answer: a person made it, it names no source, or a source not switched off attests it.

| Parameter | Type | Description |
|---|---|---|
| `item` | `object` |  |
| `disabled` | `Container[str]` |  |

### `node_in_source_scope(source_id: str | None, scope: Container[str]) -> bool`

Whether a node of `source_id` is in a source scope: it names no source, or one in it.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str \| None` |  |
| `scope` | `Container[str]` |  |

### `source_visible(source_id: str | None, disabled: Container[str]) -> bool`

Whether an item of `source_id` may answer: it names no source, or one not switched off.

`disabled` holds the ids of the sources set `enabled = 0`. The SQL
form, which every search arm, scope count and graph listing reads, is
`chaoscypher_core.adapters.sqlite.repos.source_visibility.source_visible_sql`;
a test pins the two alike.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str \| None` |  |
| `disabled` | `Container[str]` |  |

## `chaoscypher_core.ports.storage_chats`

ChatStorageProtocol — storage contract for chat history and messages.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.chats.ChatsMixin`.

### `class ChatStorageProtocol`

Storage protocol for chat operations.

Handles CRUD for:
- Chats (chat history)
- Chat messages

**Bases:** `Protocol`

**Methods:**

#### `claim_chat_processing(chat_id: str) -> bool`

Atomically claim a chat for processing (compare-and-swap on status).

Must be a single guarded UPDATE (`WHERE status != 'processing'`)
so concurrent claims cannot both succeed.

Args:
    chat_id: Chat to claim.

Returns:
    True when this call transitioned the chat to `processing`;
    False when the chat is unknown or already processing.

| Parameter | Type | Description |
|---|---|---|
| `chat_id` | `str` |  |

#### `count_chats(database_name: str, status: str | None = None, scoped: bool | None = None, search: str | None = None) -> int`

Count chats for database with optional filters.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `status` | `str \| None` |  |
| `scoped` | `bool \| None` |  |
| `search` | `str \| None` |  |

#### `create_chat(chat: dict[str, Any]) -> ChatDict`

Create chat.

| Parameter | Type | Description |
|---|---|---|
| `chat` | `dict[str, Any]` |  |

#### `create_message(message: dict[str, Any]) -> MessageDict`

Create chat message.

| Parameter | Type | Description |
|---|---|---|
| `message` | `dict[str, Any]` |  |

#### `delete_all_chats(*, database_name: str) -> int`

Delete every Chat row in one database.

Args:
    database_name: Database to scope the delete to.

Returns:
    Number of rows deleted.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `delete_chat(chat_id: str) -> bool`

Delete chat.

| Parameter | Type | Description |
|---|---|---|
| `chat_id` | `str` |  |

#### `delete_messages_after(chat_id: str, message_id: str, *, inclusive: bool = False) -> int`

Delete the tail of a chat's history starting at/after a message.

Backs regenerate (exclusive: keep the last user message, drop the
answer) and edit-and-resend (inclusive: the edited message itself is
replaced). Ordering matches `get_messages` (timestamp, id tiebreak).

Args:
    chat_id: Chat whose tail to remove.
    message_id: Anchor message; must belong to `chat_id`.
    inclusive: Also delete the anchor itself.

Returns:
    Number of rows deleted (0 when the anchor is unknown or belongs
    to a different chat).

| Parameter | Type | Description |
|---|---|---|
| `chat_id` | `str` |  |
| `message_id` | `str` |  |
| `inclusive` | `bool` |  |

#### `delete_messages_by_chat_ids(*, chat_ids: list[str]) -> int`

Delete ChatMessage rows whose chat_id is in the given list.

Args:
    chat_ids: Chat IDs whose messages should be deleted.

Returns:
    Number of rows deleted.

| Parameter | Type | Description |
|---|---|---|
| `chat_ids` | `list[str]` |  |

#### `get_chat(chat_id: str, database_name: str) -> ChatDict | None`

Get chat by ID and database.

| Parameter | Type | Description |
|---|---|---|
| `chat_id` | `str` |  |
| `database_name` | `str` |  |

#### `get_messages(chat_id: str, limit: int = 500) -> list[MessageDict]`

Get messages for a chat, ordered by timestamp.

Args:
    chat_id: Chat ID to retrieve messages for.
    limit: Maximum number of messages to return (most recent).

| Parameter | Type | Description |
|---|---|---|
| `chat_id` | `str` |  |
| `limit` | `int` |  |

#### `list_chats(database_name: str, user_id: int | None = None, status: str | None = None, limit: int = 100, offset: int = 0, scoped: bool | None = None, search: str | None = None) -> list[ChatDict]`

List chats for database with optional filters (newest first).

`search` filters by case-insensitive title substring.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `user_id` | `int \| None` |  |
| `status` | `str \| None` |  |
| `limit` | `int` |  |
| `offset` | `int` |  |
| `scoped` | `bool \| None` |  |
| `search` | `str \| None` |  |

#### `update_chat(chat_id: str, updates: dict[str, Any]) -> ChatDict`

Update chat.

| Parameter | Type | Description |
|---|---|---|
| `chat_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

## `chaoscypher_core.ports.storage_extraction_submissions`

ExtractionSubmissionStorageProtocol — storage contract for MCP chunk extraction submissions.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.extraction_submissions.ExtractionSubmissionsMixin`.
Rows are transient — created during extraction, cleared on finalization.

### `class ExtractionSubmissionStorageProtocol`

Storage protocol for MCP extraction partial results.

Manages the lifecycle of raw extraction output submitted per chunk
during MCP-driven extraction. Rows are transient -- created during
extraction, consumed during finalization, then deleted.

**Bases:** `Protocol`

**Methods:**

#### `count_extraction_submissions(source_id: str, database_name: str) -> int`

Count submitted chunks for a source.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `create_extraction_submission(data: dict[str, Any], database_name: str) -> dict[str, Any]`

Create or replace a submission for a chunk group.

| Parameter | Type | Description |
|---|---|---|
| `data` | `dict[str, Any]` |  |
| `database_name` | `str` |  |

#### `delete_extraction_submissions(source_id: str, database_name: str) -> int`

Delete all submissions for a source. Returns count deleted.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `get_extraction_submission(source_id: str, chunk_group_index: int, database_name: str) -> dict[str, Any] | None`

Get a single submission by source and chunk index.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `chunk_group_index` | `int` |  |
| `database_name` | `str` |  |

#### `list_extraction_submissions(source_id: str, database_name: str) -> list[dict[str, Any]]`

List all submissions for a source, ordered by chunk_group_index.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

## `chaoscypher_core.ports.storage_graph_snapshot`

Graph snapshot storage port for chaoscypher-core.

Defines the protocol (port) for reading and writing pre-computed
`GraphBreakdown` snapshots -- one row per database.  The concrete
SQLite implementation lives in
`chaoscypher_core.adapters.sqlite.repos.graph_snapshot`.

`SnapshotStalenessInfo` is a lightweight DTO returned by
`get_staleness_info`; it exposes only the scalar columns so callers
can decide whether to rebuild without deserialising the full JSON payload.

`GraphBreakdownQueryProtocol` is the port that
`chaoscypher_core.services.graph.snapshot.build_service.BuildGraphSnapshotService`
depends on for live aggregation queries.  The concrete implementation is
`chaoscypher_core.adapters.sqlite.repos.graph_breakdown.GraphBreakdownQueryRepository`.

### `class GraphBreakdownQueryProtocol`

Port for live graph aggregation queries used by BuildGraphSnapshotService.

The concrete implementation is
`chaoscypher_core.adapters.sqlite.repos.graph_breakdown.GraphBreakdownQueryRepository`.
BuildGraphSnapshotService depends on this protocol (not the concrete class)
so the service layer remains adapter-free at module scope.

**Bases:** `Protocol`

**Methods:**

#### `count_all_nodes(database_name: str) -> int`

Total node count for all sources in database_name (no source filter).

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `count_edges(database_name: str, source_ids: list[str]) -> int`

Count edges where both endpoints belong to source_ids.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str]` |  |

#### `count_internal_links_per_source(database_name: str, source_ids: list[str]) -> dict[str, int]`

Count internal edges per source (both endpoints share the same source_id).

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str]` |  |

#### `count_nodes(database_name: str, source_ids: list[str]) -> int`

Total node count across all source_ids in database_name.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str]` |  |

#### `count_nodes_per_source(database_name: str, source_ids: list[str]) -> dict[str, int]`

Count graph nodes per source_id.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str]` |  |

#### `count_source_attributed_nodes(database_name: str) -> int`

Node count restricted to rows carrying a source_id.

The like-for-like live counterpart of the snapshot's persisted
`total_nodes`, which is summed per source and therefore excludes
NULL-source rows.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `count_template_entities_per_source(database_name: str, source_ids: list[str]) -> dict[str, dict[str, int]]`

Count entities by template_id, grouped by source_id.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str]` |  |

#### `list_source_rows(database_name: str, source_ids: list[str] | None) -> list[SourceRowSummary]`

Return lightweight source summaries, optionally filtered by ID.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str] \| None` |  |

#### `list_template_rows(database_name: str, template_ids: list[str]) -> dict[str, TemplateSummary]`

Return template name/color keyed by template ID.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `template_ids` | `list[str]` |  |

### `class GraphSnapshotStorageProtocol`

Port for reading and writing pre-computed GraphBreakdown snapshots.

**Bases:** `Protocol`

**Methods:**

#### `get_current(database_name: str) -> GraphBreakdown | None`

Return the latest snapshot or None if no row exists.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `get_staleness_info(database_name: str) -> SnapshotStalenessInfo | None`

Return lightweight metadata (generated_at + counts) without deserializing the payload.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `upsert(breakdown: GraphBreakdown) -> None`

Insert or replace the snapshot for `breakdown.database_name`.

| Parameter | Type | Description |
|---|---|---|
| `breakdown` | `GraphBreakdown` |  |

### `class SnapshotStalenessInfo`

Lightweight metadata for staleness decisions.

Consumers compare `generated_at` + counts against the live DB to
decide whether to rebuild.  Returned by
`GraphSnapshotStorageProtocol.get_staleness_info` without
deserialising the full `payload_json`.

**Bases:** `BaseModel`

**Attributes:**

- `edge_count`: `int`
- `generated_at`: `datetime`
- `model_config`
- `node_count`: `int`

## `chaoscypher_core.ports.storage_llm_metrics`

LLMMetricsStorageProtocol — storage contract for per-call LLM metrics.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.llm_metrics.LLMMetricsMixin`.

### `class LLMMetricsStorageProtocol`

Storage protocol for LLM call metrics.

Handles CRUD and aggregation for:
- Individual LLM call metrics (per-call detail)
- Summary aggregation for source files

**Bases:** `Protocol`

**Methods:**

#### `compute_llm_summary(source_id: str, database_name: str, custom_input_cost: float = 0.0, custom_output_cost: float = 0.0) -> dict[str, Any]`

Compute aggregated LLM metrics summary for a source.

Args:
    source_id: Source ID
    database_name: Database name
    custom_input_cost: Custom cost per million input tokens (for Ollama/self-hosted)
    custom_output_cost: Custom cost per million output tokens (for Ollama/self-hosted)

Returns:
    Summary dictionary with aggregated metrics

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `custom_input_cost` | `float` |  |
| `custom_output_cost` | `float` |  |

#### `count_llm_call_metrics(database_name: str, source_id: str | None = None, success: bool | None = None) -> int`

Count LLM call metrics with optional filtering.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_id` | `str \| None` |  |
| `success` | `bool \| None` |  |

#### `create_llm_call_metric(data: dict[str, Any]) -> dict[str, Any]`

Create an LLM call metric record.

| Parameter | Type | Description |
|---|---|---|
| `data` | `dict[str, Any]` |  |

#### `create_llm_call_metrics_batch(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]`

Create multiple LLM call metrics in batch.

| Parameter | Type | Description |
|---|---|---|
| `metrics` | `list[dict[str, Any]]` |  |

#### `list_llm_call_metrics(database_name: str, source_id: str | None = None, chunk_task_id: str | None = None, operation_type: str | None = None, success: bool | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]`

List LLM call metrics with optional filtering.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_id` | `str \| None` |  |
| `chunk_task_id` | `str \| None` |  |
| `operation_type` | `str \| None` |  |
| `success` | `bool \| None` |  |
| `limit` | `int` |  |
| `offset` | `int` |  |

## `chaoscypher_core.ports.storage_sources`

SourceStorageProtocol — storage contract for source CRUD and lifecycle.

Covers Source CRUD, lifecycle stage transitions, and database-level stats.
Implemented by `SourcesMixin` + `SourceLifecycleMixin` + `SourceIndexingMixin`
in the SQLite adapter.

### `class SourceStorageProtocol`

Slim storage protocol for source CRUD and lifecycle operations.

Covers all operations on the SourceRow model itself — CRUD plus state
machine transitions. Every method reads or writes the source record.

Cascade note: `delete_source` and `delete_source_db` own the cascade
deletion of all associated chunks, citations, tags, embeddings, and
extraction data. Per-protocol delete methods (`delete_chunks_for_source`,
`delete_citations_by_source`) exist for targeted cleanup only.

**Bases:** `Protocol`

**Methods:**

#### `clear_source_commit_payload(source_id: str, database_name: str) -> None`

Clear the pending commit payload for a source.

Called by the commit handler as the LAST write inside the same
transaction that performs the graph write — if commit fails the
payload stays for the next retry; if it succeeds the payload is
discarded atomically with the source status transition.

Folding the clear into the inner commit transaction is what lets
`ImportOperationsService._run_commit` drop its outer
`adapter.transaction()` wrapper (2026-05-20 writer-lock-
contention root fix): the outer transaction was holding the
SQLite writer lock across the LLM embedding HTTP call inside
the commit service's post-inner-txn phase.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `complete_commit(source_id: str, nodes_created: int, edges_created: int, templates_created: int, source_document_node_id: str | None = None) -> None`

Mark commit stage as complete.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `nodes_created` | `int` |  |
| `edges_created` | `int` |  |
| `templates_created` | `int` |  |
| `source_document_node_id` | `str \| None` |  |

#### `complete_extraction(source_id: str, *, entities: list[dict[str, Any]], relationships: list[dict[str, Any]], detected_domain: str | None = None, forced_domain: str | None = None, forced_packs: Sequence[str] | None = None, detected_packs: Sequence[str] | None = None, domain_version: str | None = None, domain_content_hash: str | None = None, cross_chunk_filtering_log: dict[str, Any] | None = None) -> None`

Mark extraction stage as complete and persist the entity/relationship rows.

Args:
    source_id: The source ID.
    entities: Deduplicated entity dicts.
    relationships: Relationship dicts with integer `source` /
        `target` indices into `entities`.
    detected_domain: The old one-name form of `detected_packs`.
    forced_domain: The old one-name form of `forced_packs`.
    forced_packs: The packs the user forced (`[]` the floor alone;
        None when detection decided).
    detected_packs: The packs detection proposed. The source records
        `extraction_packs` (the forced, else the detected) and their
        label in `extraction_domain`.
    domain_version: Plugin version this source extracted under.
    domain_content_hash: sha256 of the plugin content at extraction time.
    cross_chunk_filtering_log: Cross-chunk filtering log dict
        surfaced by the "Filtering" UI tab.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `entities` | `list[dict[str, Any]]` |  |
| `relationships` | `list[dict[str, Any]]` |  |
| `detected_domain` | `str \| None` |  |
| `forced_domain` | `str \| None` |  |
| `forced_packs` | `Sequence[str] \| None` |  |
| `detected_packs` | `Sequence[str] \| None` |  |
| `domain_version` | `str \| None` |  |
| `domain_content_hash` | `str \| None` |  |
| `cross_chunk_filtering_log` | `dict[str, Any] \| None` |  |

#### `complete_indexing(source_id: str, chunks_count: int, embedding_model: str, embedding_dimensions: int) -> None`

Mark indexing stage as complete.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `chunks_count` | `int` |  |
| `embedding_model` | `str` |  |
| `embedding_dimensions` | `int` |  |

#### `count_sources(*, database_name: str) -> int`

Count SourceRow rows in one database.

Args:
    database_name: Database to scope to.

Returns:
    Non-negative integer count.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `create_source(source: dict[str, Any]) -> dict[str, Any]`

Create a new source.

Args:
    source: Source dictionary with all fields

Returns:
    Created source dictionary

| Parameter | Type | Description |
|---|---|---|
| `source` | `dict[str, Any]` |  |

#### `delete_all_sources(*, database_name: str) -> int`

Delete every SourceRow in one database.

Args:
    database_name: Database to scope to.

Returns:
    Number of rows deleted.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `delete_source(source_id: str, database_name: str = '') -> bool`

Delete a source and all associated SQLite data (backward-compat wrapper).

Calls `delete_source_db` then `delete_source_files` in sequence.
Prefer using the two methods separately when orchestrating inside a
transaction (so files are deleted only after the transaction commits).

Args:
    source_id: Source UUID
    database_name: Database name

Returns:
    True if deleted, False if not found

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `delete_source_db(source_id: str, database_name: str = '') -> bool`

SQL cascade delete of source and all related rows (no file deletion).

Participates in enclosing `adapter.transaction()` contexts via
`_maybe_commit()`. Callers should call `delete_source_files`
AFTER the transaction commits (files cannot be rolled back).

Args:
    source_id: Source UUID
    database_name: Database name

Returns:
    True if deleted, False if not found

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `delete_source_files(filepath: str | None) -> None`

Delete the source's on-disk files (best-effort, no raise).

Separate from `delete_source_db` so callers can delete files
outside the transaction boundary.

Args:
    filepath: Path to the source file; parent directory is removed.
        No-op if None or if the directory does not exist.

| Parameter | Type | Description |
|---|---|---|
| `filepath` | `str \| None` |  |

#### `fail_commit(source_id: str, error: str) -> None`

Mark commit stage as failed.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `error` | `str` |  |

#### `fail_extraction(source_id: str, error: str) -> None`

Mark extraction stage as failed.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `error` | `str` |  |

#### `fail_indexing(source_id: str, error: str) -> None`

Mark indexing stage as failed.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `error` | `str` |  |

#### `get_entity_uris_grouped_by_source(database_name: str, source_ids: list[str]) -> dict[str, list[str]]`

Get entity URIs grouped by source ID.

Args:
    database_name: Current database name.
    source_ids: Source IDs to query.

Returns:
    Dict mapping source_id to list of unique entity URIs.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `source_ids` | `list[str]` |  |

#### `get_source(source_id: str, database_name: str = '') -> dict[str, Any] | None`

Get a source by ID.

Args:
    source_id: Source UUID
    database_name: Database name (optional, uses default if not provided)

Returns:
    Source dictionary with keys:
        - id, database_name, version, parent_id
        - source_type, title, origin_url
        - chunk_count, total_content_length
        - embedding_model, embedding_dimensions
        - status, created_at, updated_at
        - metadata (optional dict)
        - stage_progress: `dict[str, StageProgressDict]` of per-stage
          LLM progress rows (vision, embedding, mcp_extraction).
          Empty dict when the source has no in-flight or completed
          stages.

    None if not found

Notes:
    - Heavy text columns (`commit_payload`, `full_text`) are
      excluded — fetch them via the narrow accessors
      (`get_source_commit_payload` / `get_source_full_text`).

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `get_source_by_ccx_iri(ccx_iri: str, database_name: str) -> dict[str, Any] | None`

Look up a source by its stable CCX IRI.

The lookup primitive the CCX 3.0 importer uses for upsert-by-IRI.
Scoped to `(database_name, ccx_iri)`.

Args:
    ccx_iri: The CCX 3.0 stable IRI to match.
    database_name: Database that owns the source.

Returns:
    The source row dict (including `ccx_iri` and `full_text`), or
    `None` if no row in `database_name` carries that IRI.

| Parameter | Type | Description |
|---|---|---|
| `ccx_iri` | `str` |  |
| `database_name` | `str` |  |

#### `get_source_filenames_by_ids(source_ids: list[str], database_name: str) -> dict[str, str]`

Map source id -> `filename` in one narrow query (missing ids absent).

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `list[str]` |  |
| `database_name` | `str` |  |

#### `get_source_full_text(source_id: str, database_name: str) -> str | None`

Read the full raw text for a source (narrow projection).

`get_source` excludes the heavy `full_text` column; callers
that need the raw upload text (e.g. the CCX exporter) fetch it
through this accessor instead.

Args:
    source_id: Source UUID
    database_name: Database that owns the source

Returns:
    The stored full text, or `None` if the source does not
    exist or has no full text persisted.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |

#### `get_stats(database_name: str) -> dict[str, Any]`

Get source statistics for database (counts by status).

Corresponds to the adapter-level get_stats() method on
SourceIndexingMixin.  Note this aggregates database-wide counts,
distinct from the per-source
CitationStorageProtocol.get_source_stats(source_id).

Args:
    database_name: Database to query.

Returns:
    Dict with counts by status.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `increment_source_counter(*, source_id: str, database_name: str, column: str, n: int) -> None`

Atomically increment a numeric counter column on a source row.

Best-effort: the helper in `services.quality.counters` logs and
swallows failures, so the underlying UPDATE may no-op for unknown
sources.

Args:
    source_id: Source UUID.
    database_name: Database to scope to.
    column: Name of the counter column to increment.
    n: Increment value (typically 1; may be larger for batched drops).

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `column` | `str` |  |
| `n` | `int` |  |

#### `list_enabled_source_ids() -> set[str]`

Return the ids of all enabled sources in the active database.

Single-column projection for hot per-request filters (the search
engine calls this on every keyword/semantic/hybrid search). Must
not hydrate stage progress or any wide columns.

#### `list_sources(page: int = 1, page_size: int = 50, source_type: str | None = None, status: str | None = None, enabled: str | None = None, search: str | None = None, tag_id: str | None = None, site_id: str | None = None, in_site: bool | None = None) -> tuple[list[dict[str, Any]], int]`

List sources with filtering and pagination.

Args:
    page: Page number (1-indexed)
    page_size: Items per page
    source_type: Filter by type (document/url/note/etc)
    status: Filter by status (active/archived)
    enabled: Filter by enabled status ('enabled' or 'disabled')
    search: Search in title/origin_url
    tag_id: Filter by tag assignment
    site_id: Only the pages of this imported site
    in_site: True: only pages of imported sites; False: only sources
        outside any site

Returns:
    Tuple of (sources list, total count).  Each source dict
    includes `stage_progress` (same shape as `get_source`).
    The implementation bulk-fetches stage_progress in one extra
    round trip per page to avoid N+1 queries.

| Parameter | Type | Description |
|---|---|---|
| `page` | `int` |  |
| `page_size` | `int` |  |
| `source_type` | `str \| None` |  |
| `status` | `str \| None` |  |
| `enabled` | `str \| None` |  |
| `search` | `str \| None` |  |
| `tag_id` | `str \| None` |  |
| `site_id` | `str \| None` |  |
| `in_site` | `bool \| None` |  |

#### `settle_imported_memory_links(source_ids: list[str], database_name: str) -> int`

Mark imported memories `linked` or `linked_empty` by what they attest.

A memory that attests a node is `linked`, one that attests none
`linked_empty` (`memory_linked_at` now). Run after a package import wrote their memberships. Only memory rows among
`source_ids` change. Returns how many rows changed.

| Parameter | Type | Description |
|---|---|---|
| `source_ids` | `list[str]` |  |
| `database_name` | `str` |  |

#### `start_commit(source_id: str) -> None`

Mark source as starting commit stage.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `start_extraction(source_id: str, depth: str = 'full') -> None`

Mark source as starting extraction stage.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `depth` | `str` |  |

#### `start_indexing(source_id: str) -> None`

Mark source as starting indexing stage.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |

#### `transition_source_status(source_id: str, from_status: str, to_status: str, *, database_name: str) -> bool`

Atomic compare-and-swap status transition, scoped to a single database.

Args:
    source_id: Source identifier.
    from_status: Expected current status.
    to_status: New status to set.
    database_name: Database that owns the source.

Returns:
    True if transition succeeded, False if status or database didn't
    match (or the row does not exist).

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `from_status` | `str` |  |
| `to_status` | `str` |  |
| `database_name` | `str` |  |

#### `update_source(source_id: str, updates: dict[str, Any]) -> dict[str, Any]`

Update an existing source.

Args:
    source_id: Source identifier
    updates: Dictionary of fields to update

Returns:
    Updated source dictionary

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

#### `update_source_columns(*, source_id: str, database_name: str, updates: dict[str, Any]) -> None`

Apply a partial column update to a source row.

Used by quality-counter helpers (`mark_search_indexing_*`,
`set_loader_encoding`) to stamp status / timestamp / encoding
fields without going through the heavier `update_source` path.

Args:
    source_id: Source UUID.
    database_name: Database to scope to.
    updates: Column-name -> value mapping to write.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `updates` | `dict[str, Any]` |  |

#### `update_step_progress(source_id: str, current_step: int, total_steps: int, step_description: str = '') -> None`

Update source processing progress for UI.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `current_step` | `int` |  |
| `total_steps` | `int` |  |
| `step_description` | `str` |  |

#### `upload_source(source_id: str, database_name: str, filename: str, file_content: bytes | None = None, staging_dir: str = '', extraction_depth: str = 'full', forced_domain: str | None = None, forced_packs: Sequence[str] | None = None, origin_url: str | None = None, source_type_override: str | None = None, title_override: str | None = None, content_hash: str | None = None, staged_file_path: Path | None = None, file_size: int | None = None, auto_analyze: bool = True, enable_normalization: bool | None = None, enable_vision: bool = True, content_filtering: bool = True, filtering_mode: str = SOURCE_ROW_FILTERING_MODE, enable_direction_correction: bool | None = None, protect_orphans: bool | None = None, enable_inverse_relationships: bool | None = None, max_entity_degree_override: int | None = None, confirmation_required: bool = False, site_id: str | None = None) -> dict[str, Any]`

Upload file and create source record.

Creates source with status='pending'. Returns created Source as dict.

| Parameter | Type | Description |
|---|---|---|
| `source_id` | `str` |  |
| `database_name` | `str` |  |
| `filename` | `str` |  |
| `file_content` | `bytes \| None` |  |
| `staging_dir` | `str` |  |
| `extraction_depth` | `str` |  |
| `forced_domain` | `str \| None` |  |
| `forced_packs` | `Sequence[str] \| None` |  |
| `origin_url` | `str \| None` |  |
| `source_type_override` | `str \| None` |  |
| `title_override` | `str \| None` |  |
| `content_hash` | `str \| None` |  |
| `staged_file_path` | `Path \| None` |  |
| `file_size` | `int \| None` |  |
| `auto_analyze` | `bool` |  |
| `enable_normalization` | `bool \| None` |  |
| `enable_vision` | `bool` |  |
| `content_filtering` | `bool` |  |
| `filtering_mode` | `str` |  |
| `enable_direction_correction` | `bool \| None` |  |
| `protect_orphans` | `bool \| None` |  |
| `enable_inverse_relationships` | `bool \| None` |  |
| `max_entity_degree_override` | `int \| None` |  |
| `confirmation_required` | `bool` |  |
| `site_id` | `str \| None` |  |

#### `upsert_source_by_ccx_iri(ccx_iri: str, source_dict: dict[str, Any], database_name: str) -> dict[str, Any]`

Idempotently create or update a source keyed by CCX IRI.

SELECT by `(database_name, ccx_iri)`; update the existing row's
mutable columns when found (incoming-wins, no duplicate), else create a
new source carrying the given `ccx_iri`. Used by the CCX 3.0 importer
so re-importing the same package does not duplicate sources.

Args:
    ccx_iri: Stable CCX IRI used as the merge key.
    source_dict: Source column values.
    database_name: Database to scope the upsert to.

Returns:
    The created or updated source row dict (including `ccx_iri`).

| Parameter | Type | Description |
|---|---|---|
| `ccx_iri` | `str` |  |
| `source_dict` | `dict[str, Any]` |  |
| `database_name` | `str` |  |

## `chaoscypher_core.ports.storage_tools`

ToolStorageProtocol — storage contract for system and user tools.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.tools.ToolsMixin`.

### `class ToolStorageProtocol`

Storage protocol for tool operations.

Handles CRUD for:
- System tools (built-in tools)
- User tools (user-configured tool instances)
- Tool statistics

**Bases:** `Protocol`

**Methods:**

#### `clear_all_system_tools() -> int`

Delete every SystemTool row. Returns count.

#### `clear_all_tool_statistics() -> int`

Delete every ToolStatistics row. Returns count.

#### `count_system_tools() -> int`

Count every SystemTool row. Returns non-negative int.

#### `count_user_tools(*, database_name: str) -> int`

Count UserTool rows in one database.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `create_system_tool(tool: dict[str, Any]) -> SystemToolDict`

Create or update system tool.

| Parameter | Type | Description |
|---|---|---|
| `tool` | `dict[str, Any]` |  |

#### `create_tool_statistics(stats: dict[str, Any]) -> dict[str, Any]`

Create tool statistics.

| Parameter | Type | Description |
|---|---|---|
| `stats` | `dict[str, Any]` |  |

#### `create_user_tool(tool: dict[str, Any]) -> UserToolDict`

Create user tool.

| Parameter | Type | Description |
|---|---|---|
| `tool` | `dict[str, Any]` |  |

#### `delete_all_user_tools(*, database_name: str) -> int`

Delete every UserTool row in one database. Returns count.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `delete_user_tool(tool_id: str) -> bool`

Delete user tool.

| Parameter | Type | Description |
|---|---|---|
| `tool_id` | `str` |  |

#### `get_system_tool(tool_id: str) -> SystemToolDict | None`

Get system tool by ID.

| Parameter | Type | Description |
|---|---|---|
| `tool_id` | `str` |  |

#### `get_tool_statistics(tool_type: str, tool_id: str) -> dict[str, Any] | None`

Get statistics for a tool.

| Parameter | Type | Description |
|---|---|---|
| `tool_type` | `str` |  |
| `tool_id` | `str` |  |

#### `get_user_tool(tool_id: str, database_name: str) -> UserToolDict | None`

Get user tool by ID and database.

| Parameter | Type | Description |
|---|---|---|
| `tool_id` | `str` |  |
| `database_name` | `str` |  |

#### `list_system_tools(category: str | None = None, is_active: bool | None = None) -> list[SystemToolDict]`

List all system tools with optional filters.

| Parameter | Type | Description |
|---|---|---|
| `category` | `str \| None` |  |
| `is_active` | `bool \| None` |  |

#### `list_tool_statistics() -> list[dict[str, Any]]`

List all tool statistics.

#### `list_user_tools(database_name: str, user_id: int | None = None, system_tool_id: str | None = None, is_active: bool | None = None) -> list[UserToolDict]`

List user tools for database with optional filters.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `user_id` | `int \| None` |  |
| `system_tool_id` | `str \| None` |  |
| `is_active` | `bool \| None` |  |

#### `update_system_tool(tool_id: str, updates: dict[str, Any]) -> SystemToolDict`

Update system tool.

| Parameter | Type | Description |
|---|---|---|
| `tool_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

#### `update_tool_statistics(tool_type: str, tool_id: str, updates: dict[str, Any]) -> dict[str, Any]`

Update tool statistics.

| Parameter | Type | Description |
|---|---|---|
| `tool_type` | `str` |  |
| `tool_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

#### `update_user_tool(tool_id: str, updates: dict[str, Any]) -> UserToolDict`

Update user tool.

| Parameter | Type | Description |
|---|---|---|
| `tool_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

## `chaoscypher_core.ports.storage_triggers`

TriggerStorageProtocol — storage contract for workflow triggers.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.triggers.TriggersMixin`.

### `class TriggerStorageProtocol`

Storage protocol for trigger operations.

Handles CRUD for:
- Triggers (event triggers)
- Trigger executions (history)

**Bases:** `Protocol`

**Methods:**

#### `clear_all_trigger_executions() -> int`

Delete every TriggerExecutionRow across databases. Returns count.

#### `count_triggers(*, database_name: str) -> int`

Count Trigger rows in one database.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `create_trigger(trigger: dict[str, Any]) -> TriggerDict`

Create trigger.

| Parameter | Type | Description |
|---|---|---|
| `trigger` | `dict[str, Any]` |  |

#### `delete_all_triggers(*, database_name: str) -> int`

Delete every Trigger row in one database. Returns count.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `delete_trigger(trigger_id: str) -> bool`

Delete trigger.

| Parameter | Type | Description |
|---|---|---|
| `trigger_id` | `str` |  |

#### `get_executions(trigger_id: str, limit: int = 10) -> list[dict[str, Any]]`

Get recent executions for a trigger.

| Parameter | Type | Description |
|---|---|---|
| `trigger_id` | `str` |  |
| `limit` | `int` |  |

#### `get_trigger(trigger_id: str, database_name: str) -> TriggerDict | None`

Get trigger by ID and database.

| Parameter | Type | Description |
|---|---|---|
| `trigger_id` | `str` |  |
| `database_name` | `str` |  |

#### `list_triggers(database_name: str, event_source: str | None = None, enabled: bool | None = None) -> list[TriggerDict]`

List triggers for database with optional filters.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `event_source` | `str \| None` |  |
| `enabled` | `bool \| None` |  |

#### `update_trigger(trigger_id: str, updates: dict[str, Any]) -> TriggerDict`

Update trigger.

| Parameter | Type | Description |
|---|---|---|
| `trigger_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

## `chaoscypher_core.ports.storage_workflow_executions`

WorkflowExecutionStorageProtocol — storage contract for workflow execution tracking.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.workflow_executions.WorkflowExecutionsMixin`.

### `class WorkflowExecutionStorageProtocol`

Storage protocol for workflow execution tracking operations.

Handles CRUD for:
- Workflow execution records (status, timing, outputs)
- Step execution records (per-step tracking)

Separated from WorkflowStorageProtocol per Interface Segregation Principle.
Workflow definitions are stable (rarely change), but executions are frequent
and runtime-focused.

Note:
    All status values are plain strings (framework-agnostic).
    Backend DTOs may use Enums, but storage layer uses strings.

**Bases:** `Protocol`

**Methods:**

#### `clear_all_workflow_executions() -> int`

Delete every WorkflowExecution row across databases. Returns count.

#### `complete_execution(execution_id: str, outputs: dict[str, Any], duration_ms: int) -> None`

Mark execution as completed.

Args:
    execution_id: Execution ID
    outputs: Execution outputs
    duration_ms: Total execution duration in milliseconds

Note:
    Sets status="completed", completed_at=now, outputs, duration_ms.

| Parameter | Type | Description |
|---|---|---|
| `execution_id` | `str` |  |
| `outputs` | `dict[str, Any]` |  |
| `duration_ms` | `int` |  |

#### `complete_step_execution(step_execution_id: str, outputs: dict[str, Any], duration_ms: int) -> None`

Mark step execution as completed.

Args:
    step_execution_id: Step execution ID
    outputs: Step outputs
    duration_ms: Step execution duration in milliseconds

Note:
    Sets status="completed", outputs, completed_at=now, duration_ms.

| Parameter | Type | Description |
|---|---|---|
| `step_execution_id` | `str` |  |
| `outputs` | `dict[str, Any]` |  |
| `duration_ms` | `int` |  |

#### `create_execution(execution_data: dict[str, Any]) -> dict[str, Any]`

Create workflow execution record.

Args:
    execution_data: Dict containing:
        - id: str - Execution ID
        - workflow_id: str - Workflow ID
        - triggered_by: str - Trigger source ("user", "schedule", "trigger")
        - trigger_id: Optional[str] - Trigger ID if triggered by trigger
        - parent_execution_id: Optional[str] - Parent execution if nested
        - inputs: Dict[str, Any] - Execution inputs
        - status: str - Initial status (typically "pending")
        - created_at: Optional[datetime] - Creation timestamp

Returns:
    Created execution as dict (with generated fields)

| Parameter | Type | Description |
|---|---|---|
| `execution_data` | `dict[str, Any]` |  |

#### `create_step_execution(step_execution_id: str, execution_id: str, step_id: str) -> dict[str, Any]`

Create step execution record.

Args:
    step_execution_id: Step execution ID
    execution_id: Parent execution ID
    step_id: Step ID from workflow definition

Returns:
    Created step execution as dict

Note:
    Initial status is "pending", inputs=\{\} (will be updated later).

| Parameter | Type | Description |
|---|---|---|
| `step_execution_id` | `str` |  |
| `execution_id` | `str` |  |
| `step_id` | `str` |  |

#### `fail_execution(execution_id: str, error_message: str, failed_step_id: str | None, duration_ms: int) -> None`

Mark execution as failed.

Args:
    execution_id: Execution ID
    error_message: Error message describing failure
    failed_step_id: ID of step that failed (None if failed before first step)
    duration_ms: Duration until failure in milliseconds

Note:
    Sets status="failed", error_message, failed_step_id, completed_at=now, duration_ms.

| Parameter | Type | Description |
|---|---|---|
| `execution_id` | `str` |  |
| `error_message` | `str` |  |
| `failed_step_id` | `str \| None` |  |
| `duration_ms` | `int` |  |

#### `fail_step_execution(step_execution_id: str, error_message: str, duration_ms: int) -> None`

Mark step execution as failed.

Args:
    step_execution_id: Step execution ID
    error_message: Error message describing failure
    duration_ms: Duration until failure in milliseconds

Note:
    Sets status="failed", error_message, completed_at=now, duration_ms.

| Parameter | Type | Description |
|---|---|---|
| `step_execution_id` | `str` |  |
| `error_message` | `str` |  |
| `duration_ms` | `int` |  |

#### `get_execution(execution_id: str) -> dict[str, Any] | None`

Get execution details by ID.

Args:
    execution_id: Execution ID

Returns:
    Execution dict or None if not found

| Parameter | Type | Description |
|---|---|---|
| `execution_id` | `str` |  |

#### `get_step_executions(execution_id: str) -> list[dict[str, Any]]`

Get step executions for a workflow execution.

Args:
    execution_id: Execution ID

Returns:
    List of step execution dicts, ordered by created_at asc (execution order)

| Parameter | Type | Description |
|---|---|---|
| `execution_id` | `str` |  |

#### `get_workflow_executions(workflow_id: str, limit: int = 100) -> list[dict[str, Any]]`

Get execution history for a workflow.

Args:
    workflow_id: Workflow ID
    limit: Maximum number of executions to return (default: 100)

Returns:
    List of execution dicts, ordered by created_at desc (most recent first)

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |
| `limit` | `int` |  |

#### `list_active_executions(workflow_id: str) -> list[dict[str, Any]]`

Return executions with status in \{pending, queued, running\} for a workflow.

Args:
    workflow_id: Workflow ID

Returns:
    List of active execution dicts (empty if none).

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |

#### `update_current_step(execution_id: str, step_id: str) -> None`

Update currently executing step.

Args:
    execution_id: Execution ID
    step_id: Step ID currently being executed

| Parameter | Type | Description |
|---|---|---|
| `execution_id` | `str` |  |
| `step_id` | `str` |  |

#### `update_status(execution_id: str, status: str) -> None`

Update execution status.

Args:
    execution_id: Execution ID
    status: New status ("pending", "running", "completed", "failed", "cancelled")

Note:
    If status is "running" and started_at is None, sets started_at to now.

| Parameter | Type | Description |
|---|---|---|
| `execution_id` | `str` |  |
| `status` | `str` |  |

#### `update_step_status(step_execution_id: str, status: str) -> None`

Update step execution status.

Args:
    step_execution_id: Step execution ID
    status: New status ("pending", "running", "completed", "failed", "skipped")

Note:
    If status is "running" and started_at is None, sets started_at to now.

| Parameter | Type | Description |
|---|---|---|
| `step_execution_id` | `str` |  |
| `status` | `str` |  |

## `chaoscypher_core.ports.storage_workflows`

WorkflowStorageProtocol — storage contract for workflow definitions.

Split from the legacy `ports/storage.py` god file on 2026-04-23.
Implemented by `chaoscypher_core.adapters.sqlite.mixins.workflows.WorkflowsMixin`.

### `class WorkflowStorageProtocol`

Storage protocol for workflow operations.

Handles CRUD for:
- Workflow definitions
- Workflow steps
- Workflow statistics
- Workflow executions

**Bases:** `Protocol`

**Methods:**

#### `clear_all_workflow_statistics() -> int`

Delete every WorkflowStatistics row across databases. Returns count.

#### `clear_all_workflow_steps() -> int`

Delete every WorkflowStep row across databases. Returns count.

#### `count_workflows(*, database_name: str) -> int`

Count Workflow rows in one database.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `create_workflow(workflow: dict[str, Any]) -> WorkflowDict`

Create new workflow. Returns created workflow with generated ID.

| Parameter | Type | Description |
|---|---|---|
| `workflow` | `dict[str, Any]` |  |

#### `create_workflow_safe(*, workflow: dict[str, Any]) -> dict[str, Any]`

Create a Workflow row, raising ConflictError on duplicate name.

Semantics: `INSERT`; if the unique constraint on
`(database_name, name)` trips, catch the SQLAlchemy
`IntegrityError` and raise
`chaoscypher_core.exceptions.ConflictError` with the offending
name in `details`. Keeps `sqlalchemy.exc` out of the service
layer.

Args:
    workflow: Dict with id, database_name, name, and any other
        persistable Workflow columns.

Returns:
    Dict form of the created row (per the dict-over-entity
    contract).

Raises:
    ConflictError: Duplicate workflow name in the database.

| Parameter | Type | Description |
|---|---|---|
| `workflow` | `dict[str, Any]` |  |

#### `create_workflow_statistics(stats: dict[str, Any]) -> dict[str, Any]`

Create workflow statistics.

| Parameter | Type | Description |
|---|---|---|
| `stats` | `dict[str, Any]` |  |

#### `create_workflow_step(step: dict[str, Any]) -> WorkflowStepDict`

Create new workflow step.

| Parameter | Type | Description |
|---|---|---|
| `step` | `dict[str, Any]` |  |

#### `delete_all_workflows(*, database_name: str) -> int`

Delete every Workflow row in one database. Returns count.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `delete_workflow(workflow_id: str) -> bool`

Delete workflow. Returns True if deleted, False if not found.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |

#### `delete_workflow_step(step_id: str) -> bool`

Delete workflow step.

| Parameter | Type | Description |
|---|---|---|
| `step_id` | `str` |  |

#### `delete_workflow_steps(workflow_id: str) -> int`

Delete all steps for a workflow. Returns count of deleted steps.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |

#### `get_workflow(workflow_id: str) -> WorkflowDict | None`

Get workflow by ID. Returns None if not found.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |

#### `get_workflow_statistics(workflow_id: str) -> dict[str, Any] | None`

Get statistics for a workflow.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |

#### `get_workflow_statistics_totals(*, database_name: str) -> dict[str, int]`

Sum execution counters across every workflow in one database.

Single aggregate SELECT (SUM over total/successful/failed/cancelled
executions). Use instead of per-workflow `get_workflow_statistics`
round-trips when only the global totals are needed.

Returns:
    Dict with `total_executions`, `successful_executions`,
    `failed_executions`, `cancelled_executions` — all 0 when
    the database has no statistics rows.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |

#### `get_workflow_step(step_id: str) -> WorkflowStepDict | None`

Get workflow step by ID.

| Parameter | Type | Description |
|---|---|---|
| `step_id` | `str` |  |

#### `get_workflow_steps(workflow_id: str) -> list[WorkflowStepDict]`

Get all steps for a workflow, ordered by step_number.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |

#### `list_workflows(database_name: str, category: str | None = None, is_system: bool | None = None, is_active: bool | None = None, expose_as_ai_tool: bool | None = None) -> list[WorkflowDict]`

List workflows with optional filters.

| Parameter | Type | Description |
|---|---|---|
| `database_name` | `str` |  |
| `category` | `str \| None` |  |
| `is_system` | `bool \| None` |  |
| `is_active` | `bool \| None` |  |
| `expose_as_ai_tool` | `bool \| None` |  |

#### `list_workflows_by_ids(ids: list[str]) -> list[WorkflowDict]`

Batch-fetch workflows by ID.

Single SELECT ... WHERE id IN (...). Use to avoid N+1 patterns.

Returns:
    List of workflow dicts in the same shape as get_workflow();
    order is not guaranteed to match the input ID order. Missing
    IDs are silently omitted from the result. Returns [] for
    an empty input list.

| Parameter | Type | Description |
|---|---|---|
| `ids` | `list[str]` |  |

#### `update_workflow(workflow_id: str, updates: dict[str, Any]) -> WorkflowDict`

Update workflow. Returns updated workflow.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

#### `update_workflow_statistics(workflow_id: str, updates: dict[str, Any]) -> dict[str, Any]`

Update workflow statistics.

| Parameter | Type | Description |
|---|---|---|
| `workflow_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |

#### `update_workflow_step(step_id: str, updates: dict[str, Any]) -> WorkflowStepDict`

Update workflow step.

| Parameter | Type | Description |
|---|---|---|
| `step_id` | `str` |  |
| `updates` | `dict[str, Any]` |  |
