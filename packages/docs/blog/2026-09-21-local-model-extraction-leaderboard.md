---
slug: local-model-extraction-leaderboard
title: "Which Local Model Builds the Best Knowledge Graph? A Reproducible Leaderboard"
authors: [denis]
tags: [ollama, graphrag, ai, selfhosted]
date: 2026-09-28
draft: true
description: Seven Ollama models, three corpora, one command. We scored each model's extracted knowledge graph with Chaos Cypher's evidence-checked quality formula and published the numbers, the flags, the method, and the command that reproduces them.
---

Every local-model leaderboard I have seen scores chat. None of them score the job that GraphRAG actually hands a model: read a document, name the entities, name the relationships, point at the sentence that proves each one. That is a different skill, and the models rank differently on it.

So we ran seven local models through Chaos Cypher's extraction pipeline on three small corpora, scored every resulting graph with the same evidence-gated formula the product uses, and put the table below. The command that produced it is in the post. If you have Ollama and a GPU, you can have your own copy in about an hour.

<!-- truncate -->

## What is being measured

`chaoscypher benchmark run` takes each model in a config, runs it as the extraction model against each dataset, commits the result to a throwaway database, and scores the graph. The score is the same one the source page shows after every extraction: a 0 to 100 grade built from per-entity quality (descriptions, confidence, aliases, cross-chunk support), per-relationship quality (justification, specificity, valid sentence references), graph topology (connectivity, with a penalty for graphs that are too dense to mean anything), coverage, and a structural penalty for hub skew and reciprocal duplicates. The [methodology page](/docs/reference/extraction-benchmark) has the full breakdown and the known limitations.

Three things make it honest. Every entity and relationship a model emits has to cite the numbered source sentences that support it, and the pipeline validates those references before anything is counted; an entity the model cannot point to is dropped. The run is deterministic: temperature zero, a fixed seed, thinking off, one shot per chunk. And the benchmark now records when a model did not actually finish a chunk, either because its answer hit the output limit or because the runaway-repetition detector cut it off, so a high score on a partial graph is flagged instead of hidden.

The three corpora are deliberately small (about 1,300 to 1,500 words each) and deliberately different: a chapter of *War and Peace*, an encyclopedia-style technology text, and a scientific-methods primer. Small keeps the run under an hour on one GPU. Different keeps a model from winning on one genre and calling it a day.

## The leaderboard

Run on 2026-09-28 on a single RTX 5090, Ollama, benchmark v2.0, scorer v8, temperature 0, seed 42, thinking off.

| Rank | Model | Quality | Speed (ms per chunk, p50) | Flags |
|-----:|-------|--------:|--------------------------:|-------|
| 1 | Qwen3 8B | 72.3 | 3,428 | one chunk cut off by the loop detector (scientific methods) |
| 2 | Qwen3.6 35B-A3B (MoE) | 70.5 | 3,268 | |
| 3 | Gemma 4 26B | 69.5 | 3,121 | |
| 4 | Gemma 4 12B | 68.3 | 4,345 | |
| 5 | Qwen3 14B | 67.9 | 6,618 | one chunk cut off by the loop detector (tech encyclopedia) |
| 6 | GLM4 9B | 63.3 | 4,786 | one chunk truncated at the output limit (scientific methods) |
| 7 | GPT-OSS 20B | 59.4 | 22,737 | six chunks truncated across the three corpora; ignores the thinking-off setting |

Per corpus:

| Model | Scientific methods | Tech encyclopedia | War and Peace |
|-------|-------------------:|------------------:|--------------:|
| Qwen3 8B | 67.1 | 74.6 | 75.2 |
| Qwen3.6 35B-A3B (MoE) | 65.5 | 76.0 | 69.9 |
| Gemma 4 26B | 68.5 | 70.5 | 69.5 |
| Gemma 4 12B | 67.6 | 68.2 | 69.1 |
| Qwen3 14B | 65.2 | 70.9 | 67.6 |
| GLM4 9B | 64.3 | 63.6 | 62.1 |
| GPT-OSS 20B | 72.4 | 73.5 | 32.3 |

The benchmark also computes an "overall" figure that blends quality with speed and cost. Qwen3 8B tops that blend too, because it is the fastest model in the table as well as the highest scorer. Which figure matters depends on whether you are extracting a thousand documents or ten.

## What the numbers say

**The top four are close, and small.** Four models land within four points of each other, and the two dense Gemma models are the only ones with no flag on any corpus. Qwen3 8B, the smallest model in the table, sits at the top with one flagged chunk. If you want the safest pick, Gemma 4 26B and Gemma 4 12B finished every chunk of every corpus cleanly; if you want the highest grade at the lowest cost, the 8B Qwen is hard to argue with.

**Read the flags before the rank.** GPT-OSS 20B posts the best scientific-methods score in the table and a strong encyclopedia score, then collapses to 32 on the novel, where three of its chunks hit the output limit. It also ignores the setting that turns its thinking off, so its answers spend the output budget on reasoning before they get to the entities. Its rank is real for this configuration, but its numbers are not comparable to models that finished. GLM4 9B lost one chunk the same way. The two Qwen models each had one chunk cut short by the loop detector, which is what the pipeline does when a model starts repeating itself; the graph is what it produced up to that point.

**The grade scores what a model emits, not how much.** Qwen3 8B's top score on the encyclopedia text comes from a five-entity, two-relationship graph, while GLM4 9B pulled fifty entities out of the science primer and scored lower. A clean small graph beats a noisy large one on this formula, on purpose, because the product would rather show you twenty entities it can prove than fifty it cannot. If your use case needs coverage, read the entity and relationship counts in the results file next to the grade.

**Parameter count is a weak predictor.** An 8B dense model tops a 35B mixture-of-experts model and a 26B dense one, and a 12B model outscores its 14B neighbour. Extraction rewards models that follow a strict output format and keep their sentence references straight, and that is a training property, not a size property.

**Speed spreads more than quality does.** The slowest model here is seven times slower per chunk than the fastest, for the lowest score. On a laptop GPU that gap is the difference between an afternoon and a week.

**None of these are cloud frontier numbers.** The built-in `extraction` config also lists the commercial models; we ran local models on purpose. The interesting question for a local-first tool is which model you can run yourself, not whether a hosted model would do better. It would.

## Reproduce it

```bash
pipx install chaoscypher-cli
chaoscypher benchmark init leaderboard
# keep the seven ollama entries below in the generated config, then:
chaoscypher benchmark run leaderboard --local-only
```

The seven models, by Ollama name: `qwen3:8b`, `qwen3.6:35b-a3b`, `gemma4:26b`, `gemma4:12b`, `qwen3:14b`, `glm4:9b`, `gpt-oss:20b`. The command pulls nothing on its own: it uses the models Ollama already has, so pull them first (`ollama pull qwen3:8b`, and so on). The built-in `extraction` config lists more than twenty local models if you would rather run the whole field; `chaoscypher benchmark list` prints the configs and datasets with their provenance, and `--estimate` prints the call count before you commit an evening to it. Results land as a JSON file plus a rendered Markdown leaderboard in your data directory; `chaoscypher benchmark show <results.json>` re-renders one later.

To score on your own documents, drop a dataset folder with a manifest next to the built-in ones; the [methodology page](/docs/reference/extraction-benchmark) shows the layout.

Your numbers will not match ours to the decimal: Ollama versions, quantizations, and GPU kernels move the last digit. The ranking of the clean-finish models should hold, and if it does not, that is worth a GitHub issue, because it means one of us has a different model than we think.

## What this benchmark is not

It is not a retrieval or answer-quality benchmark. The `full` config adds a retrieval stage and a chat stage judged by an LLM; this post is extraction only, because extraction is the stage that decides what your graph contains. It is not a claim that a graph beats a vector index; that question has its own [post](/blog/graphrag-teardown-naive-rag) and its own honest answer. And it is a single-shot run on tiny corpora, so it tells you which models to shortlist, not which one to marry.

## Next steps

- The full methodology, including what the scorer penalizes and why, is in the [benchmark reference](/docs/reference/extraction-benchmark).
- The [ten-minute quickstart](/blog/graphrag-ollama-10-minutes) gets one of these models extracting your own documents.
- If you run the benchmark on hardware or models we did not, open a discussion with the leaderboard file. We will fold reproductions into the next round.
