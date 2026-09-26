---
slug: claude-builds-its-own-knowledge-graph
title: "Claude Built Its Own Knowledge Graph. No Local LLM Was Involved."
authors: [denis]
tags: [graphrag, ai, selfhosted, opensource]
date: 2026-09-21
draft: false
description: Chaos Cypher's MCP server lets the AI assistant do the entity extraction itself — Claude Code reads the chunks, submits the entities, commits the graph, and then answers from it. On your disk, across sessions, with citations.
---

Every GraphRAG tool I know of has the same shape: the server owns a language model, and the server calls it to turn documents into a graph. That is fine when you have a GPU and a model you like. It is a problem when the best model you have access to is the one already sitting in your editor, and it has no idea your knowledge base exists.

Chaos Cypher's MCP server flips the direction. The assistant on the other end of the connection does the extraction. Claude Code reads the chunks, decides what the entities and relationships are, submits them back, and commits the graph. The server never calls a model. When it is done, the same assistant can ask the graph questions it could not have answered from its context window.

<!-- truncate -->

This post walks through that flow end to end with Claude Code. It works the same way from Claude Desktop, Cursor, or any MCP client whose model can follow a short set of instructions.

## What You'll Build

- Chaos Cypher's MCP server connected to Claude Code, in write mode
- A knowledge graph built from a folder of your own markdown, extracted by Claude itself
- A persistent graph on your disk that survives closing the session
- A multi-hop question answered from the graph, with citations back to the sentences that support it

The only model involved is the one Claude Code is already using. Chaos Cypher computes the chunk embeddings locally with sentence-transformers on CPU, and nothing else runs a model.

## Step 1: Install the CLI and connect it to Claude Code

The MCP server ships in the standalone CLI:

```bash
pipx install chaoscypher-cli
```

The CLI needs Python 3.14 or newer. Then register the server with Claude Code in **write** mode, because building a graph means adding documents:

```bash
claude mcp add chaoscypher -- chaoscypher mcp --mode write
```

That is the whole setup. There is no `chaoscypher setup` step here on purpose: the wizard configures a chat and extraction model for the server, and this flow does not use one.

<!-- optional screenshot: `claude mcp list` showing the chaoscypher server connected -->

## Step 2: Ask Claude to build the graph

Open Claude Code in a project that has some prose in it — design notes, ADRs, meeting notes, a docs folder. Then ask:

> Add every markdown file under `docs/` to the Chaos Cypher knowledge graph, extract the entities and relationships yourself, and tell me the quality grade when you're done.

Here is what happens next, tool by tool. You can watch it in Claude Code's tool log.

**`add_document`** takes a file path (or a URL), chunks the text, and embeds the chunks locally. By default it only indexes; it does not ask the server to extract anything. The tool description tells the assistant exactly what to do next, which is why a one-line prompt is enough.

**`confirm_extraction`** clears the domain gate. Chaos Cypher auto-detects an extraction domain for each source — technical, scientific, legal, and so on — and parks the source until someone confirms it. Claude confirms it, or overrides it if you told it to.

**`get_extraction_tasks`** returns the plan: how many chunk groups there are and the extraction instructions, including the output format Claude is expected to produce.

**`get_extraction_chunks`** fetches the text of a chunk group with **numbered sentences**. This is the part that matters for trust. Every entity and relationship Claude submits has to point at the sentence numbers that support it.

**`submit_chunk_extraction`** is where Claude does the work. The format is a compact line protocol rather than JSON, one entity per line with its properties following, then one relationship per line:

```text
E|Alice Smith|Person|Alice; A. Smith|0.95|S1|Lead engineer on the ingest service
P|0|role|Lead Engineer
E|Ingest Service|Component|ingest-svc|0.9|S1-S2|Handles uploads and chunking
R|0|1|works_on|0.9|S1|Alice Smith leads the ingest service
```

The `S1` and `S1-S2` fields are sentence references. On the server, an evidence validator checks each one against the numbered text. An entity that does not appear in the sentence it cites is dropped, and the drop is counted so you can see it later.

**`finalize_extraction`** deduplicates entities across chunks, matches them to templates, writes the citations, and commits the graph. It returns a quality grade on a 0 to 100 scale with the breakdown behind it.

That is the whole loop. For a folder of thirty markdown files it takes Claude a few minutes, and the tool log shows every submission.

<!-- optional screenshot: Claude Code tool log showing the add_document → get_extraction_chunks → submit_chunk_extraction → finalize_extraction sequence -->

**In plain English:** Claude reads your documents in numbered chunks, writes down what it found and which sentences prove it, and Chaos Cypher checks the receipts before committing anything.

## Step 3: Look at what it built

The graph is an ordinary Chaos Cypher database in your data directory. Start the local server and open it:

```bash
chaoscypher serve
```

Every entity is typed, every relationship has a justification, and every one of them links back to the source chunk and the sentence Claude cited. The source page shows the pipeline for each file: loaded, chunked, extracted, indexed, with the quality breakdown and a count of anything the evidence check rejected.

![Source overview showing the pipeline stages, extraction counts, and entity distribution for one document](/img/screenshots/app-source-overview.png)

![Knowledge graph canvas with typed, color-coded entities clustered around their source documents](/img/screenshots/app-graph-default.png)

If a relationship looks wrong, you can edit or delete it here. The graph is yours to correct, not a black box you have to trust.

## Step 4: Close the session. Open a new one. Ask a hard question.

This is the part that makes the whole thing worth doing. Quit Claude Code and start it again. The context window is empty. The graph is not.

> Using the Chaos Cypher graph, which components depend on the queue client, and which ADR governs changing it?

Claude calls **`graphrag_search`**. That tool embeds the question, finds the entities closest to it, runs Personalized PageRank over the graph from those seeds, and pulls two kinds of evidence: the chunks that the top-ranked entities were *cited from*, and the chunks a plain vector search would have returned. The two lists are fused with Reciprocal Rank Fusion and handed back with the graph context, so the answer names the components, the ADR, and the chunks each fact came from.

A question like that needs two hops: from the queue client to the things that depend on it, and from there to the decision record that governs them. Vector search over chunks tends to return the three chunks that mention "queue client" and stop. The graph walks the edges.

Chaos Cypher also degrades honestly. If the embedding model is unavailable the tool falls back to keyword search and says so in the response's retrieval mode, rather than returning nothing.

<!-- optional screenshot: Claude Code answering the two-hop question with the graphrag_search tool result expanded, showing cited chunk aliases -->

## Why this is different

Three things make client-driven extraction more than a party trick.

**The server needs no model.** The assistant you already pay for, or the local one you already run in your editor, does the extraction. A laptop with no GPU can build a graph with Claude doing the reading.

**The evidence check runs on the server, not in the prompt.** Whatever the assistant submits is validated against the numbered sentences before it is committed. The graph records what was rejected and why. You do not have to trust the model's confidence number; you can look at the sentence.

**The graph outlives the session.** It sits in a SQLite database on your disk. It is inspectable in the UI, exportable as a `.ccx` package, and available to the next session, the next tool, or the next machine.

## What it does not do yet

Extraction quality is the assistant's quality. A weak model will submit weak extractions; the evidence check catches fabrications, not shallow reading. The server also rate-limits submissions per source (100 per minute by default) and caps each submission's size, so a very large corpus is best fed in batches. And `add_document` takes one file at a time; for a whole folder, the assistant loops, or you zip the folder and add the archive.

## Next steps

- The full tool list, with the client-driven extraction limits, is in the [MCP user guide](/docs/user-guide/mcp).
- Once the graph is built, [export it as a `.ccx` package](/docs/user-guide/import-export) and mount it into another assistant with one command. That is the next post.
