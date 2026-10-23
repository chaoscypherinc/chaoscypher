---
id: search
title: Search
description: Search your Chaos Cypher knowledge graph with three modes — keyword (FTS5), semantic vector, and hybrid GraphRAG that combines graph traversal with semantic similarity.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Search

Chaos Cypher provides three search modes that work across your indexed documents and knowledge graph.

:::note[API port]

The `curl` examples on this page use `http://localhost:8080`, which is the API port for the multi-container development stack. The all-in-one container (the primary install) serves the API on port **80** instead — use `http://localhost/api/v1/...` there.

:::

## Why GraphRAG

Pairing a knowledge graph with vector search beats embeddings alone on multi-hop questions — where the answer comes from connecting several facts, not matching one passage. Independent, peer-reviewed research on the GraphRAG approach finds:

- **~7% higher multi-hop retrieval** than a state-of-the-art embedding model — [HippoRAG 2, ICML 2025](https://arxiv.org/abs/2502.14802)
- **~24% more accurate on complex multi-hop reasoning** vs. reranked vector RAG (53% vs 43%) in an independent benchmark — [GraphRAG-Bench, ICLR 2026](https://arxiv.org/abs/2506.05690)
- **6–13× faster serving** than iterative retrieval, at equal-or-better recall — [HippoRAG, NeurIPS 2024](https://arxiv.org/abs/2405.14831)

:::note
These figures are from independent, peer-reviewed research on the GraphRAG _approach_ (linked) — not ChaosCypher benchmarks. GraphRAG's edge is strongest on multi-hop questions; plain vector search is fine for single-fact lookups.
:::

## Search Modes

<Tabs>
<TabItem value="web-ui" label="Web UI">


1. Press **Ctrl+K** (or **Cmd+K** on macOS), or click the search bar at the top of the page
2. Type your query — entity matches appear instantly under **Entities**
3. Use the arrow keys (↑ / ↓) to highlight a result and **Enter** to open it, or click any row
4. Press **Esc** to dismiss the palette

> The palette is also a command launcher — start with `>` for commands or `/` to send the query straight to chat.

![Command palette showing entity search results](/img/screenshots/search-results.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Hybrid search (default)
chaoscypher source search "machine learning algorithms"

# Keyword search (fast, no LLM needed)
chaoscypher source search "machine learning" --mode keyword

# Semantic search (vector similarity)
chaoscypher source search "artificial intelligence applications" --mode semantic

# Limit results
chaoscypher source search "neural networks" --limit 5
```

</TabItem>
<TabItem value="python" label="Python">


```python
import asyncio
from chaoscypher_core import Engine

async def main():
    async with Engine("./data/databases/default") as engine:
        # Hybrid search (default)
        results = await engine.search("machine learning algorithms")
        for r in results:
            print(f"{r.score:.3f}  {r.label}")

asyncio.run(main())
```

</TabItem>
<TabItem value="api" label="API">


```bash
# Keyword search
curl "http://localhost:8080/api/v1/search?q=machine+learning&search_type=keyword"

# Semantic search
curl "http://localhost:8080/api/v1/search?q=artificial+intelligence&search_type=semantic"

# Hybrid search
curl "http://localhost:8080/api/v1/search?q=neural+networks&search_type=hybrid"
```

</TabItem>
</Tabs>


### Keyword Search

Traditional full-text search using SQLite FTS. Keyword mode searches the node index only (labels and properties), so its results are always graph nodes. A second full-text index covers document chunk text; hybrid search uses it.

Best for: finding specific terms, names, or exact phrases in your knowledge graph's entities.

### Semantic Search

Vector similarity search using embeddings. Finds content that is semantically similar to your query, even without exact word matches.

Best for: conceptual queries, finding related content, when you don't know the exact terminology.

### Hybrid Search

Runs keyword and semantic search together over both nodes and chunks and fuses the two rankings by reciprocal rank (rank, not score, is merged, because BM25 and cosine scores are on different scales; the top of each arm scores 1.0). Semantic matches must exceed the minimum similarity threshold (default: 0.55) to be included. Very short queries (under 3 characters) use keyword search only.

Best for: general-purpose search that combines both approaches.

### GraphRAG Search

Graph-enhanced retrieval that fuses knowledge graph traversal with vector search. When a question involves connections across multiple documents or entities, GraphRAG finds answers that pure vector search misses.

**How it works:**

1. Embeds your query and finds knowledge graph nodes with the most similar embeddings (seed entities) — there is no separate named-entity-extraction step
2. Filters those matches by a minimum similarity threshold to keep the strongest seed entities
3. Traverses graph relationships using Personalized PageRank
4. Retrieves provenance-linked document chunks
5. Fuses graph and vector results using Reciprocal Rank Fusion

GraphRAG is available as the `graphrag_search` tool in AI chat and as an MCP tool. The chat system automatically prioritizes GraphRAG when your database has a knowledge graph with extracted entities.

**Seeing what GraphRAG retrieved:** in the web chat, every answer that used tools shows a **Tool Activity** panel beneath it. Expand the `graphrag_search` entry to inspect its input and full result, including `retrieval_stats` — the retrieval mode (`full_graphrag`, `vector_only`, or `keyword_only`), seed entities found, PageRank entities explored, and provenance/vector chunk counts. A mode of `vector_only` usually means no graph entities matched the query (for example, when extraction hasn't run yet).

Best for: multi-hop questions, finding connections across documents, questions that span multiple topics or entities.

## Search Results

Results come in two types:

### Chunk Results

Matches from document source chunks. Each result includes:

| Field | Description |
|-------|-------------|
| **content** | The matching text passage |
| **score** | Relevance score |
| **filename** | Source document name |
| **page_number** | Page location (when available) |
| **section** | Section heading (when available) |
| **source_id** | Link back to the source document |

### Node Results

Matches from knowledge graph nodes. Each result includes:

| Field | Description |
|-------|-------------|
| **label** | Entity name |
| **score** | Relevance score |
| **template_id** | Entity type |
| **properties** | Entity properties |

## Embeddings

Vector embeddings are generated locally on the CPU using [sentence-transformers](https://www.sbert.net/). No LLM provider or API key is needed for embeddings — they run entirely offline after the initial model download.

The default model is **Qwen3 Embedding 0.6B** (1024 dimensions). You can change the model in [`settings.yaml`](../getting-started/configuration.md):

```yaml
embedding:
  model: Qwen/Qwen3-Embedding-0.6B

search:
  vector_dimensions: 1024
```

The model downloads automatically on first use and is cached locally.

## Configuration

Key search settings in [`settings.yaml`](../getting-started/configuration.md):

```yaml
embedding:
  model: Qwen/Qwen3-Embedding-0.6B

search:
  enable_vector_search: true
  vector_dimensions: 1024
  min_similarity_threshold: 0.55
  max_search_results: 100
  enable_rerank: true
  rerank_model_name: Alibaba-NLP/gte-reranker-modernbert-base
```

### GraphRAG Tuning

The parameters in the `graphrag` section of `settings.yaml` control the GraphRAG pipeline. The defaults were chosen from the GraphRAG literature and testing across database sizes — most users never need to change them.

| Setting | Default | Description |
|---------|---------|-------------|
| `seed_similarity_threshold` | `0.3` | Minimum cosine similarity for a graph entity to qualify as a PageRank seed. Lower values cast a wider net but may introduce noise. |
| `ppr_top_k` | `20` | Number of top-scoring entities from Personalized PageRank to include in graph context. Higher values give the LLM more structural context at the cost of token budget. |
| `ppr_damping` | `0.85` | PageRank damping factor. Higher means more exploration away from seeds; lower keeps results closer to directly matched entities. |
| `max_triples` | `40` | Maximum relationship triples included in the graph context. Was 200 until 2026-10-02: a block of every edge of the top nodes was found to be ignored by chat models. |
| `include_unevidenced_edges` | `false` | Let relationships no cited sentence states (kept by the extraction flagged `unevidenced`, see [Entity Extraction](../architecture/extraction-pipeline/entity-extraction.md#a-relationship-without-evidence-is-kept-flagged)) into the graph context: PageRank, the seed paths and the relationship lines, ranked after every evidenced line before the `max_triples` cap. Off, the chat reads only relationships a sentence of the source states. Switch it in **Settings → General → Chat context** ("Use unevidenced relationships in chat context"). A benchmark lever like the others (recorded in each row's run settings, settable with `SWEEP_SET`). |
| `triple_selection` | `connected` | Which edges become relationship lines: `connected` keeps edges between the top PageRank entities or touching a seed entity, ranked by PageRank; `all_edges` is every edge in node order. |
| `entity_descriptions` | `true` | Each entity line carries its extracted description (`name: description`), kept to `entity_description_chars` (400) at a sentence boundary, a sentence dropped only when every content word of it is in a kept one (`clean_descriptions`, `true`; the drops are counted in `retrieval_stats.descriptions_dropped`). Was 160 characters cut mid-word until 2026-10-09. |
| `seed_paths` | `true` | Lists the relation chains (one or two hops) between the seed entities the query matched; `paths_to_top` (`false`) would add chains from each seed to the other top entities. |
| `explain_lines` | `true` | One sentence telling the model what the graph lines are. |
| `graph_block_last` | `true` | Documents first, graph lines after them. |
| `question_gate` | `true` | Only entities and relationships sharing a significant word with the query are kept; seeds and their edges always stay. |
| `keyword_seeds` | `true` | A name or alias the query spells out seeds PageRank by keyword match, ahead of the vector matches. |
| `rank_chunks_by_seeds` | `true` | Chunks are ordered by how many seed entities they mention. |
| `ask_for_citations` | `true` | The benchmark's grounded-chat prompt asks for the app's `[[cite:Cn:Sm|filename]]` markers, as the app's system prompt does. |
| `vector_overfetch_multiplier` | `3` | When searching for seed entities, fetch this multiple of the seed limit from the vector index to account for non-entity results that need filtering. |
| `max_graph_nodes` | `50000` | Safety limit. If the graph exceeds this, PageRank is skipped (too expensive) and search falls back to vector-only mode. |
| `question_aware_descriptions` | `true` | Within `entity_description_chars`, the description sentences that share a word with the question come first (in stored order), then the rest. |
| `question_parts` | `false` | A two-part question (", and what did he say?") is searched whole and part by part in the chunk index, the hits interleaved so each part keeps its share; a one-part question is searched once. |

The graph context's text summary (the triple list a tool result carries) is
budgeted to about 4,000 characters and cut **by rank**: its lines are ordered
by standing (a line between named entities before one touching a reference
such as "the count", connected before orphan, evidenced before unevidenced;
the selection order kept within each), whole lines are kept from the top, and
the rest are counted (`... (N more triples omitted)`). Until October 2026 the
cut took the first 4,000 bytes of the selection order.

One related cap lives outside the `graphrag` section: `batching.graphrag_edge_query_limit` (default `50`) limits how many edges per direction are loaded for each top-ranked entity when assembling the graph context.

#### Question shapes

Before it assembles the graph context, GraphRAG decides the question's *shape*: `lookup` (a fact about one thing), `aggregate` (everyone or everything sharing a relation: a list), `path` (how two named things are connected) or `global` (the big picture). The shape sets the budget and the graph block, never whether the graph is used: an aggregate question gets the named entity's relations grouped by label, each row with the sentence that states it; a path question gets the shortest relation chains between the two entities over the whole graph, each hop with its sentence, or a line saying none was found. In this release a global question runs the lookup assembly with the global shape's levers (the community block comes later).

| Setting | Default | Description |
|---------|---------|-------------|
| `router` | `rules` | How the shape is decided. `rules` (the default) decides from the question's named entities and cue words ("connected", "every", "themes", ...) and treats what it cannot decide as a lookup: no extra model call, no added latency. `rules_then_llm` asks the chat model one short extra question (a JSON answer, temperature 0) only when the rules do not decide. `llm` always asks the model; `off` treats every question as a lookup. |
| `shape_levers` | aggregate and path: `{vector_reserve: 4, evidence_chunks: 6}`; global: `{vector_reserve: 4, evidence_chunks: 2}` | Lever values applied on top of the settings for one shape. A bundle may set any lever read after the shape is known (not the seed or PageRank settings, not `router`). |
| `evidence_chunks` | `0` | Chunks the graph block cites (an aggregate row's or a walk hop's sentence) that enter the chunk budget by right, so every `[C3:S2]` marker on a graph line resolves. The shipped `shape_levers` raise it for aggregate and path questions. |
| `max_hops` | `3` | The longest relation chain a path question's walk may have (1 to 4). |
| `max_walks` | `6` | The walks a path question lists in all, across its pairs of named entities: each pair's shortest first. A walk is a shortest chain or one a relation longer, never longer. |
| `aggregate_per_label` | `12` | The rows an aggregate block lists per relation before it says how many more there are. |

The quoted sentences of the aggregate and walk lines are document text: the model reads them inside the same untrusted-document fence as the chunks.

The default router adds no latency. Set `router: rules_then_llm` to let the chat model decide the questions the rules cannot, at one short extra model call each.

### Re-ranking

When enabled, re-ranking applies only to the `search_chunks` tool used by AI chat and MCP clients — not to the keyword, semantic, or hybrid modes described above (the Web UI search bar, CLI, and `/api/v1/search`). For chunk candidates it retrieves, the re-ranker evaluates each one against your query with a cross-encoder model and reorders them by true relevance rather than raw similarity scores. Chaos Cypher defaults to `Alibaba-NLP/gte-reranker-modernbert-base`, a ModernBERT-based cross-encoder (149M params, ~600MB) that scores ~56.2 NDCG@10 on the BEIR benchmark. Any HuggingFace cross-encoder model can be used via the `rerank_model_name` setting.

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_rerank` | `true` | Enable cross-encoder re-ranking |
| `rerank_model_name` | `Alibaba-NLP/gte-reranker-modernbert-base` | HuggingFace cross-encoder model for re-ranking |
| `rerank_candidate_multiplier` | `3` | Fetch 3x candidates before re-ranking |
| `rerank_min_candidates` | `15` | Minimum candidates to consider |

## Index Management

### Rebuild Indexes

If search results seem stale or incomplete, rebuild the indexes:

<Tabs>
<TabItem value="web-ui" label="Web UI">


Go to **Settings** → **Search** and click **Rebuild Search Indexes**.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher source rebuild-search
# or for a specific database
chaoscypher source rebuild-search --database my-project
```

The command auto-detects whether embeddings need regeneration: if the embedding model or dimensions changed, it regenerates all embeddings (slower); otherwise it rebuilds the indexes from stored embeddings (fast).

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST http://localhost:8080/api/v1/search/indexes
```

</TabItem>
</Tabs>


This rebuilds the fulltext (keyword) and vector (sqlite-vec) indexes from all nodes, and re-indexes all document chunk embeddings.

### Generate Missing Embeddings

Generate vector embeddings for nodes that don't have them:

<Tabs>
<TabItem value="web-ui" label="Web UI">


The Web UI has no action that embeds nodes missing an embedding. **Settings** → **Search** → **Rebuild Search Indexes** re-indexes the embeddings already stored; it regenerates embeddings only after the embedding model or dimensions change. Use the call on the **API** tab instead.

</TabItem>
<TabItem value="cli" label="CLI">


Embedding generation is available via the API.

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST http://localhost:8080/api/v1/search/embeddings
```

</TabItem>
</Tabs>


This call is synchronous: it embeds every node that lacks an embedding, in batches, and returns only when the run finishes, so on a large graph the request stays open for the whole run (allow for proxy timeouts). A batch the embedding provider rejects is counted as failed in the response message while the other batches are still written; call it again to retry the nodes that are still missing embeddings.

### Index Statistics

Check the current state of your search indexes:

<Tabs>
<TabItem value="web-ui" label="Web UI">


Search statistics are displayed in **Settings** → **Search**.

</TabItem>
<TabItem value="cli" label="CLI">


Index statistics are available via the API.

</TabItem>
<TabItem value="api" label="API">


```bash
curl http://localhost:8080/api/v1/search/stats
```

</TabItem>
</Tabs>


Returns: fulltext document count, vector index size, and vector dimensions.

## See also

- [Architecture: Indexing & Embeddings](../architecture/extraction-pipeline/indexing.md) — how FTS5 and sqlite-vec indices are built, embedding storage format, and hybrid search internals
- [API reference: Search](../reference/api/search.md) — endpoint details for keyword, semantic, and hybrid search queries; index rebuild and embedding generation endpoints
