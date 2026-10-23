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
| DeepSeek-R1 14B (local) | ollama | yes | - | free (local) | Thinking-family distill; tests whether think=off holds outside Qwen/Gemma. |
| Gemma 4 12B (local) | ollama | yes | - | free (local) | Google Gemma 4 12B; strong instruction-following at 7.6 GB. |
| Gemma 4 26B (local) | ollama | yes | - | free (local) | Google Gemma 4 26B; best quality in the Gemma family that fits ≤24 GB. |
| Gemma 4 31B (local) | ollama | yes | - | free (local) | Google Gemma 4 31B; highest-scoring open-weight model that still fits a single 32 GB GPU. |
| Gemma 4 E4B (local) | ollama | yes | - | free (local) | Google Gemma 4 E4B; the small Gemma 4 rung, to see whether the family's chat behaviour holds at 4B effective parameters. |
| GLM 4.7 Flash (local) | ollama | yes | - | free (local) | Z.ai GLM 4.7 Flash; the GLM line leads open-weight rankings and our only other GLM is the 2024-era 9B. |
| GLM4 9B (local) | ollama | yes | - | free (local) | GLM4 9B; compact Chinese-lineage model, good entity coverage. |
| GPT-OSS 120B (workstation) | ollama | yes | - | free (local) | OpenAI OSS 120B; maximum-scale open-weight extractor for workstation benchmarking. |
| GPT-OSS 20B (local) | ollama | yes | - | free (local) | OpenAI OSS 20B; mid-tier open-weight baseline at 13 GB. |
| Granite 4.2 30B (local) | ollama | yes | - | free (local) | IBM Granite 4.2 30B; enterprise line built around structured output, which is the extraction job. |
| Granite 4.2 8B (local) | ollama | yes | - | free (local) | IBM Granite 4.2 8B; the small rung of the enterprise structured-output line. |
| Llama 3.1 70B (workstation) | ollama | yes | - | free (local) | Meta Llama 3.1 70B; large-iron open-weight frontier baseline. |
| Llama 3.1 8B (local) | ollama | yes | - | free (local) | The baseline everyone knows; anchors the small tier. |
| Mistral Small 3.2 24B (local) | ollama | yes | - | free (local) | Strongest non-Qwen mid-size instruct family under the 32 GB bar. |
| Muse Glimmer 30B (local) | ollama | yes | - | free (local) | Meta's locally deployable model for tool use and long tasks; 27.9B params, 131K context. |
| Nemotron 3.5 Lightning 30B-A3B (local) | ollama | yes | - | free (local) | NVIDIA MoE tuned for always-on agents; largest of the 32 GB-class entries. |
| OLMo 3.1 32B (local) | ollama | yes | - | free (local) | AI2 OLMo 3.1 32B; fully open data and training code, the reproducibility reference point. |
| Phi-4 14B (local) | ollama | yes | - | free (local) | Microsoft Phi-4; the 16/20 GB preset default, never measured before 2026-09-24. |
| Qwen2.5 14B Instruct (local) | ollama | yes | - | free (local) | Previous-generation Qwen instruct; was in the interface Recommended list unmeasured. |
| Qwen3 Embedding 4B (local) | ollama | yes | - | free (local) | Qwen3 embedding 4B; lightweight embedder for memory-constrained setups. |
| Qwen3 Embedding 8B (local) | ollama | yes | - | free (local) | Qwen3 embedding 8B; high-quality dense retrieval at 4.7 GB. |
| Qwen3.5 27B (local) | ollama | yes | - | free (local) | Qwen3.5 27B dense; oldest rung of the 27B Qwen ladder that isolates generation from scale. |
| Qwen3.5 9B (local) | ollama | yes | - | free (local) | Qwen3.5 9B; the newest Qwen under 10B, successor to Qwen3 8B for the 16/20 GB chat slot. |
| Qwen3.6 27B (local) | ollama | yes | - | free (local) | Qwen3.6 27B dense; middle rung of the 27B ladder and the dense counterpart to the 35B-A3B MoE. |
| Qwen3.6 35B-A3B MoE (local) | ollama | yes | - | free (local) | Qwen3.6 35B MoE; near-frontier quality at 23 GB via sparse activation. |
| Qwen3.8 27B (local) | ollama | yes | - | free (local) | Qwen3.8 27B dense; highest-scoring open-weight model that fits a single 32 GB GPU. |
| Qwen3 14B (local) | ollama | yes | - | free (local) | Qwen3 14B; quality step-up from 8B while staying ≤10 GB. |
| Qwen3 30B Instruct (local) | ollama | yes | - | free (local) | Qwen3 30B instruct tune; a distinct checkpoint from the 30b-a3b base and the gen-3 rung of the 27-30B ladder. |
| Qwen3 8B (local) | ollama | yes | - | free (local) | Qwen3 8B; efficient general-purpose local extractor at 5.2 GB. |
| Claude Haiku 4.5 | anthropic | no | 200,000 | $1.00 / $5.00 | Fastest, cheapest Anthropic tier. |
| Claude Opus 4.8 | anthropic | no | 1,000,000 | $5.00 / $25.00 | Frontier reasoning baseline; most capable Anthropic model. |
| Claude Sonnet 4.6 | anthropic | no | 1,000,000 | $3.00 / $15.00 | Best speed/intelligence balance from Anthropic. |
| Gemini 2.5 Flash | gemini | no | 1,000,000 | $0.15 / $0.60 | Cheap, fast Google tier to complement Haiku/GPT-4o-Mini in the small-model slot. |
| Gemini 2.5 Pro | gemini | no | 1,000,000 | $1.25 / $10.00 | Google frontier baseline; 1M context matches Anthropic/OpenAI frontier tier. |
| GPT-4o | openai | no | 128,000 | $2.50 / $10.00 | OpenAI frontier baseline. |
| GPT-4o Mini | openai | no | 128,000 | $0.15 / $0.60 | Cheap OpenAI tier for high-volume extraction. |
