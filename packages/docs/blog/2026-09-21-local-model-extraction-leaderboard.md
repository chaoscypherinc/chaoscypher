---
slug: local-model-extraction-leaderboard
title: "Which Local Model Builds the Best Knowledge Graph? A Reproducible Leaderboard"
authors: [denis]
tags: [ollama, graphrag, ai, selfhosted]
date: 2026-09-21
draft: true
description: Seven Ollama models, three corpora, one command. We scored each model's extracted knowledge graph with Chaos Cypher's evidence-checked quality formula and published the numbers, the method, and the command that reproduces them.
---

Every local-model leaderboard I have seen scores chat. None of them score the job that GraphRAG actually hands a model: read a document, name the entities, name the relationships, point at the sentence that proves each one. That is a different skill, and the models rank differently on it.

So we ran seven local models through Chaos Cypher's extraction pipeline on three small corpora, scored every resulting graph with the same evidence-gated formula the product uses, and put the table below. The command that produced it is in the post. If you have Ollama and a GPU, you can have your own copy in about an hour.

<!-- truncate -->

## What is being measured

`chaoscypher benchmark run extraction --local-only` takes each model in the built-in config, runs it as the extraction model against each dataset, commits the result to a throwaway database, and scores the graph. The score is the same one the source page shows after every extraction: a 0 to 100 grade built from per-entity quality (descriptions, confidence, aliases, cross-chunk support), per-relationship quality (justification, specificity, valid sentence references), graph topology (connectivity, with a penalty for graphs that are too dense to mean anything), coverage, and a structural penalty for hub skew and reciprocal duplicates. The [methodology page](/docs/reference/extraction-benchmark) has the full breakdown and the known limitations.

Two things make it honest. Every entity and relationship a model emits has to cite the numbered source sentences that support it, and the pipeline validates those references before anything is counted; an entity the model cannot point to is dropped. And the run is deterministic: temperature zero, a fixed seed, one shot per chunk.

The three corpora are deliberately small (about 1,300 to 1,500 words each) and deliberately different: a chapter of *War and Peace*, an encyclopedia-style technology text, and a scientific-methods primer. Small keeps the run under an hour on one GPU. Different keeps a model from winning on one genre and calling it a day.

## The leaderboard

Run on 2026-09-21 on a single RTX 5090, Ollama, benchmark v2.0, scorer v8, temperature 0, seed 42.

| Rank | Model | Quality | Speed (ms per chunk, p50) |
|-----:|-------|--------:|--------------------------:|
| 1 | Gemma 4 26B | 72.0 | 11,818 |
| 2 | Qwen3 14B | 70.0 | 10,281 |
| 3 | Gemma 4 12B | 69.0 | 3,444 |
| 4 | GLM4 9B | 65.6 | 4,231 |
| 5 | GPT-OSS 20B | 60.9 | 29,438 |
| 6 | Qwen3.6 35B-A3B (MoE) | 60.5 | 11,496 |
| 7 | Qwen3 8B | 59.4 | 9,819 |

Per corpus:

| Model | Scientific methods | Tech encyclopedia | War and Peace |
|-------|-------------------:|------------------:|--------------:|
| Gemma 4 26B | 71.4 | 74.4 | 70.1 |
| Qwen3 14B | 70.1 | 69.9 | 69.9 |
| Gemma 4 12B | 67.4 | 69.7 | 69.8 |
| GLM4 9B | 66.0 | 66.8 | 63.9 |
| GPT-OSS 20B | 73.0 | 36.1 | 73.5 |
| Qwen3.6 35B-A3B (MoE) | 36.3 | 74.5 | 70.6 |
| Qwen3 8B | 35.7 | 76.2 | 66.1 |

The benchmark also computes an "overall" figure that blends quality with speed and cost. On that blend Gemma 4 12B comes out on top, because it is three times faster than the two models above it at a three-point quality cost. Which figure matters depends on whether you are extracting a thousand documents or ten.

## What the numbers say

**Consistency is the story, not the peak.** The top three models score within five points of each other on every corpus. The models below them do not: GPT-OSS 20B posts the best scientific and literary scores in the table and then collapses to 36 on the technical text. Qwen3.6 35B-A3B and Qwen3 8B do the reverse, excellent on the encyclopedia and poor on the primer. If your corpus is one genre, the per-corpus table is the one to read. If it is mixed, and most real corpora are, the ranking column is.

**Parameter count is a weak predictor.** A 12B dense model beats a 35B mixture-of-experts model by nine points and a 20B model by eight. Extraction rewards models that follow a strict output format and keep their sentence references straight, and that is a training property, not a size property.

**Speed spreads more than quality does.** The slowest model here is nine times slower per chunk than the fastest, for a lower score. On a laptop GPU that gap is the difference between an afternoon and a week.

**None of these are cloud frontier numbers.** The `extraction` config also lists the commercial models; we ran with `--local-only` on purpose. The interesting question for a local-first tool is which model you can run yourself, not whether a hosted model would do better. It would.

## Reproduce it

```bash
pipx install chaoscypher-cli
chaoscypher benchmark run extraction --local-only
```

The command pulls nothing on its own: it uses the models Ollama already has, so pull the seven first (`ollama pull gemma4:12b`, and so on for the list in the config). `chaoscypher benchmark list` prints the config and the datasets with their provenance, and `chaoscypher benchmark run extraction --estimate` prints the call count before you commit an evening to it. Results land as a JSON file plus a rendered Markdown leaderboard in your data directory; `chaoscypher benchmark show <results.json>` re-renders one later.

To score a model that is not in the list, `chaoscypher benchmark init my-bench` scaffolds a config you can edit. To score on your own documents, drop a dataset folder with a manifest next to the built-in ones; the [methodology page](/docs/reference/extraction-benchmark) shows the layout.

Your numbers will not match ours to the decimal: Ollama versions, quantizations, and GPU kernels move the last digit. The ranking should hold, and if it does not, that is worth a GitHub issue, because it means one of us has a different model than we think.

## What this benchmark is not

It is not a retrieval or answer-quality benchmark. The `full` config adds a retrieval stage and a chat stage judged by an LLM; this post is extraction only, because extraction is the stage that decides what your graph contains. It is not a claim that a graph beats a vector index; that question has its own [post](/blog/graphrag-teardown-naive-rag) and its own honest answer. And it is a single-shot run on tiny corpora, so it tells you which models to shortlist, not which one to marry.

## Next steps

- The full methodology, including what the scorer penalizes and why, is in the [benchmark reference](/docs/reference/extraction-benchmark).
- The [ten-minute quickstart](/blog/graphrag-ollama-10-minutes) gets one of these models extracting your own documents.
- If you run the benchmark on hardware or models we did not, open a discussion with the leaderboard file. We will fold reproductions into the next round.
