---
id: glossary
title: Glossary
description: Domain terms used across Chaos Cypher docs.
---

# Glossary

**Source** — an uploaded document (PDF, markdown, audio, video, image, etc.). Disambiguate from "source code" when both might appear.

**Loader** — a plugin that converts an uploaded file into normalizable text. Built-in loaders cover PDF, plain text, markdown, HTML, reStructuredText, DOCX, XLSX, PPTX, EPUB, audio, video, image, archives, and structured data (CSV/JSON).

**Normalization** — preprocessing that fixes encoding, removes structural noise, and produces clean text from a loaded source.

**Chunk** (small chunk) — a ~225-token segment used as the unit of retrieval. Chunks have stable IDs derived from content hashes.

**Group** (hierarchical group) — a ~900-token aggregation of small chunks used as the unit of entity extraction. Groups summarize and provide context across multiple chunks.

**Indexing** — the stage that produces embeddings and full-text indexes from chunks. Embeddings go to `sqlite-vec`; full-text goes to FTS5; both live in `app.db`.

**Entity extraction** — the stage that asks an LLM to find nodes and edges in groups. Constrained by domain templates.

**Domain** — a `.jsonld` file declaring node templates, edge templates, and prompts for a specific subject area (e.g., research, recipes, philosophy). 19 built-in domains; users can add custom domains.

**Node template / Edge template / Graph template** — reusable schema fragments. Distinct: node templates describe entities, edge templates describe relationships, graph templates compose the two for a domain.

**Deduplication** — merging proposed entities/edges that refer to the same real-world thing. Uses similarity over embeddings + property matching.

**Relationship mapping** — converting LLM-proposed relationships to canonical edges with stable IDs.

**Commit** — the final transactional stage that writes deduplicated nodes and edges to the graph and updates the source's status to `committed`.

**Status flow** — the lifecycle of a source: `pending → indexing → indexed → extracting → extracted → committing → committed`, plus `error` and `mcp_extracting`.

**Citation** — a back-reference from a generated answer to the chunk(s) that grounded it.

**MCP** — [Model Context Protocol](https://modelcontextprotocol.io/). Chaos Cypher exposes 31 MCP tools for AI clients (19 read, 12 write).

**Operations queue / LLM queue** — the two named worker queues. Operations (8 concurrent) handles indexing/dedup/commit; LLM (1 concurrent) handles extraction LLM calls.

**Lexicon Hub** — Chaos Cypher's hosted domain-template registry. (Preview — see banner on the [Lexicon Hub pages](./lexicon-hub/index.md).)

**CCX** — Chaos Cypher eXchange, the canonical backup/export format.

**RAG** — Retrieval-Augmented Generation. Chaos Cypher's chat surface uses RAG with hybrid (BM25 + vector) retrieval.

**FTS5** — SQLite Full-Text Search version 5. The backend for keyword search.

**BM25** — the ranking algorithm used by FTS5. Default field weights: label 3.0, properties 1.0, searchable_text 0.5.

**RRF** — Reciprocal Rank Fusion. The algorithm that merges BM25 and vector ranks into a single hybrid score.

**MRL** — Matryoshka Representation Learning. Used by the default embedding model (Qwen/Qwen3-Embedding-0.6B) to support truncated embeddings.
