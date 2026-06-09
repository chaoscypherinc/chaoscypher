---
id: model-cards
title: Benchmark Model Cards
description: Metadata and pricing for every model in the ChaosCypher benchmark suite.
---

# Benchmark Model Cards

> Generated from `models_registry.yaml`. Do not edit by hand —
> run `uv run python scripts/generate_model_cards.py`.

| Model | Provider | Open weight | Context | Price in/out ($/1M) | Why included |
|---|---|---|---|---|---|
| Gemma 4 12B (local) | ollama | yes | - | free (local) | Google Gemma 4 12B; strong instruction-following at 7.6 GB. |
| Gemma 4 26B (local) | ollama | yes | - | free (local) | Google Gemma 4 26B; best quality in the Gemma family that fits ≤24 GB. |
| GLM4 9B (local) | ollama | yes | - | free (local) | GLM4 9B; compact Chinese-lineage model, good entity coverage. |
| GPT-OSS 120B (workstation) | ollama | yes | - | free (local) | OpenAI OSS 120B; maximum-scale open-weight extractor for workstation benchmarking. |
| GPT-OSS 20B (local) | ollama | yes | - | free (local) | OpenAI OSS 20B; mid-tier open-weight baseline at 13 GB. |
| Llama 3.1 70B (workstation) | ollama | yes | - | free (local) | Meta Llama 3.1 70B; large-iron open-weight frontier baseline. |
| Qwen3 Embedding 4B (local) | ollama | yes | - | free (local) | Qwen3 embedding 4B; lightweight embedder for memory-constrained setups. |
| Qwen3 Embedding 8B (local) | ollama | yes | - | free (local) | Qwen3 embedding 8B; high-quality dense retrieval at 4.7 GB. |
| Qwen3.6 35B-A3B MoE (local) | ollama | yes | - | free (local) | Qwen3.6 35B MoE; near-frontier quality at 23 GB via sparse activation. |
| Qwen3 14B (local) | ollama | yes | - | free (local) | Qwen3 14B; quality step-up from 8B while staying ≤10 GB. |
| Qwen3 8B (local) | ollama | yes | - | free (local) | Qwen3 8B; efficient general-purpose local extractor at 5.2 GB. |
| Claude Haiku 4.5 | anthropic | no | 200,000 | $1.00 / $5.00 | Fastest, cheapest Anthropic tier. |
| Claude Opus 4.8 | anthropic | no | 1,000,000 | $5.00 / $25.00 | Frontier reasoning baseline; most capable Anthropic model. |
| Claude Sonnet 4.6 | anthropic | no | 1,000,000 | $3.00 / $15.00 | Best speed/intelligence balance from Anthropic. |
| Gemini 2.5 Flash | gemini | no | 1,000,000 | $0.15 / $0.60 | Cheap, fast Google tier to complement Haiku/GPT-4o-Mini in the small-model slot. |
| Gemini 2.5 Pro | gemini | no | 1,000,000 | $1.25 / $10.00 | Google frontier baseline; 1M context matches Anthropic/OpenAI frontier tier. |
| GPT-4o | openai | no | 128,000 | $2.50 / $10.00 | OpenAI frontier baseline. |
| GPT-4o Mini | openai | no | 128,000 | $0.15 / $0.60 | Cheap OpenAI tier for high-volume extraction. |
