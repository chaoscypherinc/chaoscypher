---
slug: graphrag-teardown-naive-rag
title: "GraphRAG Teardown: What the Graph Actually Adds to Naive RAG"
authors: [denis]
tags: [graphrag, rag, ai, python]
date: 2026-08-04
draft: true
description: A step-by-step teardown of vector RAG vs GraphRAG over three real AI agent papers -- what each path finds, what it misses, and when the graph earns its keep.
---

Take three well-known AI agent papers -- chain-of-thought prompting, ReAct, and Reflexion -- drop them into a knowledge base, and ask a question that spans all three: *"How does Reflexion's self-reflection loop relate to chain-of-thought prompting?"*

Naive vector RAG will hand you a competent paragraph about chain-of-thought. The connection you actually asked about -- that ReAct interleaved chain-of-thought-style reasoning traces with actions, and that Reflexion layered verbal self-reflection on top of ReAct-style agents, with two authors carrying across both papers -- is spread across three documents and stated explicitly in none of them.

This post is a teardown of both retrieval paths over exactly that corpus. Not a leaderboard: a walk through what each one mechanically does, where the vector path stalls, and where the graph earns its keep.

<!-- truncate -->

## What This Post Is (And Isn't)

This is a **mechanism teardown**, not a benchmark. You will not find a table here claiming Chaos Cypher scores X% against a baseline's Y%. Retrieval quality depends enormously on corpus, chunking, embedding model, and question shape, and a number produced by us on a corpus chosen by us would tell you very little.

What you *will* find: the exact retrieval steps both paths take, the real relationships in this three-paper corpus that separate them, and the retrieval statistics Chaos Cypher emits on every query so you can run the same comparison on your own documents and read the receipts yourself.

It's worth stating the honest version of the research picture up front. The most thorough public analysis of this question -- [*When to use Graphs in RAG: A Comprehensive Analysis for Graph Retrieval-Augmented Generation*](https://arxiv.org/abs/2506.05690) (the GraphRAG-Bench paper, ICLR 2026) -- exists precisely because graph retrieval **frequently underperforms vanilla RAG** on many real-world tasks. Its contribution is mapping *when* graph structure pays off: the benefit concentrates in complex, multi-hop reasoning, and largely evaporates on simple fact retrieval, where a well-tuned vector index is already the right tool.

That matches our own position, and it's why Chaos Cypher fuses the two rather than replacing one with the other. The graph is not a better vector index. It answers a different question.

In plain English: graphs help with questions that require connecting things, not with questions that require finding one thing.

## The Corpus and the Question

Three papers, all publicly available:

| Paper | arXiv | Venue |
|---|---|---|
| Chain-of-Thought Prompting Elicits Reasoning in Large Language Models (Wei et al.) | [2201.11903](https://arxiv.org/abs/2201.11903) | NeurIPS 2022 |
| ReAct: Synergizing Reasoning and Acting in Language Models (Yao et al.) | [2210.03629](https://arxiv.org/abs/2210.03629) | ICLR 2023 |
| Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al.) | [2303.11366](https://arxiv.org/abs/2303.11366) | NeurIPS 2023 |

The relationships between them are real and checkable. ReAct builds directly on chain-of-thought reasoning, interleaving reasoning traces with actions rather than producing reasoning alone. Reflexion builds on that line of work, adding a verbal self-reflection buffer that feeds an agent's own failures back into its next attempt. And the author lists overlap: Shunyu Yao and Karthik Narasimhan appear on both ReAct and Reflexion.

Now the question: **"How does Reflexion's self-reflection loop relate to chain-of-thought prompting?"**

Note what makes this hard. No single chunk in any of the three papers contains the answer. The chain runs chain-of-thought → ReAct → Reflexion, and each link lives in a different document.

## Round 1: What Naive Vector RAG Does

The naive path is three steps, and it is genuinely good at what it does:

1. **Embed the question** into a vector.
2. **Compare** that vector against every chunk embedding by cosine similarity.
3. **Return** the top *k* closest chunks.

Ask our question and the retrieved chunks will be dominated by passages that *talk like the question*. The phrase "self-reflection" pulls hard toward Reflexion's abstract and method section. "Chain-of-thought prompting" pulls toward the Wei et al. paper. You get strong chunks from both ends of the chain.

What you almost certainly do not get is the middle. ReAct is the load-bearing link -- it's what connects reasoning traces to acting agents -- but a ReAct passage doesn't necessarily *sound* like a question about Reflexion and chain-of-thought. Its vocabulary is its own: interleaving, action space, observation. Lexical and semantic similarity to the query is mediocre, so it ranks below chunks that merely restate the question's own terms.

The failure mode is worth naming precisely: **vector search has no representation of the fact that these three documents are related.** It sees text, scores text, returns text. Two chunks that describe the same idea in different vocabulary are far apart; two chunks that share vocabulary but no actual relationship are close together. There is no edge between ReAct and Reflexion in a vector index, because a vector index has no edges at all.

And it fails quietly. There's no error, no "I found the endpoints but not the path." You get a fluent answer built from the two ends of a three-link chain, and it reads exactly as confident as a complete one would.

In plain English: vector search finds text that sounds like your question, which is not the same as text that answers it.

## Round 2: What GraphRAG Does

Chaos Cypher's GraphRAG pipeline runs seven steps. The graph half and the vector half both run, and the last step merges them.

**Step 1 — Embed the query.** Identical starting point to the naive path.

**Step 2 — Match seed entities.** Before touching document chunks, the query vector is compared against *entity* embeddings in the knowledge graph. Entities clearing a cosine-similarity floor (`seed_similarity_threshold`, default `0.3`) become seeds, up to a `seed_limit`. For our question, expect seeds like `Reflexion`, `self-reflection`, and `chain-of-thought prompting` -- the anchor points, not the answer.

**Step 3 — Personalized PageRank.** Standard PageRank finds globally important nodes. *Personalized* PageRank restarts its random walk at your seeds, turning a global importance score into a query-specific relevance score. Chaos Cypher runs rustworkx's compiled power iteration with a damping factor of `0.85` (`ppr_damping`), in-process -- no external graph database. Seed weights come from the Step 2 similarity scores, so the walk is biased toward the entities that actually matter for *this* question.

This is where ReAct surfaces. It was never a strong seed, because it isn't semantically close to the query. It scores well because it sits structurally between two entities that *are* -- one or two hops from both `Reflexion` and `chain-of-thought`. The top `ppr_top_k` entities (default `20`) carry forward.

**Step 4 — Assemble graph context.** Seeds, discovered entities, and the relationship triples connecting them are collected into a structured context (capped by `max_triples`, default `200`) and passed to the model alongside the text. The model gets the *map*, not just the territory. On this corpus, extraction should yield edges along the lines of `Reflexion --builds_on--> ReAct`, `ReAct --extends--> chain-of-thought prompting`, `Shunyu Yao --authored--> ReAct`, `Shunyu Yao --authored--> Reflexion` -- the exact labels depend on what extraction pulled from your documents, which is the subject of the limitations section below.

**Step 5 — Retrieve provenance chunks.** The first of two retrieval paths. For every entity the graph surfaced, Chaos Cypher looks up which document chunks that entity was originally extracted from. This is what makes the graph auditable rather than decorative: the ReAct passage arrives with the evidence for why it was retrieved.

**Step 6 — Retrieve vector chunks.** The second path is ordinary hybrid search -- semantic plus keyword -- across all chunks. This is the naive path, running unchanged, inside the graph pipeline. It catches relevant passages that never produced graph entities.

**Step 7 — Merge and rank.** Two independently ranked lists, two incompatible scoring scales. Chaos Cypher merges them with Reciprocal Rank Fusion ([Cormack, Clarke & Buettcher, 2009](https://dl.acm.org/doi/10.1145/1571941.1572114)), which discards raw scores and uses only rank position: each chunk scores the sum of `1 / (k + rank)` across every list it appears in, with `k = 60` per the original paper.

The property that matters: a chunk appearing in *both* lists collects from both. A passage ranked 5th by provenance and 8th by vector search will often outrank one that's 1st by vector search and absent from provenance. Evidence confirmed by two independent signals beats evidence confirmed by one.

In plain English: the graph finds the path between the things you asked about, the vector index finds text that sounds like your question, and fusion trusts what both agree on.

<!-- screenshot: search results panel showing entities with relevance scores and type badges after a multi-hop query -->

## Reading the Receipts

The part that makes this a teardown rather than a story: every GraphRAG query returns a `retrieval_stats` block alongside its results. Six fields, all observable:

| Field | What it tells you |
|---|---|
| `mode` | Which pipeline actually ran (see below) |
| `seed_entities_found` | How many graph entities anchored the walk |
| `ppr_entities_explored` | How many entities PageRank scored |
| `provenance_chunks` | Chunks retrieved via graph provenance |
| `vector_chunks` | Chunks retrieved via hybrid search |
| `deduplicated` | Chunks the two paths agreed on and were merged |

`mode` is the honest one. The pipeline degrades rather than failing, and it tells you which rung it landed on:

- **`full_graphrag`** -- seeds found, PageRank succeeded. Graph context, provenance chunks, vector chunks, fusion.
- **`vector_only`** -- embeddings worked, but no graph seeds matched (or PageRank produced nothing). Hybrid search, no graph context.
- **`keyword_only`** -- no embeddings available. SQLite full-text keyword search.

This is the number to watch when you evaluate GraphRAG on your own corpus. A query returning `vector_only` did not get any graph benefit, whatever the answer looked like -- and if most of your queries come back `vector_only`, your graph isn't the problem, your *extraction* is. Likewise, a `deduplicated` count near zero means the two paths found completely disjoint evidence, which is usually a sign the query was single-hop and the graph half was along for the ride.

In plain English: don't take our word for whether the graph fired -- the response tells you.

<!-- screenshot: chat answer with the retrieval stats panel expanded, showing mode full_graphrag and the seed/provenance/vector counts -->

## Where the Graph Doesn't Help

Being straight about this is more useful than a win column.

**Single-hop fact lookup.** "What damping factor does PageRank use?" needs one chunk. The graph adds latency and nothing else. This is the GraphRAG-Bench finding in miniature, and it describes a lot of real queries.

**Small or unstructured corpora.** Three papers is enough to demonstrate a citation chain; a single document with no cross-references gives PageRank nothing to walk. Below a few interlinked documents, expect `vector_only` most of the time.

**Extraction quality is the ceiling.** The graph is only as good as the entities and relationships pulled out of your text during import. Miss the `Reflexion --builds_on--> ReAct` edge at extraction time and no amount of clever retrieval invents it. When GraphRAG disappoints, the fix is usually upstream in extraction, not in retrieval.

**Very large graphs skip PageRank entirely.** There's a `max_graph_nodes` ceiling (default `50,000`) on how much graph gets loaded for a walk. Past it, the pipeline skips PPR rather than stalling on a query.

**The seed threshold is a real gate.** Nothing clears `0.3` cosine similarity, nothing seeds, and you're in `vector_only` no matter how rich the graph is. Vocabulary mismatch between your question and your extracted entities shows up here first.

In plain English: the graph is a specialist, and a system that pretends otherwise is selling something.

## Run It Yourself

Both halves are separately observable in a running instance, which is the point:

- **The naive path alone:** the search bar exposes `keyword`, `semantic`, and `hybrid` modes directly. `semantic` is textbook vector RAG -- use it as your baseline.
- **The full path:** ask the same question in **Chat**, which retrieves through GraphRAG, and compare what gets cited. Every citation clicks through to the exact source chunk, so you can check whether the middle of the chain actually showed up.
- **Programmatically:** the `graphrag_search` MCP tool runs the whole pipeline and returns `graph_context`, `chunks`, and `retrieval_stats` as structured data -- the cleanest way to diff retrieval paths across a question set.

The three papers above make a good starter corpus precisely because you already know the right answer, which makes a wrong one easy to spot.

If you want the conceptual version of why multi-hop breaks vector search, that's [Why Your RAG Chat is Missing Half the Answers](/blog/graphrag-enhanced-search). If you don't have an instance running yet, [GraphRAG with Ollama in 10 minutes](/blog/graphrag-ollama-10-minutes) gets you from zero to a cited answer, fully local.
