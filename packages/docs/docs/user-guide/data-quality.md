---
id: data-quality
title: Data Quality Tab
description: How to read the Data Quality tab on a source — the 45 counters that track silent drops at every pipeline stage, when to use them, and how they reset.
---

# Data Quality tab

When you open a source, the **Data Quality** tab shows forty-five counters
that record what the pipeline dropped, deduplicated, or merged on its
way from your file to the knowledge graph. The counters live alongside
the [Quality Analysis](quality.md) grade — but they answer a different
question.

- The **quality grade** asks *"how good is the graph this source produced?"*
- **The Data Quality tab** asks *"what did the pipeline silently drop on the way to the graph?"*

If the grade looks low, the counters often tell you why.

## What is the Data Quality tab?

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

The table below shows the most frequently-consulted counters. The full set (45 fields spanning every silent-drop site in the pipeline) is documented in the [Quality Metrics API reference](../reference/api/quality-metrics.md) with per-field semantics; the Data Quality tab in the UI renders every counter that's non-zero for the current source, grouped by stage.

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
| `orphan_entities_filtered` | Entities that survived deduplication but had zero relationships, dropped at commit time when orphan filtering is enabled. | Commit |
| `relationships_dropped_invalid` | Relationships pointing at non-existent entity indices (catches LLM index-skew). These would crash the graph if committed. | Post-extraction / Commit |
| `relationships_dropped_capped` | Relationships dropped because they exceeded the per-entity degree cap, the same-source-type cap, or the total-ratio cap from the active filtering mode. | Post-extraction |
| `citations_skipped_no_chunk_index` | Citations skipped at commit because the underlying entity / relationship had no chunk index to point at. | Commit |

In addition to the counters, the tab surfaces three companion
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
