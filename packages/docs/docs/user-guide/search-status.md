---
id: search-status
title: Search Status
description: How to read the search-status badge on each source — pending, indexed, degraded, or failed — and what each state means for search results.
---

# Search status

After a source commits to the knowledge graph, its content still has
to make it into the **vector search index** before semantic search and
RAG retrieval can find it. That extra step is best-effort and runs
outside the commit transaction (so a search-index hiccup never blocks
graph data from landing). The `vector_indexing_status` field tracks
where the source is in that hand-off.

You'll see the status folded into the one status chip on each source's
row in the Sources list and in the source detail header: an enabled source
shows **Active** (green) while `pending`, `indexed` or unknown, **Search
retrying** (amber) while `degraded` and **Search failed** (red) once
`failed`; a disabled source shows **Disabled** whatever its index state.

## The four states

| State | Chip | Meaning |
|-------|-------|---------|
| `pending` | Active (green) | The commit just landed and the post-transaction indexing call is queued. The source's nodes / chunks are *almost* searchable; usually resolves to `indexed` within a second or two. |
| `indexed` | Active (green) | Both node and chunk vector writes succeeded. The source is fully searchable via FTS5 and sqlite-vec. `vector_indexed_at` is stamped with the time it landed. |
| `degraded` | Search retrying (amber) | At least one indexing call raised an error. The commit pipeline enqueued a retry in `pending_search_index` for the orphan-sweep worker to handle. The source's graph data is fine; only its searchability is partial. |
| `failed` | Search failed (red) | The sweep worker exhausted its retry budget. The retry queue entry was removed and the source needs operator attention. |

## What each state means for search results

- `pending` and `indexed` — the source contributes to all search modes (`hybrid`, `keyword`, `semantic`).
- `degraded` — the source contributes to whichever modes have working indexes. If only chunks failed, you still get node search; if only nodes failed, you still get RAG search.
- `failed` — the source is missing from the vector index entirely. Keyword (FTS5) search may still surface it depending on which call raised, but you should treat the source as effectively invisible to semantic search until you fix it.

## Recovering a degraded or failed source

A `degraded` source usually self-heals — the orphan-sweep worker drains
the retry queue on a schedule and most failures are transient (Valkey
hiccup, embedding model warming up).

A `failed` source needs a manual nudge: **re-extract it** (the chip's
tooltip says the same). Re-extraction resets the status to `pending` and
runs the indexing hand-off again. Rebuilding the search indexes
(`chaoscypher source rebuild-search`, `POST /api/v1/search/indexes`)
re-indexes the embeddings already stored and never changes a source's
`vector_indexing_status`, so it does not clear a `failed` chip.

If a single source lands in `failed` again after a re-extract, file an
issue with the source ID and the counter values from the
[Pipeline flow & quality counters](data-quality.md).

## Counters reset on Re-extract

`vector_indexing_status` is one of the fields that resets when you
**Re-extract** a source: it goes back to `pending`, and
`vector_indexed_at` clears, so the post-commit indexing call gets a
fresh chance.

## See also

- [Pipeline flow & quality counters](data-quality.md) — the counters that track silent drops
- [Search](search.md) — search modes and how RAG retrieval works
