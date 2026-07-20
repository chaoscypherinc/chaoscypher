---
slug: graphrag-ollama-10-minutes
title: "GraphRAG with Ollama in 10 Minutes: The Local-First Quickstart"
authors: [denis]
tags: [selfhosted, graphrag, ollama, python]
date: 2026-07-14
draft: true
description: Install Chaos Cypher and Ollama, upload a PDF, and ask a cited question against your own knowledge graph — no API key, nothing leaves your machine.
---

You don't need a cloud API key to try GraphRAG. Ten minutes, one document, and a laptop with a decent GPU (or none at all, for the first few steps) is enough to see a full knowledge graph form and answer a question with a citation you can click through to the source page.

<!-- truncate -->

This is the fast version. If you want the reasoning behind running everything locally -- privacy, VRAM presets, multi-GPU load balancing -- that's in [Build a Private AI Knowledge Graph That Never Leaves Your Machine](/blog/local-ai-knowledge-graph). This post is the "just show me" version: install, upload, watch the graph build, ask a question, done.

## What You'll Build

By the end of this walkthrough you'll have:

- Chaos Cypher running locally via Docker
- A local LLM (Ollama) doing the entity extraction and chat
- One document imported, chunked, and embedded
- A knowledge graph of entities and relationships extracted from that document
- A chat answer with a citation that traces back to the exact chunk it came from

Nothing leaves your machine. No OpenAI key, no Anthropic key, no usage bill.

## Step 1: Install Ollama and Start the Model Download

Grab Ollama from [ollama.com](https://ollama.com) for your platform, then pull the default chat/extraction model in a terminal:

```bash
ollama pull qwen3:30b-instruct
```

This is the long pole of the whole setup -- roughly 18-20 GB. Kick it off now and let it run in the background while you do the next steps; indexing and search don't need it, only extraction and chat do.

If you're on a smaller GPU or just want to see the pipeline move faster for this first run, point `ollama_chat_model` at a smaller `qwen3` tag instead -- you can always switch to the full model afterward from Settings.

## Step 2: Start Chaos Cypher

```bash
docker run -d --name chaoscypher \
  -p 80:80 \
  -p 443:443 \
  -v chaoscypher-data:/data \
  ghcr.io/chaoscypherinc/chaoscypher:latest
```

(Cloned the repo instead? `make docker-up` builds and starts the same container.)

On Linux Docker Engine (not Docker Desktop), add `--add-host=host.docker.internal:host-gateway` so the container can reach Ollama on your host -- Docker Desktop resolves this automatically.

<!-- screenshot: docker-up terminal output showing the container starting and becoming healthy -->

Open [http://localhost](http://localhost). A startup page shows each service (Nginx, Cortex, Valkey, Neuron) coming online -- usually 30-60 seconds. Set a username and password on first run, and you land on the Dashboard.

## Step 3: Upload a Document

Go to **Sources** in the sidebar and drag in a PDF, DOCX, or text file -- a 10-20 page document is a good first run so you can watch the whole pipeline complete in a couple of minutes.

Indexing starts immediately (chunking + embedding for search) and finishes in about 30 seconds per 100 pages -- this step downloads a small embedding model (~600 MB) the first time, but doesn't need Ollama at all. Once indexing finishes, a **Review** dialog proposes an extraction domain (technical, medical, legal, and so on) detected from the content. Click **Confirm** to queue entity extraction.

If the Ollama model pull from Step 1 is still running, the source will sit at **extracting** until it finishes -- indexing and search still work while you wait.

## Step 4: Watch the Graph Form

Once extraction commits, open **Graph** in the sidebar. You'll see nodes for the people, organizations, concepts, and events the model found in your document, connected by edges representing the relationships it inferred between them. For a 100-page document on a 30B-class model, expect roughly 5-10 minutes of extraction time.

<!-- screenshot: graph view showing extracted entities and relationships after a first extraction run -->

Click any node to see its properties, its connections, and the source text it was extracted from -- that link back to source evidence is the same mechanism the chat citations use in the next step.

## Step 5: Ask a Cited Question

Open **Chat**, start a new conversation, and ask something specific about the document you just uploaded. Chaos Cypher retrieves the relevant chunks and graph context, hands them to your local model, and streams back an answer with citations attached to the sentences that came from your source.

<!-- screenshot: chat panel showing a streamed answer with a highlighted citation -->

Click a citation and it jumps straight to the exact chunk in the source document the sentence was grounded in -- not a link to "the document," the specific paragraph. That's the difference between an AI answer you have to take on faith and one you can verify in a click.

## Where to Go From Here

That's the whole loop: install, upload, extract, ask, verify. A few directions once you're comfortable with it:

- **Bigger documents, bigger graphs.** Batch-upload a folder of related documents and the graph starts connecting entities across sources, not just within one.
- **Tune for your hardware.** The VRAM preset table and multi-instance GPU setup in the [privacy-first deep dive](/blog/local-ai-knowledge-graph) cover getting the most out of local hardware.
- **Mix local and cloud.** Keep chat and search local for privacy, and switch just the extraction pass to a cloud provider if you want maximum quality on a hard document -- it's a single settings change, same pipeline either way.

In plain English: you can run a real knowledge graph, end to end, on your own machine, in about the time it takes to read this post.
