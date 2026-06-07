---
title: Setup Wizard
description: Run chaoscypher setup to configure your LLM provider — interactive wizard covering provider selection, model config, API key entry, embedding setup, and connection testing.
---

# Setup

The `setup` command runs an interactive wizard that configures your LLM provider for entity extraction and AI-powered features. It handles provider selection, model configuration, API key collection, and connection testing.

```bash
chaoscypher setup --help
```

## Quick Start

Run the wizard with no arguments to step through the full configuration interactively:

```bash
chaoscypher setup
```

**Sample wizard flow:**

```
╭──────────── Chaos Cypher Setup Wizard ────────────╮
│ Configure LLM for entity extraction               │
╰───────────────────────────────────────────────────╯

Choose LLM Provider:

 [1]  Ollama        Local LLM - Free, private, no API key required
 [2]  OpenAI        GPT-4o - Cloud-based, requires API key
 [3]  Anthropic     Claude - Cloud-based, requires API key
 [4]  Google Gemini Gemini Pro - Cloud-based, requires API key

Select provider [1]:

How much GPU VRAM do you have?

 [1]  16GB  (RTX 4080, 5080)    → phi4:14b
 [2]  20GB  (RTX 5080 Super)    → phi4:14b
 [3]  24GB  (RTX 4090, 3090)    → qwen3:30b
 [4]  32GB  (RTX 4090, 3090)    → qwen3:30b
 [5]  48GB  (A6000, 2x 4090)    → qwen3:30b
 [6]  96GB  (H100)              → gpt-oss:120b
 [7]  128GB (Multi-H100)        → gpt-oss:120b
 [8]  Custom                    I'll specify models manually

Select VRAM tier [3]:

Applying 24GB VRAM preset...
  Chat model: qwen3:30b
  Extraction model: qwen3:30b-instruct
  Context window: 32768

Configure embedding provider? [y/N]:

  Embedding auto-configured: ollama / qwen3-embedding:0.6b

╭─────────────── Configuration Complete ──────────────────╮
│  Provider          ollama                               │
│  URL               http://localhost:11434               │
│  Chat Model        qwen3:30b                            │
│  Extraction Model  qwen3:30b-instruct                   │
│  Context Window    32768                                │
│                                                         │
│  Embedding Provider  ollama                             │
│  Embedding Model     qwen3-embedding:0.6b               │
│  Config File         ~/.local/share/chaoscypher/settings.yaml │
╰─────────────────────────────────────────────────────────╯

Next steps:
  chaoscypher source add document.pdf  # Process a document
  chaoscypher chat                     # Start interactive chat
```

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--provider {ollama,openai,anthropic,gemini}` | `-p` | Pre-select a provider (skip the selection prompt) |
| `--vram INT` | | VRAM size in GB for Ollama (applies matching VRAM preset) |
| `--non-interactive` | | CI/script mode — reads configuration from environment variables |
| `--test/--no-test` | | Test provider connectivity after configuration (default: `--test`) |
| `--force` | `-f` | Reconfigure even if already configured |

## Providers

| Provider | API Key Required | Environment Variable | What Gets Configured |
|----------|-----------------|---------------------|---------------------|
| `ollama` | No | — | Ollama URL, chat model, extraction model, vision model, context window |
| `openai` | Yes | `OPENAI_API_KEY` | API key, chat model, extraction model, vision model |
| `anthropic` | Yes | `ANTHROPIC_API_KEY` | API key, chat model, extraction model, vision model |
| `gemini` | Yes | `GEMINI_API_KEY` | API key, chat model, extraction model, vision model |

The wizard configures separate models for chat (interactive conversation) and extraction (entity and relationship extraction from documents). Vision models are optional and enable image understanding.

## VRAM Presets (Ollama)

When using Ollama, select a VRAM tier to automatically apply the optimal model configuration for your hardware:

| VRAM | Example GPUs | Recommended Model |
|------|-------------|-------------------|
| 16 GB | RTX 4080, 5080 | phi4:14b |
| 20 GB | RTX 5080 Super | phi4:14b |
| 24 GB | RTX 4090, 3090 | qwen3:30b |
| 32 GB | RTX 4090, 3090 | qwen3:30b |
| 48 GB | A6000, 2x 4090 | qwen3:30b |
| 96 GB | H100 | gpt-oss:120b |
| 128 GB | Multi-H100 | gpt-oss:120b |

Choose **Custom** to specify models manually if your hardware is not listed or you prefer different models.

## Skip Provider Selection

Pass `--provider` to skip straight to the provider-specific configuration:

```bash
# Jump directly to Ollama setup
chaoscypher setup --provider ollama

# Jump directly to Ollama setup with a VRAM preset
chaoscypher setup --provider ollama --vram 24

# Jump directly to OpenAI setup
chaoscypher setup --provider openai
```

## Non-Interactive CI Mode

Use `--non-interactive` for scripts, Docker entrypoints, or CI pipelines. In this mode the wizard detects the provider from environment variables automatically:

```bash
# Provider auto-detected from env vars (openai takes priority over anthropic)
export OPENAI_API_KEY="sk-..."
chaoscypher setup --non-interactive

# Explicit provider + VRAM preset in one command
chaoscypher setup --non-interactive --provider ollama --vram 32

# Skip connectivity test (useful in CI where Ollama may not be running)
chaoscypher setup --non-interactive --provider ollama --no-test
```

**Auto-detection priority:** `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → `GEMINI_API_KEY` → `ollama`

In non-interactive mode, API keys are read directly from environment variables — they are not saved to the config file.

## Reconfiguring

If you have already run setup, the wizard will ask before overwriting the existing configuration. Use `--force` to bypass this prompt:

```bash
chaoscypher setup --force
```

## Connection Testing

By default, the wizard tests connectivity before saving:

- **Ollama** — makes a `GET /api/tags` request to the configured URL
- **OpenAI** — validates the API key against `GET /v1/models`
- **Anthropic** — sends a minimal test message to verify the key
- **Gemini** — validates the API key against the models list endpoint

Pass `--no-test` to skip testing (for example, when setting up ahead of installing a model):

```bash
chaoscypher setup --provider ollama --no-test
```

If the connection test fails, you are prompted whether to continue anyway.

## Embedding Provider

After LLM configuration, the wizard optionally configures the embedding provider used for semantic search and RAG. If you are using Ollama and skip this step, the embedding provider is automatically set to Ollama with the default embedding model.

Available embedding providers: **Local CPU** (sentence-transformers), **Ollama** (GPU-accelerated), **OpenAI**, **Google Gemini**.
