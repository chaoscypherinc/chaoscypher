---
id: cache
title: Cache
description: How Chaos Cypher caches pinned LLM answers and embeddings under the data directory, what it never caches, the size cap, and how to clear or turn it off.
---

# Cache

Chaos Cypher keeps the model's answers and embeddings on disk, so the same input to the same model
costs nothing the second time. Re-importing a document, retrying an extraction or asking the same
grounded question again is answered from the cache instead of the GPU or a paid API.

There are two caches, both on by default:

- **Completions**: chat and extraction answers.
- **Embeddings**: the vectors for chunks, entities and search queries.

## Why a cached answer is safe to reuse

Decoding is pinned by default: temperature 0 and a fixed seed (see
[LLM Behavior](./configuration.md#llm-behavior)). Under pinned decoding the answer is a function of
the input, so serving it again is the same as asking again. Embeddings are always deterministic.

An entry is keyed on everything that changes the answer: the exact model build (for Ollama, the
model's digest, so an `ollama pull` that changes the weights starts fresh), the messages, the
decoding and the tools. Anything else bypasses the cache and goes to the model:

- **Sampled decoding.** A raised `ai_temperature`, or a regeneration, which samples on purpose.
- **Ollama without a seed.** At temperature 0 with no seed the answer can still vary.
- **Cut-off answers.** An answer stopped by the loop guard, the token limit or an error is never
  stored.

## Hosted providers

OpenAI, Anthropic and Gemini do not honour a seed, so even at temperature 0 the same question can
come back worded differently. Their answers are still cached at temperature 0, keyed on the model
id, but a hit is the first answer seen rather than the one deterministic answer. Those entries are
flagged unseeded. If you want a fresh answer from a hosted model every time, turn the completion
cache off.

## Size and eviction

Each cache is capped at 2 GB. Past the cap, the entries that were used least recently are deleted
until the cache is back under 90% of the cap. Change the cap per cache in `settings.yaml`
(16 MB minimum):

```yaml
cache:
  completions:
    max_bytes: 4294967296   # 4 GB
  embeddings:
    max_bytes: 1073741824   # 1 GB
```

## Where the files live

In `<data_dir>/cache/llm`. In Docker that is `/data/cache/llm`, inside the same `/data` volume as
your databases and `settings.yaml`, so the cache survives container upgrades. On a local install
it sits under the platform data directory (for example `~/.local/share/chaoscypher/cache/llm` on
Linux). Set `cache.dir` to put it somewhere else.

The cache is derived data: backups leave it out, and deleting the directory only means the next
calls go to the model again.

## The Settings page

**Settings → General → Cache** shows one card per cache:

- an **enable** switch, which applies at once (no Save needed);
- the number of entries and the size against the cap;
- the hit rate, with hits and misses on hover;
- the time saved, the model time each hit would have cost;
- a table by call site (extraction, chat, embedding the index, embedding queries, and so on) with
  hits, misses, bypasses and time saved, which is how you spot a stage that never hits;
- **Clear**, which deletes every entry after a confirmation.

## Clear or turn it off

- **Clear** a cache from its card on the Settings page. The entries go; the hit and miss counts
  stay.
- **Turn it off** with the card's switch, or in `settings.yaml`:

  ```yaml
  cache:
    completions:
      enabled: false
  ```

  or with an environment variable: `CHAOSCYPHER_CACHE__COMPLETIONS__ENABLED=false`
  (`CHAOSCYPHER_CACHE__EMBEDDINGS__ENABLED` for the other). Off stops both reading and writing;
  the stored entries and stats stay until you clear them.
