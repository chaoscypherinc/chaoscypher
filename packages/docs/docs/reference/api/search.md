---
title: Search API
description: Full-text keyword search, semantic vector search, and hybrid GraphRAG search across indexed documents and knowledge graph entities at /api/v1/search.
---

# Search API

Full-text and semantic search across indexed documents and knowledge graph entities.

**Base path:** `/api/v1/search`

:::tip[Related pages]

- [User guide: Search](../../user-guide/search.md) — search modes, re-ranking, and index management from the UI and CLI
- [Architecture: Indexing & Embeddings](../../architecture/extraction-pipeline/indexing.md) — how FTS5 and sqlite-vec indices are built and maintained

:::

---

## Search

```
GET /api/v1/search
```

Run a search query using keyword, semantic, or hybrid strategies.

### Query Parameters

| Parameter     | Type   | Required | Default   | Description                                                    |
|---------------|--------|----------|-----------|----------------------------------------------------------------|
| `q`           | string | Yes      | --        | Search query string                                            |
| `search_type` | string | No       | `keyword` | Search strategy: `keyword`, `semantic`, or `hybrid`            |
| `limit`       | int    | No       | `50`      | Maximum number of results (min: 1, capped at server max: 1000) |

### Search Types

| Type       | Description                                              |
|------------|----------------------------------------------------------|
| `keyword`  | Full-text keyword search against the FTS index           |
| `semantic` | Vector similarity search using embeddings                |
| `hybrid`   | Semantic search with automatic keyword fallback if no vector results are found |

:::note[Limit behavior]

When `limit` is not provided, the server default page size (50) is used.
Values exceeding the server maximum (1000) are clamped automatically.

:::

### Examples

```bash
# Keyword search
curl "http://localhost:8080/api/v1/search?q=machine+learning&search_type=keyword"

# Semantic search
curl "http://localhost:8080/api/v1/search?q=artificial+intelligence&search_type=semantic"

# Hybrid search (semantic with keyword fallback)
curl "http://localhost:8080/api/v1/search?q=neural+networks&search_type=hybrid"

# Keyword search with a custom result limit
curl "http://localhost:8080/api/v1/search?q=deep+learning&search_type=keyword&limit=10"
```

### Response

`200 OK`

```json
{
  "data": [
    {
      "result_type": "chunk",
      "score": 0.89,
      "node": null,
      "chunk": {
        "chunk_id": "chunk-abc123",
        "source_id": "src-def456",
        "chunk_index": 5,
        "content": "Machine learning is a subset of artificial intelligence...",
        "page_number": 3,
        "section": "Introduction",
        "filename": "paper.pdf"
      }
    },
    {
      "result_type": "node",
      "score": 0.75,
      "node": {
        "id": "node-789ghi",
        "template_id": "concept",
        "label": "Machine Learning",
        "properties": {},
        "position": null,
        "embedding": null,
        "source_id": null,
        "created_at": "2026-01-15T10:30:00",
        "updated_at": "2026-01-15T10:30:00",
        "edge_count": null,
        "incoming_edge_count": null,
        "outgoing_edge_count": null,
        "citation_count": null,
        "relationship_type_count": null
      },
      "chunk": null
    }
  ],
  "type": "keyword"
}
```

#### SearchResponse

| Field  | Type               | Description                                          |
|--------|--------------------|------------------------------------------------------|
| `data` | `SearchResult[]`   | Array of search results                              |
| `type` | string             | Search type used: `keyword`, `semantic`, or `hybrid`  |

#### SearchResult

| Field         | Type                | Description                                       |
|---------------|---------------------|---------------------------------------------------|
| `result_type` | string              | `"node"` or `"chunk"`                             |
| `score`       | float               | Relevance score                                   |
| `node`        | `NodeResponse|null` | Node data (present when `result_type` is `node`)  |
| `chunk`       | `ChunkResult|null`  | Chunk data (present when `result_type` is `chunk`) |

#### ChunkResult

| Field         | Type        | Description                            |
|---------------|-------------|----------------------------------------|
| `chunk_id`    | string      | Unique chunk identifier                |
| `source_id`   | string      | Parent source document identifier      |
| `chunk_index` | int         | Position of the chunk within the source |
| `content`     | string      | Chunk text content                     |
| `page_number` | int or null | Page number (if available)             |
| `section`     | string or null | Section heading (if available)       |
| `filename`    | string      | Source filename                        |

---

## Index Statistics

```
GET /api/v1/search/stats
```

Returns statistics about the current search indexes.

### Example

```bash
curl "http://localhost:8080/api/v1/search/stats"
```

### Response

`200 OK`

```json
{
  "fulltext_doc_count": 1500,
  "vector_index_size": 1500,
  "vector_dimension": 1024
}
```

#### SearchStatistics

| Field                | Type | Description                              |
|----------------------|------|------------------------------------------|
| `fulltext_doc_count` | int  | Number of documents in the fulltext index |
| `vector_index_size`  | int  | Number of vectors in the sqlite-vec index  |
| `vector_dimension`   | int  | Dimensionality of stored vectors          |

---

## Index Status

```
GET /api/v1/search/indexes/status
```

Check whether the search indexes need rebuilding. Returns the current model and dimension configuration alongside live index row counts. Use this before calling `POST /api/v1/search/indexes` to decide whether a full reindex is required.

### Example

```bash
curl "http://localhost:8080/api/v1/search/indexes/status"
```

### Response

`200 OK`

```json
{
  "needs_rebuild": false,
  "embedding_model": "nomic-embed-text",
  "vector_dimensions": 768,
  "fulltext": {
    "document_count": 1500
  },
  "vector": {
    "vector_count": 1500,
    "dimensions": 768
  },
  "error": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `needs_rebuild` | bool | `true` when the persisted model/dimensions no longer match the index — a full reindex is required |
| `embedding_model` | string \| null | Name of the embedding model the index was built against |
| `vector_dimensions` | int \| null | Dimensionality of vectors currently in the index |
| `fulltext` | object \| null | Fulltext index stats (e.g. `{"document_count": int}`) |
| `vector` | object \| null | Vector index stats (e.g. `{"vector_count": int, "dimensions": int}`) |
| `error` | string \| null | Populated when index stats could not be read (e.g. table missing) |

---

## Rebuild Indexes

```
POST /api/v1/search/indexes
```

Rebuilds search indexes from all graph nodes and document chunks. Auto-detects whether embeddings need regeneration based on the current embedding model configuration.

- **Fast rebuild (FTS-only):** When embeddings are current, only the fulltext index is rebuilt. Returns `200 OK` immediately.
- **Full rebuild (with re-embedding):** When the embedding model or dimensions have changed, queues a background job to regenerate all embeddings. Returns `202 Accepted` with a task ID.

### Use Cases

- After bulk imports
- When an index is corrupted
- After manual graph modifications
- After changing the embedding model (triggers full re-embedding)

### Example

```bash
curl -X POST "http://localhost:8080/api/v1/search/indexes"
```

### Response (Fast Rebuild)

`200 OK`

```json
{
  "success": true,
  "total_nodes": 1500,
  "nodes_with_embeddings": 1200,
  "chunks_indexed": 3400,
  "message": "Search indexes rebuilt successfully"
}
```

### Response (Full Rebuild with Re-embedding)

`202 Accepted`

```json
{
  "task_id": "task-rebuild-abc123",
  "success": true,
  "total_nodes": 1500,
  "nodes_with_embeddings": 0,
  "chunks_indexed": 3400,
  "message": "Embeddings need regeneration. Queued re-embedding for 1500 nodes and 3400 chunks."
}
```

:::info[Tracking progress]

When a `202` response is returned, use [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task) to monitor the re-embedding job.

:::

#### RebuildIndexResponse

| Field                   | Type   | Description                                    |
|-------------------------|--------|------------------------------------------------|
| `success`               | bool   | Whether the rebuild completed successfully      |
| `total_nodes`           | int    | Total nodes processed                          |
| `nodes_with_embeddings` | int    | Nodes that have vector embeddings               |
| `chunks_indexed`        | int    | Number of document chunks indexed (default: 0)  |
| `message`               | string | Human-readable status message                   |
| `task_id`               | string? | Background task ID (only present for `202` responses) |

---

## Generate Missing Embeddings

```
POST /api/v1/search/embeddings
```

Queues embedding generation for all graph nodes that do not yet have vector embeddings. Processing happens asynchronously in the background via the Operations queue.

:::tip[Monitor progress]

Use [`GET /api/v1/search/stats`](#index-statistics) to check `vector_index_size` as embeddings are generated.

:::

### Use Cases

- Existing nodes created before auto-embedding was enabled
- Nodes imported from external sources without embeddings

### Example

```bash
curl -X POST "http://localhost:8080/api/v1/search/embeddings"
```

### Response

`200 OK`

```json
{
  "success": true,
  "total_nodes": 1500,
  "nodes_queued": 300,
  "message": "Queued 300 nodes for embedding generation"
}
```

#### GenerateEmbeddingsResponse

| Field          | Type   | Description                                          |
|----------------|--------|------------------------------------------------------|
| `success`      | bool   | Whether the queuing completed successfully            |
| `total_nodes`  | int    | Total nodes in the graph                             |
| `nodes_queued` | int    | Number of nodes queued for embedding generation       |
| `message`      | string | Human-readable status message                         |
