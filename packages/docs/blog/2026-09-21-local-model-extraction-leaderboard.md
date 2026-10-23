---
slug: local-model-extraction-leaderboard
title: "Which Local Model Should Build Your Knowledge Graph? A Reproducible Leaderboard"
authors: [denis]
tags: [ollama, graphrag, ai, selfhosted]
date: 2026-10-21
draft: true
description: 22 Ollama models, scored on the two jobs a knowledge graph hands a model — following the extraction prompt and answering from the graph — with pass/fail checks, an interval on every score, and the commands that produce the board.
---

Most local-model leaderboards score chat. A knowledge graph hands a model a different job first: read a passage, name the entities and relationships in a strict format, and point at the sentence that proves each one. Then, later, answer a question from what was extracted without inventing anything. Models rank differently on those two jobs, and a model that chats well can still build a messy graph.

So the [leaderboard](/leaderboard) scores both, for 22 local models pulled from Ollama, with checks you can read and a command that produces the board.

<!-- truncate -->

## What is being measured

The board has two columns of work and one blend.

**Extraction: does the model do what the extraction prompt says?** Each probe is a short hand-written passage plus one instruction under test, run through the real extraction prompts and parser. Pure-function checks decide pass or fail; there is no judge model. The instructions cover the output format, evidence discipline (cite the right sentence, use no outside knowledge), entity discipline (names, not roles) and relationship discipline. There are 65 of them in isolation, and then 39 spliced into real extraction-sized passages of *War and Peace* ("in chunks"), because a model that follows an instruction in a tidy passage does not always follow it in real text.

**Chat: does the model answer from the graph?** Every model answers the same questions about Book One of *War and Peace*, from the same reference graph and the same retrieved context, so the only thing that changes between rows is the chat model. An answer passes only if it names what was asked, names nothing the context does not hold, and declines when the book does not answer the question.

**Overall** blends the two: 0.6 of extraction plus 0.4 of chat, with in-chunks extraction counted twice and isolated once, since in chunks is the number that carries over to your documents. Extraction runs with thinking off, chat with thinking on, both at temperature zero with a fixed seed.

In plain English: one column asks "can this model build a clean graph?", the other asks "can it answer from one without making things up?", and Overall weighs the first a bit more.

## How to read the board

Three things on the page matter more than the rank.

**Every score carries a 90% interval**, shown as ± beside it. It comes from resampling the row's tasks 2,000 times, and it says how far the score could move if the suite had drawn other tasks of the same kind. Sorted by Overall, a ≈ marks a row the board cannot tell apart from the one above it, and plenty of neighbouring rows are ties.

**Flags beat ranks.** A row is flagged when answers were cut off at the output limit, when the runaway-repetition detector stopped the model, or when it thought anyway after being told not to. A flagged row is scored on what it actually produced, so its number is real, but the flag tells you why it is low.

**"No tools" means no chat in the app.** The app's chat loop always passes tools to the model, so a model whose Ollama manifest lacks tool calling can score here but cannot be your chat model. The board marks it.

In plain English: read the ± and the flags first; two rows whose ranges overlap are a coin toss, and a flag explains a low score before you blame the model.

## What the board says

**Extraction is where models differ.** The chat column is bunched; the extraction column spreads wide. If you are choosing a model to build the graph, that is the column to sort by.

**Size is a weak predictor.** Gemma 4 12B sits among models that need far more GPU memory, and several 30B-class models sit near the bottom because their in-chunks extraction falls apart. Following a strict output format and keeping sentence references straight is a training property more than a size property.

**Read the flags before the rank.** GPT-OSS 20B and DeepSeek-R1 14B both thought despite the thinking-off setting, so their answers spend the output budget on reasoning before they reach the entities, and many of GPT-OSS 20B's are cut off. Llama 3.1 8B and GLM 4.7 Flash hit the output limit on many probes. Their rows are honest about what they produced, and not comparable to rows that finished cleanly.

**The model at the top also builds the chat column's graph.** Gemma 4 31B leads by Overall, with no flag on either extraction suite, and it is the model that built the reference graph every chat row answers from.

**A few Claude rows appear for reference.** They ran through Claude Code against the same probes and checks, on extraction only, with the client's own settings instead of our pins and part of the in-chunks suite. The page marks them, and they are not a like-for-like comparison with the pinned local rows.

In plain English: pick the extraction model by the extraction column and its flags, not by parameter count, and treat the top handful as a cluster rather than a podium.

## Where the graph matters

The same page carries a second section: the same questions answered three ways by every model, with plain vector RAG (LlamaIndex's standard tutorial pipeline), hybrid keyword + vector search, and graph + vector retrieval. That section states a headline only when it holds across the models measured. It has its [own write-up on the methods page](/docs/reference/extraction-benchmark#where-the-graph-matters-the-three-rung-retrieval-comparison), with the bars each headline has to clear.

## Reproduce it

```bash
pipx install chaoscypher-cli
chaoscypher setup        # once: pick Ollama as the provider
chaoscypher benchmark run probes --local-only
chaoscypher benchmark run chat-probes --local-only
```

`benchmark` needs a configured provider, so a fresh install asks you to run `chaoscypher setup` first; that is the second line. `probes` produces the extraction columns and `chat-probes` the chat column; `--local-only` runs the local models in each config and skips the hosted ones. Pull the models you want first (`ollama pull gemma4:12b`, and so on). `--estimate` prints the call count before you commit an evening to it, `--model-timeout` caps a hung model and moves on, and `chaoscypher benchmark list` prints every config and dataset.

Results land as a JSON file plus a rendered Markdown table in your data directory; `chaoscypher benchmark show <results.json>` re-renders one later. To score your own documents, add a dataset folder next to the built-in ones; the [methodology page](/docs/reference/extraction-benchmark) shows the layout.

Your numbers will not match ours to the decimal: Ollama versions, quantizations and the release you install move them. The clusters should hold. If they do not, that is worth a GitHub issue, because it means one of us has a different model than we think.

## What this benchmark is not

It is not a model-intrinsic benchmark: it scores each model together with ChaosCypher's prompts and pipeline, which is the pragmatic point, and those live in the repository as part of the method. It is local models on one novel for the chat column. And it tells you which models to shortlist, not which one to marry.

## Next steps

- The full methodology, including every check and why, is in the [benchmark reference](/docs/reference/extraction-benchmark).
- The [ten-minute quickstart](/blog/graphrag-ollama-10-minutes) gets one of these models extracting your own documents.
- If you run the board on models we did not, open a discussion with your results file. We fold reproductions into the next round.
