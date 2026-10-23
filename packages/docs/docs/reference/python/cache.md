---
title: "Cache API"
---

# Cache API

The LLM response and embedding caches: a SQLite store and the wrappers that serve pinned completions and embeddings from it.

## `chaoscypher_core.cache`

LLM response and embedding caches: the same input to the same model build costs nothing twice.

A store (`SqliteCacheStore`, one SQLite file) holds pinned
completions (`CachedChatProvider`) and embeddings
(`CachedEmbeddingProvider`) keyed on the model build and the normalised
input (`chaoscypher_core.cache.keys`), so the same input to the same
model costs nothing the second time. Only pinned decoding is cached
(`chaoscypher_core.cache.policy`); stats are kept per call site
(`chaoscypher_core.cache.call_site`).

This package imports only `chaoscypher_core.models`, `ports`, `utils`,
`exceptions` and `settings`; the provider factories import it
(`chaoscypher_core.cache.wiring`), never the reverse.

Wrap any chat provider (an object with `BaseLLMProvider.chat` and a
`config` naming its `chat_provider` and `ai_temperature`) and the
second identical pinned call is served from the file. Without a build
resolver the key names `provider:model`; an Ollama call is cached only
when it carries a seed. `CachedEmbeddingProvider` wraps an embedding
provider the same way, keyed on the `model_build` you pass.

Example:

```
from pathlib import Path

from chaoscypher_core.cache import CachedChatProvider, SqliteCacheStore

store = SqliteCacheStore(Path("cache/completions.db"), max_bytes=2 * 1024**3)
provider = CachedChatProvider(my_provider, store)
answer = await provider.chat([{"role": "user", "content": "Who kept the accounts?"}])
```

## `chaoscypher_core.cache.store`

What a cache store holds and the protocol every store implements.

A store maps `(namespace, key)` to a `CacheEntry`: the stored
object as zlib-compressed canonical JSON (`encode_value`) plus the
facts the stats page needs (which model build answered, from which call
site, whether the answer is the seeded deterministic one, and what the
first call cost). Namespaces partition one store file: `completions` and
`embeddings` for the product caches, `bench:<run_id>` for a benchmark
run whose entries a reference pack exports.

Stats are kept per `(namespace, call_site)` apart from the entries, so a
`CacheStore.clear` empties the entries and keeps the history.

### `class CacheEntry`

One stored answer and the facts about how it was made.

**Attributes:**

- `bytes`: `int` — `len(value)`: what the entry weighs against the size cap.
- `call_site`: `str` — The stage that first asked (`chaoscypher_core.cache.call_site`).
- `latency_ms`: `int` — What the first call cost in model time; a hit adds it to the time saved.
- `model_build`: `str` — The model build that produced it (`chaoscypher_core.cache.keys.model_build_id`).
- `seeded`: `bool` — True when the provider honours seeds, so a hit is the deterministic answer.
- `value`: `bytes` — zlib-compressed canonical JSON of the stored object (`encode_value`).

### `class CacheStats`

One namespace's size and history, as data (the Settings page renders it).

**Attributes:**

- `by_call_site`: `dict[str, dict[str, int]]` — `{site: {hits, misses, bypasses, latency_saved_ms}}`.
- `bypasses`: `int` — Calls the policy kept away from the store (sampled or unseeded decoding).
- `bytes`: `int` — Their total stored size.
- `entries`: `int` — How many entries the namespace holds now.
- `hits`: `int` — Answers served from the store.
- `latency_saved_ms`: `int` — The sum of the first-call model time of every hit.
- `max_bytes`: `int` — The cap the caller enforces.
- `misses`: `int` — Calls that went to the model and were offered to the store.
- `namespace`: `str` — The namespace these numbers describe.

### `class CacheStore`

A key-value store of `CacheEntry` with per-call-site stats.

**Bases:** `Protocol`

**Methods:**

#### `clear(namespace: str | None = None) -> int`

Remove the entries of `namespace` (every namespace when None); return how many.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str \| None` |  |

#### `close() -> None`

Release the store's resources.

#### `get(namespace: str, key: str) -> CacheEntry | None`

The entry under `key` (bumping its recency), or None on a miss.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `key` | `str` |  |

#### `namespaces() -> list[str]`

The namespaces that hold entries, sorted.

#### `put(namespace: str, key: str, entry: CacheEntry) -> None`

Store `entry` under `key` (last write wins), evicting past the cap.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `key` | `str` |  |
| `entry` | `CacheEntry` |  |

#### `record(namespace: str, call_site: str, *, hit: bool | None, latency_saved_ms: int = 0, count: int = 1) -> None`

Count `count` calls: hits (`True`), misses (`False`) or bypasses (`None`).

`latency_saved_ms` is the total the hits saved.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `call_site` | `str` |  |
| `hit` | `bool \| None` |  |
| `latency_saved_ms` | `int` |  |
| `count` | `int` |  |

#### `stats(namespace: str, max_bytes: int) -> CacheStats`

The size and history of `namespace`.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `max_bytes` | `int` |  |

### `decode_value(value: bytes) -> Any`

The object `encode_value` stored.

| Parameter | Type | Description |
|---|---|---|
| `value` | `bytes` |  |

### `encode_value(obj: Any) -> bytes`

`obj` as zlib-compressed canonical JSON (sorted keys, no spaces, UTF-8).

| Parameter | Type | Description |
|---|---|---|
| `obj` | `Any` |  |

### `stored_value(entry: CacheEntry, *, namespace: str, key: str, shaped: Callable[[Any], bool]) -> Any | None`

The object an entry holds, or None (a soft miss) when it does not decode or is misshapen.

The one read of a stored value every wrapper uses: a value that is not
zlib (`zlib.error`), not UTF-8 or not JSON (`ValueError`), or that
`shaped` rejects (a vector that is not a list of numbers, a response
that is not a dict), is logged as `llm_cache_bad_value` and treated
as a miss, so a damaged store file costs a model call, never a crash.

| Parameter | Type | Description |
|---|---|---|
| `entry` | `CacheEntry` |  |
| `namespace` | `str` |  |
| `key` | `str` |  |
| `shaped` | `Callable[[Any], bool]` |  |

## `chaoscypher_core.cache.sqlite_store`

The one shipped `chaoscypher_core.cache.store.CacheStore`: one SQLite file.

The file is derived data with its own small schema (not Alembic): a
`meta.schema_version` that differs from `SqliteCacheStore.SCHEMA_VERSION`,
or a file that is not a database at all, deletes the file and starts empty
(logged `llm_cache_schema_reset`). WAL journaling, and `BEGIN IMMEDIATE`
around every write so two workers on one file serialise on SQLite's own
lock; identical keys hold identical values by construction, so last write
wins is correct. One connection per store, guarded by a lock so threads
sharing a store never interleave transactions.

### `class SqliteCacheStore`

A `chaoscypher_core.cache.store.CacheStore` in one SQLite file, LRU by last hit.

`put` evicts the least-recently-hit entries of the whole file (every
namespace counts against the one cap) until the total is at most
`EVICT_TO` times `max_bytes`. The total is kept by triggers in
the `totals` row, so a put never sums the table.

**Methods:**

#### `clear(namespace: str | None = None) -> int`

Remove the entries of `namespace` (every one when None); the stats are kept.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str \| None` |  |

#### `close() -> None`

Close the connection.

#### `get(namespace: str, key: str) -> CacheEntry | None`

The entry under `key` (bumping `last_hit_at` and `hits`), or None.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `key` | `str` |  |

#### `namespaces() -> list[str]`

The namespaces that hold entries, sorted.

#### `put(namespace: str, key: str, entry: CacheEntry) -> None`

Store `entry` under `key` (last write wins) and evict past the cap.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `key` | `str` |  |
| `entry` | `CacheEntry` |  |

#### `put_many(namespace: str, items: Iterable[tuple[str, CacheEntry]], *, replace: bool = True) -> int`

Store every `(key, entry)` in one transaction, evict once; return how many were new.

`replace=False` keeps an entry already under a key (an import never
overwrites what the store holds).

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `items` | `Iterable[tuple[str, CacheEntry]]` |  |
| `replace` | `bool` |  |

#### `read_schema_version(con: sqlite3.Connection) -> int | None`

The file's schema version; None for a fresh file, -1 for tables without a version.

| Parameter | Type | Description |
|---|---|---|
| `con` | `sqlite3.Connection` |  |

#### `record(namespace: str, call_site: str, *, hit: bool | None, latency_saved_ms: int = 0, count: int = 1) -> None`

Count `count` calls: hits (`True`, adding `latency_saved_ms`), misses or bypasses.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `call_site` | `str` |  |
| `hit` | `bool \| None` |  |
| `latency_saved_ms` | `int` |  |
| `count` | `int` |  |

#### `stats(namespace: str, max_bytes: int) -> CacheStats`

The size of `namespace` now and its history per call site.

| Parameter | Type | Description |
|---|---|---|
| `namespace` | `str` |  |
| `max_bytes` | `int` |  |

**Attributes:**

- `EVICT_TO` — Past the cap, entries are evicted down to this fraction of it (headroom against thrash).
- `SCHEMA_VERSION` — Bumped when the tables change; an older file is deleted and recreated.
- `max_bytes`: `int` — The size cap `put` enforces.
- `path`: `Path` — The store file.

### `read_entries(path: Path, *, namespace: str | None = None, model_build: str | None = None) -> Iterator[tuple[str, str, CacheEntry]]`

`(namespace, key, entry)` for every entry of the store file at `path` (read-only).

Opens its own read-only connection, so it reads a live store without
taking its lock and never rewrites the file it reads (a pack's store
keeps its sha256). `namespace` and `model_build` filter when given.

Raises:
    ValidationError: When the file is not a store of this schema version.

| Parameter | Type | Description |
|---|---|---|
| `path` | `Path` |  |
| `namespace` | `str \| None` |  |
| `model_build` | `str \| None` |  |

## `chaoscypher_core.cache.chat`

The completion cache: a chat provider whose pinned answers are served again.

A call is looked up under `chaoscypher_core.cache.keys.completion_key`
(the model build, the normalised messages, the EFFECTIVE decoding and the
tools) when the policy says its decoding is pinned
(`chaoscypher_core.cache.policy.cacheable`); anything else (a sampled
temperature, an unseeded Ollama call, an unknown model build, input that is
not plain message dicts) bypasses and goes to the inner provider as it came.

A non-stream answer is stored as its response dict and served as a fresh
copy; a stream is stored as its ordered chunk list and replayed as an async
generator, so the consumer's loop guard and sinks see the same shapes. Only
a clean finish (`CLEAN_FINISH_REASONS`) is stored: never a loop abort,
a length cut, an error chunk or an exception; nor a degenerate answer
(`is_degenerate_answer`, or the stricter check a caller passes): a
clean but empty answer stored once would be served on every rerun, so the
caller's retry would start past the first, pinned attempt every time. A
stream and a non-stream call of the same input are keyed apart (their stored
shapes differ).

Time saved is the sum of what each hit's first call cost: the provider's
`provider_timings.total_ms` when it reports one, else the wall clock.

### `class CachedChatProvider`

A `BaseLLMProvider` stand-in: pinned completions served from the store.

Everything but `chat` is the inner provider's (`metadata`,
`check_health`, `close`, `chat_model`, `config`...).

Example:

```
store = SqliteCacheStore(Path("cache/completions.db"), max_bytes=2 * 1024**3)
provider = CachedChatProvider(my_provider, store, build_resolver=resolve_digest)
answer = await provider.chat([{"role": "user", "content": "hi"}])
```

**Methods:**

#### `chat(messages: list[dict[str, Any]], tools: list[dict] | None = None, stream: bool = False, enable_thinking: bool = False, high_priority: bool = False, temperature: float | None = None, max_tokens: int | None = None) -> Any`

The answer: from the store when pinned and seen, else from the inner provider (then stored).

Returns a response dict, or an async generator of chunk dicts when
`stream` (a hit replays the stored chunks).

Raises:
    ValidationError: Under `require_hit` (a replay) when the call would
        reach the model: a miss (`replay miss: <key>`), an unknown
        model build, or a call the store cannot serve (sampled
        decoding, input that is not plain message dicts).

| Parameter | Type | Description |
|---|---|---|
| `messages` | `list[dict[str, Any]]` |  |
| `tools` | `list[dict] \| None` |  |
| `stream` | `bool` |  |
| `enable_thinking` | `bool` |  |
| `high_priority` | `bool` |  |
| `temperature` | `float \| None` |  |
| `max_tokens` | `int \| None` |  |

#### `check_health() -> Any`

The wrapped provider's health (never cached).

**Attributes:**

- `chat_model`: `Any` — The wrapped provider's model (read on every call: a change keys anew).
- `config`: `Mapping[str, Any]` — The wrapped provider's config (empty when it has none).
- `inner`: `ChatProvider` — The wrapped provider.
- `provider_type`: `str` — The wrapped provider's type (`config["chat_provider"]`), `unknown` when unset.

### `class ChatProvider`

What the wrapper needs of the provider it wraps: `BaseLLMProvider.chat`.

**Bases:** `Protocol`

**Methods:**

#### `chat(messages: list[dict[str, Any]], tools: list[dict] | None = None, stream: bool = False, enable_thinking: bool = False, high_priority: bool = False, temperature: float | None = None, max_tokens: int | None = None) -> Any`

A response dict, or an async generator of chunk dicts when `stream`.

| Parameter | Type | Description |
|---|---|---|
| `messages` | `list[dict[str, Any]]` |  |
| `tools` | `list[dict] \| None` |  |
| `stream` | `bool` |  |
| `enable_thinking` | `bool` |  |
| `high_priority` | `bool` |  |
| `temperature` | `float \| None` |  |
| `max_tokens` | `int \| None` |  |

### `is_degenerate_answer(answer: Mapping[str, Any]) -> bool`

Whether a clean answer is not worth serving again: blank content and no tool calls.

Covers the empty answer and the one whose output tokens all went to
thinking (output tokens spent, content empty). A tool-call answer has
no content by design and is kept. `answer` is a response dict, or a
stream's done chunk (which carries the accumulated `content` and the
`tool_calls`).

| Parameter | Type | Description |
|---|---|---|
| `answer` | `Mapping[str, Any]` |  |

## `chaoscypher_core.cache.embedding`

The embedding cache: an `EmbeddingProviderProtocol` over any provider.

Each text is looked up under `chaoscypher_core.cache.keys.embedding_key`
(the model build, the text, the provider's output dimensions and query
prefix); only the misses go to the inner provider, in one `batch_embed`
call, in first-appearance order, and the answer comes back in input order.
Embeddings are deterministic, so no decoding policy applies. A miss records
the model time it cost (the inner call's wall clock shared over its texts);
a hit adds that time to the time saved.

### `class CachedEmbeddingProvider`

`EmbeddingProviderProtocol` over any provider: misses go to inner in one batch, in input order.

Example:

```
store = SqliteCacheStore(Path("cache/embeddings.db"), max_bytes=2 * 1024**3)
provider = CachedEmbeddingProvider(my_provider, store, model_build="ollama:m:sha256:abc")
```

**Methods:**

#### `batch_embed(texts: list[str], batch_size: int = 64) -> BatchEmbedResult`

Every text's vector in input order; only the misses reach the inner provider.

| Parameter | Type | Description |
|---|---|---|
| `texts` | `list[str]` |  |
| `batch_size` | `int` |  |

#### `check_health() -> EmbeddingHealthStatus`

The wrapped provider's health (never cached).

#### `embed(text: str) -> EmbedResult`

The vector of `text`: from the store, else from the inner provider (then stored).

| Parameter | Type | Description |
|---|---|---|
| `text` | `str` |  |

**Attributes:**

- `inner`: `EmbeddingProviderProtocol` — The wrapped provider.
- `model_name`: `str` — The wrapped provider's model (a protocol member, so defined, not delegated).
- `provider_type`: `str` — The wrapped provider's type.
- `query_prefix`: `str` — The wrapped provider's query prefix (a protocol member, so defined, not delegated).

## `chaoscypher_core.cache.call_site`

Which stage is calling the model, for the per-call-site stats.

The stages set it (`with call_site("extraction"):`); the cache wrappers
read it when they record a hit, miss or bypass. A ContextVar, so
concurrent tasks keep their own site and nested sites restore on exit.

### `call_site(name: str) -> Iterator[None]`

Attribute the model calls inside the block to `name` (one of `CALL_SITES`).

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` |  |

### `current_call_site() -> str`

The call site the innermost `call_site` block set, else `other`.
