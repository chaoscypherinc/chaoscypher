---
slug: local-ai-knowledge-graph
title: "Build a Private AI Knowledge Graph That Never Leaves Your Machine"
authors: [denis]
tags: [workflows, privacy]
date: 2026-03-12
description: Run Chaos Cypher with Ollama for a fully local AI knowledge graph — no API keys, no data leaving your network, same pipeline as cloud providers.
---

Every week, another AI tool asks you to upload your most sensitive documents to someone else's servers. Your contracts, medical records, internal research, personal journals -- all piped through APIs you don't control, stored in logs you can't audit, governed by terms of service that change without notice.

<!-- truncate -->

For a lot of use cases, that's fine. But there's a whole class of knowledge that simply cannot leave your network. Healthcare organizations bound by HIPAA. Law firms handling privileged communications. Financial institutions with regulatory obligations around client data. Companies whose competitive advantage lives in proprietary research. Or maybe you just have a journal and you'd rather not feed your inner monologue to a data center in Virginia.

The usual answer is "just don't use AI tools." That's not really an answer anymore. The productivity gap between AI-assisted knowledge work and manual knowledge work is too wide to ignore. The real question is: can you get the benefits of AI-powered knowledge graphs without the privacy tradeoffs?

Yes. Chaos Cypher paired with Ollama runs a complete AI knowledge graph pipeline -- document ingestion, entity extraction, relationship mapping, semantic search, and conversational chat -- entirely on your local machine. No API keys. No usage limits. No monthly bills. No data leaving your network. You install it, you run it, you own it.

This isn't a compromise or a toy demo. It's the same extraction pipeline, the same graph visualization, the same chat interface that works with cloud providers. You're just swapping the LLM backend from a remote API to a local one.

## From Zero to Local Knowledge Graph

Here's the full workflow, start to finish. Fifteen minutes if you're following along, five if you've done this before.

**Step 1: Install Ollama and pull a model.**

Head to [ollama.com](https://ollama.com) and install it for your platform. Then pull a model:

```bash
ollama pull qwen3:30b
```

That downloads the model weights once. After that, Ollama runs as a local API server -- same REST interface as OpenAI, but pointing at `localhost:11434`.

**Step 2: Start the Chaos Cypher stack.**

```bash
make docker-dev
```

This brings up four containers: the Cortex API server, a Neuron background worker, the web Interface, and Valkey for job queuing. Everything talks to Ollama on your host machine through Docker's `host.docker.internal` bridge. No external network calls.

**Step 3: Upload a document.**

Open `http://localhost:3000`, create a database (or use the default), and drag a PDF, DOCX, or text file into the Sources page. Chaos Cypher immediately begins indexing -- chunking the document, generating embeddings, and building a search index. This takes about 30 seconds for a 100-page PDF and requires no GPU at all (more on that below).

**Step 4: Extract entities and relationships.**

Once indexing completes, kick off entity extraction. This is where the LLM does its work -- reading through each chunk, identifying entities (people, organizations, concepts, events), discovering relationships between them, and building a structured knowledge graph. Chaos Cypher automatically detects the type of document and applies [domain-specific extraction rules](/blog/domain-extraction-guide) for higher quality results. For a 100-page document with a 30B model, expect roughly 5-10 minutes.

![Sources list showing document processing status](/img/screenshots/sources-list.png)

**Step 5: Chat with your knowledge graph.**

Once extraction finishes and the results are committed to your graph, open the Chat page and start asking questions. The chat system uses RAG (retrieval-augmented generation) to search your indexed documents and graph, then feeds the relevant context to your local LLM for a grounded answer. Everything stays on your machine -- the search, the retrieval, the generation.

### Pick Your Preset

Not everyone has the same GPU. Chaos Cypher ships with VRAM presets that auto-configure the right model, context window, and batch size for your hardware. Select a preset in Settings and it handles the rest.

| VRAM | Chat Model | Extraction Model | Context | GPU Examples |
|------|-----------|-------------------|---------|--------------|
| 16 GB | Phi4 14B | Phi4 14B | 16K | RTX 4080, RTX 5080 |
| 20 GB | Phi4 14B | Phi4 14B | 24K | RTX 5080 Super |
| 24 GB | Qwen3 30B | Qwen3 30B Instruct | 16K | RTX 4090, RTX 3090 |
| 32 GB | Qwen3 30B | Qwen3 30B Instruct | 32K | RTX 4090, RTX 3090 |
| 48 GB | Qwen3 30B | Qwen3 30B Instruct | 48K | A6000, 2x 4090 |
| 96 GB | gpt-oss 120B | gpt-oss 120B | 48K | RTX 6000 Pro |
| 128 GB | gpt-oss 120B | gpt-oss 120B | 64K | DGX Spark, AMD Ryzen AI Max+ 395 |

The sweet spot for most people is 24 GB. An RTX 4090 running Qwen3 30B gives you strong chat quality and solid extraction results. If you're on 16 GB, you'll still get a good experience for chat and search -- extraction quality will be noticeably lower on complex documents, but perfectly usable for straightforward material.

![LLM provider settings with Ollama configuration and VRAM preset](/img/screenshots/settings-llm-provider.png)

## Under the Hood

A few things are worth knowing about how the local pipeline actually works.

### Embeddings Are Always Local

Here's something that surprises people: the embedding model that powers semantic search runs on CPU. It has nothing to do with Ollama or your GPU. Chaos Cypher defaults to Qwen3-Embedding-0.6B, a compact model that downloads once and runs locally via sentence-transformers. Any HuggingFace sentence-transformers model can be used, and cloud providers (OpenAI, Ollama, Gemini) are also supported.

This means semantic search works even if Ollama is offline. It means you can index thousands of documents on a machine with no GPU at all. The embeddings are generated in the Neuron worker during indexing and stored in your local SQLite database (via sqlite-vec). Search queries generate an embedding on the fly, compare it against the index, and return results -- all on CPU, all local, typically in under a second.

Re-ranking also runs locally. Chaos Cypher uses a cross-encoder model (Alibaba-NLP/gte-reranker-modernbert-base, 149M parameters, ~600 MB) via sentence-transformers to re-rank search results by relevance before passing them to the LLM. No API calls involved. The ModernBERT-based model scores ~56.2 NDCG@10 on the BEIR benchmark -- significantly more accurate than smaller models on diverse, out-of-domain queries. Any HuggingFace cross-encoder can be swapped in via settings.

### Multi-Instance Load Balancing

Have multiple machines with GPUs? Or multiple GPUs in one workstation? You can point Chaos Cypher at all of them. Configure multiple Ollama instances in your settings, and the load balancer distributes requests across them with three strategies:

- **Round-robin** -- simple alternation, good for identical hardware
- **Least-loaded** -- sends requests to whichever instance has the fewest active jobs
- **Random** -- exactly what it sounds like

Each instance gets independent health checks. If one goes down, the load balancer automatically fails over to the healthy instances. When it comes back, it rejoins the pool. The configuration is hot-reloadable -- add or remove instances from the Settings page without restarting anything. In-flight requests drain gracefully before an instance is removed.

This is particularly useful for extraction workloads. A 500-page document produces hundreds of chunk groups to process. Spreading that across two or three GPUs cuts extraction time proportionally.

### Thinking Mode

Qwen3 models support an extended reasoning mode using `<think>` tags -- the model works through its reasoning step by step before producing a final answer. Chaos Cypher detects and handles this automatically. When thinking is enabled for chat, the model's internal reasoning is extracted and available separately from the final response. For models that don't support thinking tags, everything works normally -- no configuration needed, graceful fallback.

Thinking is currently best suited for chat interactions where you want more careful, reasoned responses. For extraction tasks, the overhead of reasoning tokens tends to slow things down without a proportional quality improvement, so Chaos Cypher disables it for extraction by default. You can toggle this per-operation type in settings.

### Performance Reality Check

Let's be honest about the tradeoffs, because nobody benefits from hype.

**Chat is great locally.** Interactive question-answering with RAG retrieval works well on 24 GB+ hardware. The model has context from your documents, it generates coherent answers, latency is acceptable for interactive use. Streaming means you see tokens as they arrive -- the experience feels responsive even when total generation takes a few seconds.

**Simple extraction works well.** Documents with clear entity boundaries -- people's names, organization names, dates, locations -- extract reliably on local models. Legal contracts with named parties and defined obligations, research papers with cited authors and institutions, meeting notes with action items and owners.

**Complex extraction is where you notice the gap.** Dense academic papers with nuanced conceptual relationships, documents where entities are implied rather than stated, multi-hop reasoning about how concepts relate to each other -- this is where cloud models with 100B+ parameters still have a meaningful advantage. A Qwen3 30B model will get you 70-80% of what Claude or GPT-4.1 would produce on hard extraction tasks. For many use cases, that's more than enough. For others, you'll want to use a cloud provider for the extraction pass and keep everything else local.

The good news: Chaos Cypher lets you mix and match. Use Ollama for chat and search (where privacy matters most, since those are interactive queries about your data), and use a cloud provider for the one-time extraction pass if you need maximum quality. Or keep everything local and accept the quality tradeoff. Your call.

### Four Providers, One Interface

Chaos Cypher supports four LLM providers through a unified interface:

- **Ollama** -- local models, no API key, no cost
- **OpenAI** -- GPT-4.1, high-quality extraction
- **Anthropic** -- Claude Sonnet 4.5, strong reasoning
- **Gemini** -- Gemini 2.5 Pro, massive context window

Switching between them is a single config change. The same entity extraction pipeline, the same chat system, the same search infrastructure. You can start with Ollama to prove the workflow works, then switch to a cloud provider for production extraction, or vice versa. You can even use different providers for different operations -- Ollama for chat, OpenAI for extraction.

## Try It Yourself

Minimal configuration in `data/settings.yaml`:

```yaml
LLM:
  chat_provider: "ollama"
  ollama_chat_model: "qwen3:30b-instruct"
  ollama_num_ctx: 32768
```

The default Ollama instance points at `http://host.docker.internal:11434`,
which Just Works™ for the all-in-one container talking to a host-side
Ollama. To override the URL or add multi-GPU instances, use
`ollama_instances`.

Or skip the YAML entirely -- open the Settings page in the UI, select Ollama as your provider, pick a VRAM preset that matches your GPU, and you're done. The preset fills in the model name, context window, batch size, and extraction model automatically.

Then start everything:

```bash
make docker-dev
```

<!-- SCREENSHOT: Terminal showing make docker-dev starting all services (Valkey, Cortex, Neuron, Interface) with healthy status. -->

Upload a document, wait for indexing (30 seconds) and extraction (a few minutes), and you have a working knowledge graph built entirely on your hardware.

![Knowledge graph visualization showing extracted entities and relationships](/img/screenshots/graph-visualization.png)

A few tips for getting the best results:

- **Pull models before starting Chaos Cypher.** Run `ollama pull qwen3:30b` (or whichever model your preset uses) before your first extraction. The Neuron worker will wait for Ollama, but pre-pulling avoids the initial download delay.
- **Monitor VRAM usage.** Run `nvidia-smi` to see how much VRAM your model is using. If you're near the limit, drop to a smaller context window or a smaller model. OOM kills during extraction are recoverable (the job retries), but they're slow.
- **Start with shorter documents.** Your first upload should be a 10-20 page document so you can see the full pipeline complete in a couple of minutes. Scale up once you're comfortable with the output quality.
- **Experiment with extraction models.** The presets pair specific extraction models with chat models. The extraction model uses an instruct-tuned variant optimized for structured output. If extraction quality isn't where you want it, try the next VRAM tier up -- the jump from 8B to 30B parameters makes a significant difference in extraction accuracy.

## What's Next

Running everything locally is the starting point, not the ceiling.

If you outgrow a single GPU, the multi-instance setup lets you spread load across multiple machines on your network -- a small GPU cluster for your team, still fully private, still no cloud dependency. Configure two or three Ollama instances on different machines, point Chaos Cypher at all of them, and extraction workloads parallelize automatically.

When you do need cloud-tier quality for specific tasks, the cloud providers are there. Chaos Cypher doesn't lock you into local-only or cloud-only. You choose per-operation, per-database, whenever you want. The architecture is the same either way -- the only thing that changes is where the LLM inference happens.

The privacy argument isn't really about paranoia. It's about control. Your knowledge graph is a map of everything you know -- your research, your relationships, your institutional memory. Keeping that map on your own hardware isn't a limitation. It's a feature.
