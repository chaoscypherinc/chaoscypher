---
title: Quality Metrics
description: Reference for the 45 source-row quality counters returned in SourceResponse.quality_metrics — what each counter means, when it increments, and how it resets.
---

# Quality metrics

The `quality_metrics` block on every [SourceResponse](sources.md#sourceresponse)
records what the pipeline silently dropped, deduplicated, or merged on
the way to the knowledge graph. There are 45 counters plus the encoding /
vector-index companion fields. They back the [Pipeline flow & quality counters](../../user-guide/data-quality.md)
in the UI and the data the CLI exposes via `chaoscypher source get
SOURCE_ID`.

Two counters are JSON-shaped (`loader_html_dropped_tags`,
`loader_pptx_shapes_skipped`) — `dict[str, int]` per-key breakdowns
rather than scalar totals. The UI's Pipeline flow section collapses them to a
sum + top-keys preview line; downstream consumers can drill in by key.

This page is the **field reference**. For the user-facing explanation
of when to consult counters vs. the quality grade, see the [Data
Quality tab](../../user-guide/data-quality.md) page in the user guide.

## Where counters live

```
GET /api/v1/sources/{source_id}
```

Returns a [SourceResponse](sources.md#sourceresponse). The
`quality_metrics` field has the following shape:

```json
{
  "id": "src_abc123",
  "...": "... other source fields ...",
  "quality_metrics": {
    "loader_encoding_used": "utf-8",
    "loader_warnings_count": 0,
    "loader_files_skipped": 0,
    "loader_replacement_chars_count": 0,
    "loader_pdf_pages_failed": 0,
    "loader_docx_paragraphs_skipped": 0,
    "loader_xlsx_rows_skipped": 0,
    "loader_csv_rows_truncated": 0,
    "loader_html_dropped_tags": null,
    "loader_pptx_shapes_skipped": null,
    "cleaner_lines_removed": 142,
    "cleaner_paragraphs_deduplicated": 7,
    "cleaner_chars_removed": 8420,
    "cleaner_plugin_load_failures": 0,
    "ocr_cleaner_skipped_by_predicate": 0,
    "chunks_coalesced_count": 3,
    "chunker_normalize_drops": 0,
    "chunker_prestrip_lines_removed": 0,
    "chunks_skipped_by_depth": 0,
    "standalone_chunk_failures": 0,
    "user_regex_timeout_hits": 0,
    "llm_chunks_truncated": 0,
    "llm_chunks_aborted_by_loop": 1,
    "llm_chunks_timed_out": 0,
    "llm_chunks_failed_permanent": 0,
    "parser_lines_dropped": 4,
    "semantic_dedup_fallbacks": 0,
    "dedup_entities_merged": 12,
    "structural_entities_filtered": 2,
    "orphan_entities_filtered": 0,
    "relationships_dropped_invalid": 1,
    "relationships_dropped_capped": 5,
    "relationships_dropped_type_unmatched": 0,
    "relationships_direction_corrected": 0,
    "evidence_entities_dropped": 0,
    "evidence_relationships_dropped": 0,
    "aggregator_relationships_dropped": 0,
    "citations_skipped_no_chunk_index": 0,
    "citations_skipped_index_not_mapped": 0,
    "embedding_chunk_failures": 0,
    "embedding_dimension_mismatches": 0,
    "vision_pages_truncated": 0,
    "vision_pages_sampled_quick_mode": 0,
    "chunks_rerun_total": 0,
    "relationships_type_fuzzy_matched": 0,
    "relationships_type_fell_through": 0,
    "vector_indexed_at": "2026-05-08T11:23:14.412Z",
    "vector_indexing_status": "indexed"
  }
}
```

`vector_indexing_status` (and `vector_indexed_at`) are also surfaced flat
on the source *list* items (`SourceSummaryResponse`, `GET /api/v1/sources`)
so the list view can render the search-status badge without loading
quality metrics. On the detail response (`GET /api/v1/sources/{id}`) they
appear only inside `quality_metrics`.

## Field reference

### Loader stage

| Field | Type | Description |
|-------|------|-------------|
| `loader_encoding_used` | string? | Encoding the loader actually used to decode this file. Values include `utf-8`, `utf-8-bom`, `cp1252`, `latin-1-fallback`, `utf-8-replace`, or any encoding label charset-normalizer returns. `null` until the loader runs. |
| `loader_warnings_count` | int | Non-fatal loader hiccups the user should know about (a single bad JSONL line, an undecodable archive member, an empty worksheet). The file still loaded. |
| `loader_files_skipped` | int | Archive entries skipped due to unsupported extension, oversized content, or security validation (path traversal, absolute paths, symlinks). |
| `loader_replacement_chars_count` | int | U+FFFD replacement characters that landed in the text because the encoding detector fell back to `utf-8-replace`. A non-zero value almost always indicates an encoding mismatch in the source. |
| `loader_pdf_pages_failed` | int | Individual PDF pages whose `extract_text()` raised; the rest of the document loaded, the failed page is empty. |
| `loader_docx_paragraphs_skipped` | int | DOCX paragraphs the loader couldn't extract. |
| `loader_xlsx_rows_skipped` | int | XLSX rows the loader couldn't extract. |
| `loader_csv_rows_truncated` | int | CSV rows truncated by the configured per-file row cap. |
| `loader_html_dropped_tags` | object? | `dict[tag → count]` of HTML elements the sanitizer stripped (e.g. `<script>`, `<style>`). `null` when the HTML loader didn't run for this source. |
| `loader_pptx_shapes_skipped` | object? | `dict[shape_type → count]` of PPTX shapes the loader couldn't extract text from. `null` when the PPTX loader didn't run. |

### Normalization stage

| Field | Type | Description |
|-------|------|-------------|
| `cleaner_lines_removed` | int | Lines the OCR cleaner dropped — gibberish, standalone page numbers, repeated headers / footers. |
| `cleaner_paragraphs_deduplicated` | int | Duplicate paragraphs collapsed by the cleaner — typically multi-column OCR producing the same text twice. |
| `cleaner_chars_removed` | int | Net character delta from the text cleaner (encoding fixes, control-character stripping, whitespace collapse). |
| `cleaner_plugin_load_failures` | int | User cleaner plugins that failed to load — affected sources may have residual noise. |
| `ocr_cleaner_skipped_by_predicate` | int | Sources whose OCR cleaner was skipped because the predicate (`applies_to`) decided the text already looked clean. |

### Chunking stage

| Field | Type | Description |
|-------|------|-------------|
| `chunks_coalesced_count` | int | Chunks the chunker merged into a neighbor (coalesced) to keep all content reaching extraction. Phase 7 rename of the legacy `chunks_filtered_count` — name now reflects the merge semantics. |
| `chunker_normalize_drops` | int | Chunks dropped because normalization left them empty (e.g. all whitespace after structural-noise stripping). |
| `chunker_prestrip_lines_removed` | int | Header / footer lines stripped before chunking began. |
| `chunks_skipped_by_depth` | int | Chunks skipped because their structural depth fell outside the configured `min_depth` / `max_depth` range. |
| `standalone_chunk_failures` | int | Standalone chunks (top-level fragments outside the normal chunker traversal) that failed to chunk and were saved as-is. |
| `user_regex_timeout_hits` | int | User-defined regex matches abandoned for taking too long — the rule was skipped for that input. |

### LLM extraction stage

| Field | Type | Description |
|-------|------|-------------|
| `llm_chunks_truncated` | int | Chunks where the LLM hit its token cap and stopped mid-response. Some content was extracted; some was lost. Drives the `length` finish-reason. |
| `llm_chunks_aborted_by_loop` | int | Chunks where the streaming loop detector aborted the LLM (degenerate repetition, out-of-bounds indices, runaway entity counts). |
| `llm_chunks_timed_out` | int | Chunks abandoned after exceeding the per-call wall-clock timeout. |
| `llm_chunks_failed_permanent` | int | Chunks that exhausted their retry budget and could not be extracted. |
| `parser_lines_dropped` | int | Malformed `E\|` / `R\|` / `P\|` lines the parser couldn't make sense of (missing fields, out-of-bounds indices, invalid numerics). |
| `semantic_dedup_fallbacks` | int | Chunks that fell back to lexical deduplication because semantic similarity was unavailable. |
| `chunks_rerun_total` | int | Manual per-chunk reruns — incremented each time a user clicks **Rerun** on a chunk row in the Processing tab. |

### Post-extraction

| Field | Type | Description |
|-------|------|-------------|
| `dedup_entities_merged` | int | Entities collapsed into another entity by exact-name or semantic deduplication. Highest-confidence record wins; aliases / properties merge. |
| `structural_entities_filtered` | int | Entities representing document structure (chapters, sections, page headers) removed by the structural filter. Configured per-domain via `is_structural`. |
| `relationships_dropped_invalid` | int | Relationships pointing at non-existent entity indices. Catches LLM index-skew before it hits the graph. |
| `relationships_dropped_capped` | int | Relationships dropped by the per-entity degree cap, the same-source-type cap, or the total-ratio cap from the active filtering mode. |
| `relationships_dropped_type_unmatched` | int | Relationships whose predicate didn't match any allowed relationship type in the active domain. |
| `relationships_type_fuzzy_matched` | int | Relationships that survived the cross-chunk type-constraint check because a fuzzy tier (substring / word-overlap) matched the LLM-emitted entity type to an allowed type. A high value means LLM types are drifting from the templates — tighten the prompt or add `type_aliases`. |
| `relationships_type_fell_through` | int | Relationships that survived because balanced mode lets an unrecognized type pass without a constraint check (strict mode drops the same relationships). Companion to `relationships_dropped_type_unmatched`. |
| `relationships_direction_corrected` | int | Relationships flipped to match the canonical predicate orientation (e.g. `OWNED_BY` → `OWNS` with swapped source/target). |
| `evidence_entities_dropped` | int | Entities removed during evidence reconciliation — survivors must have a citation chain back to source chunks. |
| `evidence_relationships_dropped` | int | Relationships removed during evidence reconciliation. |
| `aggregator_relationships_dropped` | int | Relationships dropped during cross-source aggregation when the aggregator could not reconcile conflicting evidence. |

### Commit stage

| Field | Type | Description |
|-------|------|-------------|
| `orphan_entities_filtered` | int | Entities that survived deduplication but had zero relationships, dropped at commit when orphan filtering is enabled by the active filtering mode. |
| `citations_skipped_no_chunk_index` | int | Entity / relationship citations skipped because the underlying record had no chunk index to point at. |
| `citations_skipped_index_not_mapped` | int | Citations skipped because their chunk index didn't map back to a stored chunk (e.g. truncated by the chunker after extraction completed). |

### Embedding stage

| Field | Type | Description |
|-------|------|-------------|
| `embedding_chunk_failures` | int | Chunks that failed to embed and were skipped from vector search. Surfaces silently-degraded sources where some chunks aren't searchable. |
| `embedding_dimension_mismatches` | int | Chunk embeddings rejected because their dimension did not match the configured embedding model — typically a sign of a model swap mid-run. |

### Vision stage

| Field | Type | Description |
|-------|------|-------------|
| `vision_pages_truncated` | int | Vision-described pages whose description hit the `vision_max_output_tokens` budget and was cut off. The partial description is still saved — this surfaces the truncation rate. |
| `vision_pages_sampled_quick_mode` | int | Image pages skipped by Quick-mode sampling (`extraction_depth='quick'`). Stays `0` for full-depth runs and for sources with fewer image pages than the sampling cap. |

### Vector search status

| Field | Type | Description |
|-------|------|-------------|
| `vector_indexed_at` | datetime? | When the post-commit vector indexing call succeeded; `null` until then. |
| `vector_indexing_status` | string | `pending`, `indexed`, `degraded`, or `failed`. See [Search Status](../../user-guide/search-status.md). |

## Reset behavior

Every counter and the two `vector_*` fields **reset to their
post-upload defaults** when you trigger `force_re_extract`:

```bash
curl -X POST http://localhost/api/v1/sources/src_abc123/re_extract
```

Reset values:

- All 43 integer counters → `0`
- Both JSON counters (`loader_html_dropped_tags`, `loader_pptx_shapes_skipped`) → `null`
- `loader_encoding_used` → `null`
- `vector_indexed_at` → `null`
- `vector_indexing_status` → `"pending"`

This is intentional: re-extract is the moment to compare a new run
against the old one. Take a snapshot of `quality_metrics` before
calling re-extract if you want to diff against the new values.

The cached **quality grade** (`cached_quality_grade`,
`cached_avg_entity_quality`, etc.) is recomputed and persisted
separately — those fields are not in the reset set.

## See also

- [Sources API](sources.md) — full source endpoint reference
- [Pipeline flow & quality counters (user guide)](../../user-guide/data-quality.md) — when to consult counters vs. the grade
- [Search Status (user guide)](../../user-guide/search-status.md) — the four `vector_indexing_status` states
- [Filtering Modes](../filtering-modes.md) — how the mode you pick changes which counters move
