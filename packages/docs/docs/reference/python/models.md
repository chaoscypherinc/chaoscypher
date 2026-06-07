---
title: "Models & DTOs"
---

# Models & DTOs

Core Pydantic models used across the ChaosCypher library. These are pure data transfer objects with no ORM or framework dependencies.

## `chaoscypher_core.models`

Core models for chaoscypher-engine.

Pure Pydantic models (no SQLModel coupling) for use across the engine.
These models define the data structures for nodes, edges, templates,
suggestions, and other entities.

### `class BatchEmbedResult`

Result from LLMProvider.batch_embed().

**Bases:** `BaseModel`

**Attributes:**

- `embeddings`: `list[list[float]]`
- `failed`: `int`
- `model_config`
- `provider`: `str`
- `total`: `int`

### `class ChunkingResult`

Result from Engine.chunk_document().

Contains metadata about the chunking operation including source ID
and chunk counts. Use the source_id to pass to subsequent pipeline
stages like `engine.index_source()` or `engine.commit()`.

**Bases:** `BaseModel`

**Attributes:**

- `analysis_depth`: `str`
- `model_config`
- `source_id`: `str`
- `total_groups`: `int`
- `total_small_chunks`: `int`

### `class ChunksResult`

Result from ChunkingService.create_chunks().

Contains the actual chunks and groups produced by the chunking service,
plus summary counts. Returned by `ChunkingService.create_chunks()` and
accepted by `ChunkingService.store_chunks()`.

**Bases:** `BaseModel`

**Attributes:**

- `chunks_filtered`: `int`
- `chunks_skipped_by_depth`: `int`
- `hierarchical_groups`: `list[dict[str, Any]]`
- `model_config`
- `normalize_drops`: `int`
- `prestrip_lines_removed`: `int`
- `small_chunks`: `list[dict[str, Any]]`
- `total_groups`: `int`
- `total_original_chunks`: `int`
- `total_original_groups`: `int`
- `total_small_chunks`: `int`

### `class DatabaseInfo`

Database metadata.

Returned by DatabaseProtocol.get_database() and used across core, cortex,
and CLI packages. Contains the union of fields needed by all consumers.

**Bases:** `BaseModel`

**Methods:**

#### `from_path(name: str, path: str, app_db_filename: str = 'app.db') -> DatabaseInfo`

Create DatabaseInfo from a database directory path.

Inspects the database file on disk to populate `exists`, `size`,
and `last_modified`.

Args:
    name: Database name.
    path: Path to the database directory.
    app_db_filename: Database filename inside the directory.

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` |  |
| `path` | `str` |  |
| `app_db_filename` | `str` |  |

**Attributes:**

- `created_at`: `datetime | None`
- `description`: `str | None`
- `exists`: `bool`
- `last_modified`: `datetime | None`
- `model_config`
- `name`: `str`
- `path`: `str`
- `size`: `int`

### `class DatabaseStats`

Database statistics returned by Engine.get_stats().

**Bases:** `BaseModel`

**Attributes:**

- `data_dir`: `str`
- `database_name`: `str`
- `edges`: `int`
- `model_config`
- `nodes`: `int`
- `templates`: `int`

### `class Edge`

Graph edge (relationship) - complete version for engine use.

Includes all fields needed by GraphRepository including timestamps.

**Bases:** `BaseModel`

**Attributes:**

- `created_at`: `datetime`
- `id`: `str`
- `label`: `str`
- `model_config`
- `properties`: `dict[str, Any]`
- `source_node_id`: `str`
- `target_node_id`: `str`
- `template_id`: `str`
- `updated_at`: `datetime`

### `class EdgeCreate`

Edge creation data.

Used when creating new edges via GraphRepository.create_edge()

**Bases:** `BaseModel`

**Attributes:**

- `label`: `str`
- `model_config`
- `properties`: `dict[str, Any]`
- `source_id`: `str | None`
- `source_node_id`: `str`
- `target_node_id`: `str`
- `template_id`: `str`

### `class EdgeUpdate`

Edge update data.

Used when updating existing edges via GraphRepository.update_edge()
All fields are optional - only provided fields will be updated.

**Bases:** `BaseModel`

**Attributes:**

- `label`: `str | None`
- `model_config`
- `properties`: `dict[str, Any] | None`

### `class EdgeWithNodes`

Edge with hydrated source and target node objects.

Returned by `GraphRepository.list_edges(with_nodes=True)` to eliminate
the O(N) `get_node()`-in-a-loop antipattern.  Both endpoint nodes are
batch-loaded in a single IN-query and attached here so callers can access
`edge.source_node.label` without issuing additional round trips.

Example:

```
edges = graph_repo.list_edges(with_nodes=True)
for edge in edges:
    print(f"{edge.source_node.label} → {edge.target_node.label}")
```

**Bases:** `Edge`

**Attributes:**

- `model_config`
- `source_node`: `Node | None`
- `target_node`: `Node | None`

### `class EmbedResult`

Result from LLMProvider.embed().

**Bases:** `BaseModel`

**Attributes:**

- `embedding`: `list[float]`
- `model_config`
- `provider`: `str`
- `usage`: `TokenUsage | None`

### `class EngineSearchResult`

Individual search result from Engine.search().

**Bases:** `BaseModel`

**Attributes:**

- `content`: `str | None`
- `id`: `str`
- `label`: `str`
- `model_config`
- `result_type`: `str`
- `score`: `float`
- `snippet`: `str` — Best text preview regardless of result type.

Returns content for chunks (with label fallback), label for nodes.
- `source`: `str | None`
- `template_id`: `str | None`

### `class ExtractionResult`

Result from ChunkingService.process() standalone extraction.

Contains extracted entities, relationships, and domain detection metadata.
Use `model_dump_json()` for JSON output or attribute access for fields.

Example:
    >>> result = await ChunkingService().process(text)
    >>> print(result.domain, result.domain_confidence)
    >>> print(result.model_dump_json(indent=2))

**Bases:** `BaseModel`

**Attributes:**

- `cached_embeddings`: `list[Any]`
- `chunk_ids`: `list[list[str]]`
- `domain`: `str`
- `domain_confidence`: `float`
- `entities`: `list[dict[str, Any]]`
- `filtering_log`: `dict[str, Any] | None`
- `model_config`
- `relationships`: `list[dict[str, Any]]`

### `class HealthReport`

Combined health report from LLMProvider.check_health().

**Bases:** `BaseModel`

**Attributes:**

- `chat`: `HealthResult`
- `model_config`

### `class HealthResult`

Health check result for a single provider.

**Bases:** `BaseModel`

**Attributes:**

- `embedding_dimensions`: `int | None`
- `error`: `str | None`
- `model`: `str | None`
- `model_config`
- `provider`: `str | None`
- `response_time_ms`: `int | None`
- `status`: `str`

### `class IndexingResult`

Result from Engine.index_source().

**Bases:** `BaseModel`

**Attributes:**

- `chunks_count`: `int`
- `embedding_dimensions`: `int`
- `embedding_model`: `str`
- `model_config`

### `class LLMChatResponse`

Response from LLMProvider.chat().

**Bases:** `BaseModel`

**Attributes:**

- `content`: `str`
- `finish_reason`: `str | None`
- `instance_id`: `str | None`
- `is_stream`: `bool`
- `model_config`
- `provider`: `str`
- `stream`: `Any | None`
- `thinking`: `str | None`
- `tool_calls`: `list[dict[str, Any]] | None`
- `usage`: `TokenUsage | None`

### `class Node`

Graph node (entity) - complete version for engine use.

Includes all fields needed by GraphRepository including timestamps.

**Bases:** `BaseModel`

**Attributes:**

- `created_at`: `datetime`
- `embedding`: `list[float] | None`
- `entity_type`: `str | None`
- `id`: `str`
- `label`: `str`
- `model_config`
- `position`: `NodePosition | None`
- `properties`: `dict[str, Any]`
- `source_id`: `str | None`
- `template_id`: `str`
- `updated_at`: `datetime`

### `class NodeCreate`

Node creation data.

Used when creating new nodes in the graph via GraphRepository.create_node()

**Bases:** `BaseModel`

**Attributes:**

- `embedding`: `list[float] | None`
- `entity_type`: `str | None`
- `label`: `str`
- `model_config`
- `position`: `NodePosition | None`
- `properties`: `dict[str, Any]`
- `source_id`: `str | None`
- `template_id`: `str`

### `class NodePosition`

Position of a node in graph canvas (optional, for UI).

**Bases:** `BaseModel`

**Attributes:**

- `model_config`
- `x`: `float`
- `y`: `float`

### `class NodeUpdate`

Node update data.

Used when updating existing nodes via GraphRepository.update_node()
All fields are optional - only provided fields will be updated.

**Bases:** `BaseModel`

**Attributes:**

- `embedding`: `list[float] | None`
- `label`: `str | None`
- `model_config`
- `position`: `NodePosition | None`
- `properties`: `dict[str, Any] | None`

### `class PaginatedResult`

Paginated result from Engine list operations.

Wraps a list of domain models with pagination metadata.
Items in `data` are typed domain models (Node, Edge, or Template),
not raw dicts.

Example:
    result = engine.list_nodes(page=1, page_size=20)
    for node in result.data:
        print(node.label)
    print(f"Page \{result.page\} of \{result.total_pages\}")

**Bases:** `BaseModel`

**Attributes:**

- `data`: `list[Any]`
- `has_next`: `bool`
- `has_prev`: `bool`
- `page`: `int`
- `page_size`: `int`
- `total`: `int`
- `total_pages`: `int`

### `class ProcessingResult`

Result from Engine.process_document() and Engine.add_document().

**Bases:** `BaseModel`

**Attributes:**

- `edges`: `list[str]`
- `model_config`
- `nodes`: `list[str]`
- `source_id`: `str`
- `status`: `str | None`
- `templates`: `list[str]`

### `class PropertyDefinition`

Definition of a property type in a template.

**Bases:** `BaseModel`

**Attributes:**

- `allowed_node_types`: `list[str] | None`
- `default_value`: `Any | None`
- `description`: `str | None`
- `display_name`: `str`
- `enum_values`: `list[str] | None`
- `model_config`
- `name`: `str`
- `property_type`: `PropertyType`
- `required`: `bool`
- `validation_pattern`: `str | None`

### `class PropertyType`

Types of properties that can be attached to nodes/edges.

**Bases:** `StrEnum`

**Attributes:**

- `BOOLEAN`
- `DATE`
- `DATETIME`
- `EMAIL`
- `ENUM`
- `FLOAT`
- `INTEGER`
- `JSON`
- `NODE_REFERENCE`
- `NODE_REFERENCE_LIST`
- `STRING`
- `TEXT`
- `URL`

### `class RebuildResult`

Result from Engine.rebuild_indexes().

**Bases:** `BaseModel`

**Attributes:**

- `chunks_indexed`: `int`
- `model_config`
- `nodes_with_embeddings`: `int`
- `total_nodes`: `int`

### `class Source`

Unified source document model - from upload through committed.

Single model representing a document throughout its entire lifecycle:
upload → indexing → extraction → commit to graph.

**Bases:** `BaseModel`

**Attributes:**

- `chunk_count`: `int`
- `commit_completed_at`: `datetime | None`
- `commit_edges_created`: `int`
- `commit_nodes_created`: `int`
- `created_at`: `datetime`
- `database_name`: `str`
- `embedding_model`: `str | None`
- `enabled`: `bool`
- `error_message`: `str | None`
- `extraction_completed_at`: `datetime | None`
- `extraction_depth`: `str | None`
- `extraction_entities_count`: `int`
- `extraction_relationships_count`: `int`
- `file_size`: `int | None`
- `file_type`: `str | None`
- `filename`: `str`
- `filepath`: `str | None`
- `id`: `str`
- `indexing_completed_at`: `datetime | None`
- `model_config`
- `origin_url`: `str | None`
- `source_type`: `str | None`
- `status`: `str`
- `title`: `str | None`
- `updated_at`: `datetime`
- `user_metadata`: `dict[str, Any] | None`

### `class SourceErrorStage`

Stage at which a source's pipeline failed.

Single source of truth for the `error_stage` column on SourceRow.
Values match the strings written by `_apply_failure`,
`mark_source_exhausted`, and `fail_url_fetch` today, so this
enum requires no data migration.

The Cortex retry endpoint compares `error_stage` to these values
to decide which lifecycle status to reset to. The Cortex abort path
translates the in-flight `SourceStatus` (e.g. EXTRACTING) to the
matching `SourceErrorStage` (e.g. EXTRACTION) before persisting.

**Bases:** `StrEnum`

**Attributes:**

- `COMMIT`
- `EXTRACTION`
- `INDEXING`
- `RECOVERY_EXHAUSTED`
- `URL_FETCH`

### `class SourceStatus`

Source processing lifecycle statuses.

Normal lifecycle for text-only sources:

```
PENDING → INDEXING → INDEXED → EXTRACTING → EXTRACTED → COMMITTING → COMMITTED
```

For image-bearing sources with the vision pipeline enabled:

```
PENDING → INDEXING → VISION_PENDING → INDEXING (resume) → INDEXED → EXTRACTING → …
```

`VISION_PENDING` is set by the indexing handler after it has enqueued
per-page vision tasks and returns without blocking.  The vision finalizer
transitions the source back to `INDEXING` (CAS from `VISION_PENDING`)
and enqueues an `OP_INDEX_DOCUMENT` task with `resume_after_vision=True`
so the resume run merges descriptions into the chunks and reaches
`INDEXED`.  The recovery scanner uses `VISION_PENDING` as its gate
for the vision-recovery branch.

**Bases:** `StrEnum`

**Attributes:**

- `AWAITING_CONFIRMATION`
- `COMMITTED`
- `COMMITTING`
- `ERROR`
- `EXTRACTED`
- `EXTRACTING`
- `INDEXED`
- `INDEXING`
- `MCP_EXTRACTING`
- `PENDING`
- `VISION_PENDING`

### `class StepToolType`

Type of tool used in a workflow step.

**Bases:** `StrEnum`

**Attributes:**

- `SYSTEM_TOOL`
- `USER_TOOL`
- `WORKFLOW`

### `class Template`

Node or edge template definition - complete version for engine use.

Includes property definitions, system flag, and timestamps.

**Bases:** `BaseModel`

**Attributes:**

- `color`: `str | None`
- `created_at`: `datetime`
- `description`: `str | None`
- `icon`: `str | None`
- `id`: `str`
- `is_system`: `bool`
- `model_config`
- `name`: `str`
- `properties`: `list[PropertyDefinition]`
- `source_id`: `str | None`
- `template_type`: `str`
- `updated_at`: `datetime`

### `class TemplateCreate`

Template creation data.

Used when creating new templates via GraphRepository.create_template()

**Bases:** `BaseModel`

**Attributes:**

- `color`: `str | None`
- `description`: `str | None`
- `icon`: `str | None`
- `model_config`
- `name`: `str`
- `properties`: `list[PropertyDefinition]`
- `source_id`: `str | None`
- `template_type`: `str`

### `class TemplateUpdate`

Template update data.

Used when updating existing templates via GraphRepository.update_template()
All fields are optional - only provided fields will be updated.

**Bases:** `BaseModel`

**Attributes:**

- `color`: `str | None`
- `description`: `str | None`
- `embedding`: `list[float] | None`
- `embedding_dimensions`: `int | None`
- `embedding_model`: `str | None`
- `icon`: `str | None`
- `model_config`
- `name`: `str | None`
- `properties`: `list[PropertyDefinition] | None`

### `class TokenUsage`

Token usage statistics from an LLM call.

**Bases:** `BaseModel`

**Attributes:**

- `cost_usd`: `float | None`
- `input_tokens`: `int`
- `model_config`
- `output_tokens`: `int`
- `total_tokens`: `int`

### `class ToolResult`

Result from LLMProvider.execute_tool().

**Bases:** `BaseModel`

**Attributes:**

- `model_config`
- `result`: `Any`
- `tool_name`: `str`

### `class UserPrincipal`

Normalized user principal for ACL checks.

Accepts dict, object, or None at service boundaries; service code
only ever sees this dataclass.

**Attributes:**

- `id`: `int | None`
- `is_admin`: `bool`
