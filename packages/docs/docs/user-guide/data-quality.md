---
id: data-quality
title: Pipeline Flow & Quality Counters
description: How to read the Pipeline flow section on a source's Overview tab — the quality counters that track silent drops at every pipeline stage, when to use them, and how they reset.
---

# Pipeline flow & quality counters

When you open a source, the collapsible **Pipeline flow** section on its
**Overview** tab surfaces the quality counters
that record what the pipeline dropped, deduplicated, or merged on its
way from your file to the knowledge graph. The counters live alongside
the [Quality Analysis](quality.md) grade — but they answer a different
question.

- The **quality grade** asks *"how good is the graph this source produced?"*
- **The pipeline counters** ask *"what did the pipeline silently drop on the way to the graph?"*

If the grade looks low, the counters often tell you why.

## Where to find the counters

Open a source from the **Sources** page and stay on the **Overview** tab.
Below the stat tiles sits a collapsible **Pipeline flow** panel:

- **Collapsed** (the default), it shows a muted strip of stat cards
  summarizing the run.
- **Expanded**, it reveals the full pipeline funnel with a per-stage stats
  board — a curated selection of the most diagnostically useful counters
  (not the complete field set — see below), shown only when non-zero for
  this source, grouped by the stage that incremented it (loading,
  normalization, chunking, LLM extraction, post-extraction, commit).

## What do the counters record?

Every stage of the pipeline — loading, cleaning, chunking, the LLM
extraction, post-processing, and commit — has at least one place where
content gets removed for a defensible reason: a duplicate paragraph,
a chunk full of boilerplate, an LLM stream that ran into a loop, an
invalid relationship referencing a non-existent entity. Pre-May 2026
those drops happened silently. Now each one increments a typed counter
on the source row.

The counters are **best-effort**: if a counter increment fails
(database write contention, for example) the pipeline keeps going. They
exist for visibility, never for control flow.

## The counters

The table below shows the most frequently-consulted counters. The complete field set (the `QualityCounter` enum, `chaoscypher_core.services.quality.counters`, is the source of truth and the only place the count lives) spans every silent-drop site in the pipeline and is documented in the [Quality Metrics API reference](../reference/api/quality-metrics.md) with per-field semantics. The Pipeline flow board in the UI does not render the complete field set — it surfaces a curated subset of these counters, grouped by stage and shown only when non-zero for the current source.

| Counter | What it measures | Stage |
|---------|------------------|-------|
| `loader_warnings_count` | Non-fatal loader hiccups (one bad JSONL line, an undecodable archive member, a worksheet that couldn't be read). The file still loaded, but you should know something was off. | Loading |
| `loader_files_skipped` | For archives only — how many entries the archive loader skipped (unsupported extensions, oversized files, security violations). | Loading |
| `cleaner_lines_removed` | Gibberish lines, page numbers, repeated headers / footers dropped by the OCR cleaner. | Normalization |
| `cleaner_paragraphs_deduplicated` | Duplicate paragraphs collapsed by the cleaner — common in two-column PDFs where OCR sees the same text twice. | Normalization |
| `cleaner_chars_removed` | Net character delta from text cleaning (encoding fixes, control-character removal, whitespace collapse). | Normalization |
| `chunks_coalesced_count` | Chunks that were merged into a neighbor (coalesced) by the chunker because they fell below `min_chunk_size`. The content still reaches extraction — it's just folded into an adjacent chunk so the LLM sees larger, more contextful units. Phase 7 rename of the legacy `chunks_filtered_count`. | Chunking |
| `llm_chunks_truncated` | Chunks where the LLM hit its token cap and the response was cut off mid-output. Some content was extracted; some was lost. | LLM extraction |
| `llm_chunks_aborted_by_loop` | Chunks where the streaming loop detector aborted the LLM mid-response (degenerate repetition, out-of-bounds indices, runaway entity counts). | LLM extraction |
| `parser_lines_dropped` | Malformed `E\|`/`R\|`/`P\|` lines the parser couldn't make sense of (missing fields, out-of-bounds indices, bad numeric values). | LLM extraction |
| `dedup_entities_merged` | Entities collapsed into another entity by exact-name or semantic deduplication. The merge keeps the highest-confidence record and combines aliases / properties. | Post-extraction |
| `structural_entities_filtered` | Entities representing document structure (chapters, sections, page headers) removed by the structural filter. Configured per-domain. | Post-extraction |
| `orphan_entities_filtered` | Entities that survived deduplication with zero relationships and were **kept**, flagged as unconnected, because they carry a description or an evidence sentence. They appear in the graph canvas's "Unresolved" region. Before October 2026 this counted the ones deleted. | Commit |
| `evidence_less_entities_dropped` | Entities with zero relationships and neither a description nor an evidence sentence, dropped at commit when orphan protection is off. | Commit |
| `relationships_unevidenced_kept` | Relationships whose cited sentence does not state them, **kept** with their justification and flagged unevidenced (the reason is on the edge). They are drawn dashed on the graph canvas and left out of chat answers unless `graphrag.include_unevidenced_edges` is on. Before October 2026 these were deleted and counted in `evidence_relationships_dropped`, which now counts only relationships with an endpoint that is no entity. | Extraction |
| `relationships_dropped_invalid` | Relationships pointing at non-existent entity indices (catches LLM index-skew). These would crash the graph if committed. | Post-extraction / Commit |
| `relationships_dropped_capped` | Relationships past the per-entity degree cap, the same-source-type cap, or the total-ratio cap from the active filtering mode, **kept** and flagged as capped (ranked last, drawn thin on the canvas). Before October 2026 this counted the ones dropped. | Post-extraction |
| `plausibility_entities_flagged` | Entities whose name scored below the plausibility bar, **kept** and flagged as low plausibility because they carry a description or evidence. They appear in the canvas's "Unresolved" region. | Extraction |
| `unplaced_type_entities_flagged` | Entities whose written type no core type answers to, **kept** as a Concept and flagged as an unplaced type. They appear in the canvas's "Unresolved" region. | Extraction |
| `entities_unevidenced_kept` | Entities whose cited sentence does not name them, **kept** because they carry a description and flagged unevidenced (the reason is on the entity). They appear in the canvas's "Unresolved" region. Before October 2026 these were deleted and counted in `evidence_entities_dropped`, which now counts only entities with no description. | Extraction |
| `excluded_entities_kept` | Entities whose name is one of the domain's exclusion examples, **kept** because they carry a description or evidence and flagged as an excluded name (the rule is on the entity). They appear in the canvas's "Unresolved" region. | Extraction |
| `relationships_unmatched_label_kept` | Relationships whose label no relationship type of the domain answers to, **kept** as `related_to` with the label as written, flagged as an unmatched label and drawn thin on the canvas. | Post-extraction |
| `relationships_type_mismatch_kept` | Relationships whose endpoints are not the types their relationship type allows, **kept** with their label, flagged as a type mismatch and drawn thin on the canvas. Before October 2026 these and the unmatched labels were deleted and counted in `relationships_dropped_type_unmatched`, which now counts the two kept together. | Post-extraction |
| `citations_skipped_no_chunk_index` | Citations skipped at commit because the underlying entity / relationship had no chunk index to point at. | Commit |

In addition to the counters, the section surfaces three companion
fields:

- `loader_encoding_used` — which encoding the loader actually used for
  this file (`utf-8`, `cp1252`, `latin-1-fallback`, etc.). Useful when
  you're staring at mojibake on the source detail page.
- `vector_indexed_at` — when the vector indexing call succeeded, or
  `null` if it hasn't.
- `vector_indexing_status` — `pending`, `indexed`, `degraded`, or
  `failed`. See [Search Status](search-status.md).

## When to consult counters vs. the grade

The grade is the right place to start if you want to know *whether*
extraction did a good job on this source. The counters are the right
place to look if the grade is surprising and you want to know *why*.

| You see this | Look at |
|--------------|---------|
| "Why is the grade so low?" | Counters — `cleaner_lines_removed` huge means the document was mostly OCR noise; `relationships_dropped_capped` huge means a chatty LLM tripped the safety nets. |
| "Why did this 200-page PDF only produce 8 entities?" | Counters — a high `chunks_coalesced_count` means many short fragments were coalesced into neighbors (no content lost, just fewer chunks); `llm_chunks_aborted_by_loop` near the chunk total means the LLM kept looping; non-zero `llm_chunks_timed_out` or `llm_chunks_failed_permanent` means some chunks were abandoned. |
| "Why does the same source produce different graphs on Cortex vs. CLI?" | It shouldn't (Cortex / CLI / MCP are at parity as of May 2026). If you see a real difference, file a bug with both source IDs and the counter values. |
| "Why is `cleaner_chars_removed` so high?" | The text cleaner removed characters: encoding fixes, control-character stripping, whitespace collapse. Compare it to `total_content_length` — a 5-10% delta is normal. |

## Counters reset on Re-extract

When you click **Re-extract** (or call `POST
/api/v1/sources/{id}/re_extract`), every counter on the row is zeroed
back to its post-upload state. The `vector_indexing_status` resets to
`pending` and `vector_indexed_at` clears.

This is intentional: re-extract is the moment to compare *the new run*
against *the old run*. If you keep the old counters' values around (in
a screenshot, an export, a `curl` of the API), you can diff them against
the post-re-extract values to see exactly what changed. That's the
fastest way to evaluate whether bumping the filtering mode or switching
the domain actually improved things or just shuffled the counts around.

The Quality Analysis grade itself is recomputed and persists separately
— it doesn't reset.

## See also

- [Quality Analysis](quality.md) — the 0-100 grade and its component scores
- [Filtering Modes](../reference/filtering-modes.md) — what each mode does to the counters
- [Search Status](search-status.md) — the four states of `vector_indexing_status`
- [Quality Metrics API](../reference/api/quality-metrics.md) — full field reference
