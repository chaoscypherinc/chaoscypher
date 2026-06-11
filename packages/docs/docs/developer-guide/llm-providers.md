---
id: llm-providers
title: LLM Providers
description: Configure Ollama, OpenAI, Anthropic, and Gemini as LLM backends in chaoscypher-core using the factory pattern with caching and automatic fallbacks.
---

# LLM Providers

Chaos Cypher supports multiple LLM providers for chat and entity extraction. The provider system uses a factory pattern with caching and automatic fallbacks.

## Available Providers

| Provider | Chat | Extraction | Module |
|----------|:----:|:----------:|--------|
| **Ollama** | yes | yes | `adapters.llm.providers.ollama_provider` |
| **OpenAI** | yes | yes | `adapters.llm.providers.openai_provider` |
| **Anthropic** | yes | yes | `adapters.llm.providers.anthropic_provider` |
| **Gemini** | yes | yes | `adapters.llm.providers.gemini_provider` |

All providers extend `BaseLLMProvider` and implement a consistent interface for chat completions and streaming.

:::note[Embeddings are handled separately]

Vector embeddings are produced by a dedicated **embedding provider** using sentence-transformers on the local CPU by default. The chat-side LLM provider does not generate embeddings. See [Embedding Service](#embedding-service) below.

:::

## LLMProvider

For direct LLM access without queue infrastructure, use `LLMProvider`. This is the recommended approach for CLI applications, scripts, and core service integration.

**Import:**

```python
from chaoscypher_core import LLMProvider
```

:::tip[Engine shortcut]

If using `Engine`, access a pre-wired provider via `engine.llm_provider`, or use the convenience methods `engine.chat()`, `engine.embed()`, and `engine.batch_embed()` directly.

:::

**Constructor:**

```python
LLMProvider(
    settings: Any | None = None,            # Optional; defaults to EngineSettings() (Ollama on http://localhost:11434)
    managers: LLMManagers | None = None,    # Optional: service managers for tool execution
)
```

The `managers` parameter is an optional `TypedDict` providing service dependencies for tool execution during chat. For basic chat (no tool calling), omit it. For tool execution, provide at minimum `graph_manager`:

```python
# Basic usage (chat only) -- no settings needed for default Ollama
llm = LLMProvider()

# With custom settings
llm = LLMProvider(settings=settings)

# With tool execution support
llm = LLMProvider(settings=settings, managers={
    "graph_manager": engine.graph_repository,
    "search_manager": engine.search_repository,
})
```

:::note

Tool execution reads the `graph_manager` and `search_manager` keys. The bare `graph`, `search`, and `config` keys also exist on `LLMManagers` but are reserved for neuron worker wiring — they are ignored during tool execution.

:::

### Chat Completion

```python
from chaoscypher_core import LLMProvider

llm = LLMProvider()

# String shorthand — auto-wrapped as a user message
response = await llm.chat("What is a knowledge graph?")
print(response.content)

# Full message list for multi-turn or system prompts
response = await llm.chat(
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is a knowledge graph?"},
    ],
)

print(response.content)
print(f"Tokens: {response.usage.total_tokens}")
```

**Response format** (returns an `LLMChatResponse` Pydantic model):

```python
response.content       # "A knowledge graph is..."
response.tool_calls    # None, or list of tool calls if tools were provided
response.thinking      # None, or thinking process if enable_thinking=True
response.usage         # TokenUsage(input_tokens=42, output_tokens=128, total_tokens=170)
response.provider      # "ollama"
response.is_stream     # False
```

### Streaming Chat

```python
response = await llm.chat(
    messages=[{"role": "user", "content": "Explain entropy"}],
    stream=True,
)

# response.stream is an async generator
async for chunk in response.stream:
    print(chunk.content, end="", flush=True)
```

### Tool Calling

```python
response = await llm.chat(
    messages=[{"role": "user", "content": "Search for quantum computing"}],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "search_graph",
                "description": "Search the knowledge graph",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        }
    ],
)

if response.tool_calls:
    for call in response.tool_calls:
        print(f"Tool: {call['function']['name']}, Args: {call['function']['arguments']}")
```

## Configuring Providers

### Ollama (Local, Default)

Ollama is the default provider — zero configuration needed if Ollama is running locally with `qwen3:30b-instruct` pulled. The default instance URL is `http://localhost:11434`, overridable via the `CHAOSCYPHER_OLLAMA_URL` environment variable (the Docker images set `CHAOSCYPHER_OLLAMA_URL=http://host.docker.internal:11434` so containers reach Ollama on the host):

```python
from chaoscypher_core import EngineSettings

settings = EngineSettings()  # Uses Ollama defaults
```

Override only what differs from the defaults:

```python
settings = EngineSettings(llm={"ollama_chat_model": "llama3:70b"})
```

<details>
<summary>All Ollama defaults</summary>

| Setting | Default |
|---------|---------|
| `chat_provider` | `ollama` |
| `ollama_instances` | `[OllamaInstance(id="default", name="Default", base_url="http://localhost:11434")]` |
| `ollama_chat_model` | `qwen3:30b-instruct` |
| `ollama_num_ctx` | `32768` |
| `ollama_extraction_model` | same as `ollama_chat_model` |

The default instance `base_url` honors the `CHAOSCYPHER_OLLAMA_URL` environment variable when set. The Docker images and compose stack set it to `http://host.docker.internal:11434` so containers reach an Ollama running on the host.

To override the URL programmatically, edit the seeded instance directly:

```python
from chaoscypher_core.settings import EngineSettings, OllamaInstance

settings = EngineSettings(
    llm={
        "ollama_instances": [
            OllamaInstance(
                id="default",
                name="Default",
                base_url="http://my-ollama-host:11434",
            ),
        ],
    },
)
```

</details>

### OpenAI

```python
settings = EngineSettings(
    llm={
        "chat_provider": "openai",
        "openai_api_key": "sk-...",
        "openai_chat_model": "gpt-4.1",
        # Optional: separate extraction model
        "openai_extraction_model": "gpt-4.1",
    },
)
```

### Anthropic

```python
settings = EngineSettings(
    llm={
        "chat_provider": "anthropic",
        "anthropic_api_key": "sk-ant-...",
        "anthropic_chat_model": "claude-sonnet-4-5",
    },
)
```

### Gemini

```python
settings = EngineSettings(
    llm={
        "chat_provider": "gemini",
        "gemini_api_key": "...",
        "gemini_chat_model": "gemini-2.5-pro",
    },
)
```

## Embedding Service

Vector embeddings are produced by a dedicated **embedding provider** that, by default, runs locally on the CPU using [sentence-transformers](https://www.sbert.net/). This is independent of LLM providers — no API keys or external services are needed for the local default. Cloud providers (OpenAI, Gemini) and Ollama are also supported and selected via `EmbeddingSettings.provider`.

**Build a provider:**

```python
from chaoscypher_core import EngineSettings, create_embedding_provider

provider = create_embedding_provider(EngineSettings())
result = await provider.embed("Knowledge graph technology")
```

:::tip[Engine shortcut]

If using `Engine`, access a pre-wired provider via `engine.embedding_service`, or use the convenience methods `engine.embed()` and `engine.batch_embed()` directly.

:::

### Quick Embedding

The simplest way to generate embeddings — uses default model and settings:

```python
from chaoscypher_core import embed

result = await embed("Knowledge graph technology")
print(f"Dimensions: {len(result.embedding)}")  # 1024

# Batch embedding
results = await embed(["First document", "Second document"])
print(f"Total: {results.total}")  # 2
```

<details>
<summary>Custom embedding model</summary>

Override the model directly via the `model` parameter:

```python
from chaoscypher_core import ChaosCypher

result = await ChaosCypher.embed("Knowledge graph technology", model="BAAI/bge-large-en-v1.5")
```

Or configure it globally:

```python
ChaosCypher.configure(embedding_model="BAAI/bge-large-en-v1.5")
result = await ChaosCypher.embed("Knowledge graph technology")
```

For full control, use `create_embedding_provider` (available as a top-level export):

```python
from chaoscypher_core import create_embedding_provider, EngineSettings

settings = EngineSettings(embedding={"model": "BAAI/bge-large-en-v1.5"})
provider = create_embedding_provider(settings)
result = await provider.embed("Knowledge graph technology")
```

</details>

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `embedding.model` | `Qwen/Qwen3-Embedding-0.6B` | Any HuggingFace sentence-transformers model ID |
| `embedding.provider` | `local` | Embedding provider: local, ollama, openai, gemini |
| `search.vector_dimensions` | `1024` | Output dimensions (Matryoshka Representation Learning (MRL) truncation) |

The model downloads automatically on first use and is cached. All encoding runs on background threads via `asyncio.to_thread()` to keep the event loop responsive.

### Response Models

**`EmbedResult`** — Single embedding:

```python
result.embedding    # list[float] — truncated to vector_dimensions
result.provider     # "local"
```

**`BatchEmbedResult`** — Batch embedding:

```python
result.embeddings   # list[list[float]] — same order as input
result.total        # int — total texts processed
result.failed       # int — always 0 for local embeddings
result.provider     # "local"
```

## Health Checks

Verify provider connectivity before starting operations:

```python
# Via Engine (recommended)
health = await engine.check_health()
print(f"Chat: {health.chat.status}")

# Standalone
from chaoscypher_core import LLMProvider
health = await LLMProvider().check_health()
```

<details>
<summary>Advanced: Internal Factory API</summary>

For lower-level health checking, use `ProviderFactory` directly:

```python
from chaoscypher_core import ProviderFactory

factory = ProviderFactory(settings)

# Check chat provider -- returns a plain dict, not a model
chat_health = await factory.check_provider_health("chat")
print(f"Chat: {chat_health['status']}")  # "healthy" or "unhealthy"
print(f"Model: {chat_health.get('model')}")
print(f"Response time: {chat_health.get('response_time_ms')}ms")
```

</details>

## Multi-Instance Ollama

For high-throughput scenarios, Chaos Cypher supports load balancing across multiple Ollama instances:

```python
settings = EngineSettings(
    llm={
        "chat_provider": "ollama",
        "ollama_instances": [
            {"id": "gpu1", "name": "GPU 1", "base_url": "http://gpu1:11434"},
            {"id": "gpu2", "name": "GPU 2", "base_url": "http://gpu2:11434"},
        ],
        "ollama_load_balancing": "round_robin",  # or "least_loaded", "random"
    },
)
```

The load balancer automatically acquires and releases instance slots, distributing requests across healthy instances. Streaming requests bypass the load balancer and use the default single-provider path.

<details>
<summary>Advanced: Provider Factory</summary>

`ProviderFactory` is an internal API for obtaining raw provider instances. It handles provider selection, caching, and configuration extraction from settings. For most use cases, prefer `LLMProvider` or `engine.llm_provider` instead.

`ProviderFactory` is available as a top-level export:

```python
from chaoscypher_core import ProviderFactory
```

**Constructor:**

```python
ProviderFactory(
    settings: Any,  # Must have a .llm attribute with LLMSettings fields
)
```

**Methods:**

| Method | Returns | Notes |
|--------|---------|-------|
| `get_chat_provider()` | `BaseLLMProvider` | Uses `settings.llm.chat_provider` |
| `get_extraction_provider()` | `BaseLLMProvider` | Uses extraction model if configured, else chat model |
| `check_provider_health(provider_type)` | `async -> dict` | Tests provider connectivity |

Provider instances are **cached** -- calling `get_chat_provider()` twice returns the same instance, reusing the underlying connection.

</details>

## Finish-reason propagation

Every provider must populate a normalized `finish_reason` on its
chat response so the extraction pipeline can decide whether a chunk
truncated, was content-filtered, or completed cleanly. The
[Extraction Task API](../reference/api/sources.md#extractiontaskresponse)
exposes this field per chunk, and the source-row counters
(`llm_chunks_truncated`, `llm_chunks_aborted_by_loop`) are derived
from it.

### Canonical values

The pipeline's stable vocabulary is six tokens:

| Value | Meaning |
|-------|---------|
| `stop` | Model finished naturally (end of turn / `<eos>` / Anthropic `end_turn` / Gemini `STOP`). |
| `length` | Model hit the output-token cap (`length` / Anthropic `max_tokens` / Gemini `MAX_TOKENS`). Drives `llm_chunks_truncated`. |
| `content_filter` | Provider's safety system blocked the response (Gemini `SAFETY` / `RECITATION` / `BLOCKLIST` / `PROHIBITED_CONTENT` / `SPII`; OpenAI `content_filter`). |
| `tool_calls` | Model emitted tool calls instead of free text (OpenAI `tool_calls` / Anthropic `tool_use`). |
| `error` | Provider returned a malformed-tool-call or hard error mid-stream. |
| `unknown` | Stream ended without a recognizable finish reason — the helper falls back to this rather than `null` so callers always see a non-null token. |

### Where to wire it

`chaoscypher_core.adapters.llm.providers.base` exports two helpers
that every provider's streaming implementation calls:

```python
from chaoscypher_core.adapters.llm.providers.base import (
    extract_streaming_finish_reason,
    normalize_finish_reason,
)

last_chunk = None
async for chunk in stream:
    ...  # accumulate content / tokens
    last_chunk = chunk

raw = extract_streaming_finish_reason(last_chunk)
finish_reason = normalize_finish_reason(raw)
```

`extract_streaming_finish_reason` looks in the standardized
`response_metadata` dict first (where most LangChain providers stash
the value) and falls back to the chunk's `finish_reason` attribute. It
returns the *raw* provider value so callers can decide whether to
normalize.

`normalize_finish_reason` maps every known raw value (OpenAI `stop` /
`length` / `tool_calls`, Anthropic `end_turn` / `max_tokens` /
`stop_sequence` / `tool_use`, Ollama `load` / `unload`, Gemini's
uppercase enum) to one of the six canonical tokens. Anything
unrecognized normalizes to `"unknown"`.

The four built-in providers (Ollama, OpenAI, Anthropic, Gemini) all
go through this path. Providers added via the `chaoscypher.providers`
entry-point group should follow the same pattern — populate
`finish_reason` on the `LLMChatResponse` so chunk truncation and
abort visibility don't break.

### Streaming line-buffer flush

The streaming consumer (`_consume_extraction_stream` in
`utils/ai_entities.py`) flushes any trailing partial line through the
loop detector when the stream ends, so the last entity / relationship
line is no longer silently dropped when the model tops out mid-token.
Provider authors don't need to do anything for this — it's handled in
the shared consumer.

## BaseLLMProvider Interface

All providers implement the `BaseLLMProvider` abstract base class:

```python
from chaoscypher_core import BaseLLMProvider
```

**Required members for concrete providers:**

| Member | Description |
|--------|-------------|
| `metadata` (property) | Returns a `PluginMetadata` instance whose `plugin_id` is the provider name used by `ProviderRegistry` and the `chaoscypher.providers` entry-point group. Return a cached instance (a classvar or an attribute set in `__init__`). |
| `_init_llm()` | Initialize the LangChain chat model |
| `chat(messages, tools, stream, **kwargs)` | Chat completion (streaming and non-streaming) |

All three are abstract -- a subclass implementing only `_init_llm()` and `chat()` cannot be instantiated.

### Registering a New Provider

The four built-in providers are seeded by the `ProviderRegistry` class from its private `_BUILTIN_PROVIDERS` classvar. Third-party providers register through the `chaoscypher.providers` entry-point group -- no Core edits needed:

1. Subclass `BaseLLMProvider`, implementing `metadata`, `_init_llm()`, and `chat()`.
2. Set a `_METADATA` `PluginMetadata` classvar on the class (the registry reads `plugin_id` from it).
3. Declare the entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."chaoscypher.providers"]
my_provider = "my_package.provider:MyCustomProvider"
```

On startup, `ProviderRegistry` scans the entry-point group and registers each provider class. Classes that are not `BaseLLMProvider` subclasses and classes missing `_METADATA` are logged and skipped, so a misbehaving third-party provider cannot crash registry discovery.

The registry pattern follows the Open/Closed Principle -- new providers can be added without modifying existing code.
