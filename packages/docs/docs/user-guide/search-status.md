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

You'll see the status as a `SearchStatusBadge` next to each source on
the Sources list and on the source detail page.

## The four states

| State | Badge | Meaning |
|-------|-------|---------|
| `pending` | grey | The commit just landed and the post-transaction indexing call is queued. The source's nodes / chunks are *almost* searchable; usually resolves to `indexed` within a second or two. |
| `indexed` | green | Both node and chunk vector writes succeeded. The source is fully searchable via FTS5 and sqlite-vec. `vector_indexed_at` is stamped with the time it landed. |
| `degraded` | amber | At least one indexing call raised an error. The commit pipeline enqueued a retry in `pending_search_index` for the orphan-sweep worker to handle. The source's graph data is fine; only its searchability is partial. |
| `failed` | red | The sweep worker exhausted its retry budget. The retry queue entry was removed and the source needs operator attention. |

## What each state means for search results

- `pending` and `indexed` — the source contributes to all search modes (`hybrid`, `keyword`, `semantic`).
- `degraded` — the source contributes to whichever modes have working indexes. If only chunks failed, you still get node search; if only nodes failed, you still get RAG search.
- `failed` — the source is missing from the vector index entirely. Keyword (FTS5) search may still surface it depending on which call raised, but you should treat the source as effectively invisible to semantic search until you fix it.

## Recovering a degraded or failed source

A `degraded` source usually self-heals — the orphan-sweep worker drains
the retry queue on a schedule and most failures are transient (Valkey
hiccup, embedding model warming up).

A `failed` source needs a manual nudge. The most reliable fix is to
**rebuild the search indexes** for the database — `chaoscypher source
rebuild-search` from the CLI or `POST /api/v1/search/indexes` from the
API. The rebuild runs through every committed source and regenerates
the missing entries.

If a single source consistently lands in `failed` after a rebuild,
that's a bug — file an issue with the source ID and the counter values
from the [Data Quality tab](data-quality.md).

## Counters reset on Re-extract

`vector_indexing_status` is one of the fields that resets when you
**Re-extract** a source: it goes back to `pending`, and
`vector_indexed_at` clears, so the post-commit indexing call gets a
fresh chance.

## See also

- [Data Quality tab](data-quality.md) — the counters that track silent drops
- [Search](search.md) — search modes and how RAG retrieval works
