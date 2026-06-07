---
id: filtering-modes
title: Filtering Modes
description: How the 0-5 filtering mode slider tunes ChaosCypher's extraction pipeline — what each mode keeps, what it drops, and which mode fits which kind of source.
---

# Filtering modes

When you upload a source, ChaosCypher's AI extracts entities and
relationships from the text. The raw output contains some noise — the AI
may repeat itself, hallucinate, or label things with generic types. The
**filtering mode** slider controls how aggressively the pipeline cleans
that raw output.

There are six modes, sliding from "keep everything" to "only confident
facts." Pick the one that matches the kind of source you're importing.

## At a glance

| Mode | Vibe | What's on |
|------|------|-----------|
| 0 unfiltered | Raw | Nothing (just index validation) |
| 1 minimal | Permissive | Relationship caps |
| 2 lenient | Narrative-friendly | + types, plausibility, structural, orphans |
| 3 balanced | Default | Standard thresholds |
| 4 strict | Structured docs | Strict edge types, tighter caps |
| 5 maximum | Curated | Highest plausibility, tightest caps, smallest aliases |

Three knobs move monotonically as you slide the mode up:

| Knob | 0 unfiltered | 1 minimal | 2 lenient | 3 balanced | 4 strict | 5 maximum |
|------|--------------|-----------|-----------|------------|----------|-----------|
| `loop_max_entity_count` | 200 | 100 | 75 | 50 | 35 | 25 |
| `semantic_dedup_threshold` | 0.99 | 0.97 | 0.93 | 0.90 | 0.87 | 0.85 |
| `minimum_alias_length` | 1 | 1 | 2 | 2 | 3 | 3 |

`loop_max_entity_count` aborts a chunk whose LLM stream emits more
entity lines than the cap (catches degenerate loops earlier in stricter
modes). `semantic_dedup_threshold` is the cosine-similarity bar for
merging two entities; lower means more aggressive merging.
`minimum_alias_length` drops short aliases like "AI" or "ML" in stricter
modes to keep the alias index focused on full names.

## The six modes

### 0 — Unfiltered

Keep everything the AI produced, including obvious noise. Useful when
you're debugging extraction quality or want to see exactly what the AI
saw before any filters touched it.

**What's on:**

- Index validation only (relationships pointing at non-existent entities
  are still dropped — those would crash the graph).

**What's off:** every other filter.

### 1 — Minimal

Drop only the obviously broken stuff. Tolerant of repetition and unusual
entity types.

**What's on:** relationship caps (loose), invalid-reference checks.

**What's off:** type constraints, plausibility, structural filtering,
orphan filtering.

**Use for:** raw inspection, fiction with experimental structure,
collections where you'd rather audit yourself than trust the system.

### 2 — Lenient

Forgiving with prose that uses pronouns and loose references. Designed
for narrative text where the AI has to do a lot of pronoun resolution.

**What changes vs. minimal:**

- Type constraints turned on (relationships must align with their entity
  types, but rules are forgiving).
- Plausibility filter turned on, but at low thresholds (0.20 named, 0.10
  non-named).
- Structural filtering turned on (drops "Chapter 1" / "Section A" style
  noise).
- Orphan filtering turned on (drops entities with no relationships).

**Use for:** novels, biographies, narrative non-fiction, journals.

### 3 — Balanced (default)

Sensible filtering for general-purpose documents. Medium evidence and
type rules; standard plausibility thresholds.

**Use for:** blog posts, articles, mixed corpora, "I don't know what
kind of document this is, just work."

### 4 — Strict

Tightens type constraints and visual-content plausibility. Drops
relationships that don't match the active domain's edge templates.

**What changes vs. balanced:**

- Strict edge-type constraints: relationships whose source/target types
  don't fit the domain's allowed edge templates are dropped.
- Visual content plausibility factor raised from 0.5 to 1.0.
- Per-entity relationship caps tightened (max degree 20, max
  same-source-type 10, max ratio 6.0).
- Loop detection triggers earlier (35 entities/chunk vs. 50).
- Aliases must be 3+ characters.

**Use for:** structured technical docs, API references, scientific
papers, domain-specific corpora where you have well-defined entity and
edge templates.

### 5 — Maximum

Highest precision. Drops anything below a high quality bar. Use when
you want only confident, well-evidenced facts and you're willing to
miss some real content to get there.

**What changes vs. strict:**

- Plausibility threshold raised (0.40 named, 0.25 non-named).
- Per-entity caps tightened further (max degree 15 vs. 20, max
  same-source-type 7 vs. 10, max ratio 4.0 vs. 6.0).
- Loop detection triggers earliest (25 entities/chunk).
- Semantic dedup threshold lowest (0.85), so more aggressive merging.

**Use for:** building a curated knowledge graph, executive briefings,
fact-checking corpora, high-precision search.

## How filtering mode interacts with re-extract

Filtering mode is a property of the source, persisted at upload time. The
UI **Re-extract** action (the three-dot source menu) re-runs extraction
with the source's stored filtering mode — it does not offer a mode picker.

To re-extract under a *different* mode, call the extraction API directly:
`POST /sources/{id}/extraction` with `force=true` and `filtering_mode=…`.
(The dedicated `POST /sources/{id}/re_extract` endpoint always reuses the
stored mode and takes no `filtering_mode` argument.)

## What about my counters?

Every filter that drops content increments a counter on the source's
data quality panel (see [Quality Analysis](../user-guide/quality.md)).
After a re-extract, those counters reset to zero and accumulate fresh
stats so you can see exactly what the new mode did differently.
