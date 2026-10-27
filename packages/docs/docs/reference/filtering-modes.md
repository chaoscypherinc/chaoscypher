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

### Domain packs and the slider

A domain pack may tune `loop_max_entity_count` (and `minimum_alias_length`)
for its kind of text; the slider still moves it. At the default `balanced`
the pack's value applies as tuned. A looser mode raises it to at least that
mode's value, a stricter mode lowers it to at most that mode's value, so it
never moves against your choice. A preset the pack names itself (used only
where no mode was chosen and no source row exists, as in the library's
standalone extraction) is not a slider move: there the pack's values apply as
tuned. The pack's other `extraction_limits` apply on top of any preset. The
technical pack's cap of 30, for example:

| | 0 unfiltered | 1 minimal | 2 lenient | 3 balanced | 4 strict | 5 maximum |
|--|--------------|-----------|-----------|------------|----------|-----------|
| technical pack's cap | 200 | 100 | 75 | 30 | 30 | 25 |

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
- Orphan filtering turned on: an entity with no relationships is kept,
  flagged as unconnected, when it carries a description or an evidence
  sentence; only one carrying neither is dropped.

**Use for:** novels, biographies, narrative non-fiction, journals.

### 3 — Balanced (default)

Sensible filtering for general-purpose documents. Medium evidence and
type rules; standard plausibility thresholds.

**Use for:** blog posts, articles, mixed corpora, "I don't know what
kind of document this is, just work."

### 4 — Strict

Tightens type constraints and visual-content plausibility. Flags
relationships that don't match the active domain's edge templates.

**What changes vs. balanced:**

- Strict edge-type constraints: relationships whose source/target types
  don't fit the domain's allowed edge templates are kept flagged
  `type_mismatch`, and a label no template answers to is kept as
  `related_to` flagged `unmatched_label` (until October 2026 both were
  dropped; every extraction plan runs the check strict).
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

## Keep, flag, never delete

A mode decides how strict each filter is; since October 2026 no filter
throws away a record that carries evidence (a description or an evidence
sentence). It keeps the record with a flag, and the consumers decide
(chat retrieval ranks a flagged record after the others; the graph canvas
links flagged nodes to its "Unresolved (N)" node and draws flagged edges
thin). What each filter does now:

| Filter | Before | Now |
|---|---|---|
| Plausibility score | dropped a name below the bar | keeps one that carries evidence, flagged `low_plausibility` with its score; drops one carrying nothing |
| Type rescue | dropped an entity no core type answered to | keeps it as a `Concept`, flagged `unplaced_type`, the written type beside it |
| Relationship caps (same source and type, degree, total) | dropped the edges past a cap | keeps every edge, flagging the ones past a cap `capped` (they walk at a tenth of an edge's weight and do not count toward later caps) |
| Exclusion examples and plausibility, for a reference | kept a *person* written by a title or description (`the count`) | keeps a reference of any type (`the army`, `the government`, `the village`), flagged `descriptor_reference` |
| Orphan filter at commit | dropped an edgeless entity | keeps one that carries evidence, flagged `orphan` |
| Evidence filter, for an entity | dropped one its cited sentence does not name | keeps one that carries a description, flagged `unevidenced` with the reason; drops one carrying nothing |
| Exclusion examples, for a name | dropped a non-person excluded name | keeps one that carries evidence, flagged `excluded_name` with the rule; drops one carrying nothing |
| Strict edge-type constraints | dropped an edge whose label matched no template, or whose endpoints the template refused | keeps it, as `related_to` flagged `unmatched_label` (the written label beside it), or flagged `type_mismatch` with the reason |
| Alias cleaner | deleted an alias sharing no word with the name | keeps an alias its text writes (`Lise`, `Annette`, `Moskva`); moves an unwritten one to `descriptors` |

The thresholds the modes set still decide *which* records are flagged.
The counters below count the flags where nothing is dropped.

## How filtering mode interacts with re-extract

Filtering mode is a property of the source, persisted at upload time. The
UI **Re-extract** action (the three-dot source menu) re-runs extraction
with the source's stored filtering mode — it does not offer a mode picker.

To re-extract under a *different* mode, pass `filtering_mode` to either
endpoint:

- `POST /sources/{id}/extraction` with `force=true` and `filtering_mode=…`
- `POST /sources/{id}/re_extract` with `{"filtering_mode": "…"}` in the body

Both apply the mode to **that run only** — the source row keeps its
upload-time value, so a later re-extract without an override returns to it.

Fixed 2026-08-04: until then neither route worked. The extraction endpoint's
`filtering_mode` was unreachable (the worker read the source row first, and the
column is non-nullable with `default="balanced"`, so it was never empty), and
`re_extract` declared no request body at all — so both silently ran a full,
token-costing re-extraction under the stored mode.

## What about my counters?

Every filter that drops content, or keeps it flagged, increments a counter
on the source's data quality panel (see [Quality Analysis](../user-guide/quality.md)):
`plausibility_entities_flagged`, `unplaced_type_entities_flagged`,
`entities_unevidenced_kept`, `excluded_entities_kept`,
`relationships_unevidenced_kept`, `relationships_unmatched_label_kept`,
`relationships_type_mismatch_kept`, `orphan_entities_filtered` (orphans
kept), `relationships_dropped_capped` (edges past a cap, kept) and
`relationships_dropped_type_unmatched` (edges the strict type check kept
flagged) count records kept with a flag.
After a re-extract, those counters reset to zero and accumulate fresh
stats so you can see exactly what the new mode did differently.
