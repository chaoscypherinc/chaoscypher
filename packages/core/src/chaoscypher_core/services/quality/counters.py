# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Typed increment helper for source quality counters.

Counter writes are best-effort: if the underlying UPDATE fails the
helper logs the failure and returns silently.  Counter visibility is
non-functional — never block the pipeline on it.

The helpers expect the storage adapter to expose two methods, both
implemented on ``SourceLifecycleMixin``:

- ``increment_source_counter(*, source_id, database_name, column, n)``
  performs an atomic ``COALESCE(col, 0) + :n`` UPDATE on a single
  allowlisted column.
- ``update_source_columns(*, source_id, database_name, updates)``
  bulk-sets a dict of columns on the row in one statement.

Counter columns and the ``vector_indexed_at`` / ``vector_indexing_status``
fields were added by the same migration (0021_*).  Every per-stage
increment call-site across the pipeline goes through this module — it is
the single typed entry point for counter writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

import structlog


logger = structlog.get_logger(__name__)


class QualityCounter(StrEnum):
    """Allowlisted counter column names, one per pipeline drop / merge site.

    The string value is the SQL column on ``sources``.  Keeping the enum
    in lockstep with the columns means a typo in a stage's ``increment``
    call surfaces as a static type error rather than a silent miss.
    """

    LOADER_WARNINGS = "loader_warnings_count"
    LOADER_FILES_SKIPPED = "loader_files_skipped"
    CLEANER_LINES_REMOVED = "cleaner_lines_removed"
    CLEANER_PARAGRAPHS_DEDUPLICATED = "cleaner_paragraphs_deduplicated"
    CLEANER_CHARS_REMOVED = "cleaner_chars_removed"
    # Phase 7 audit-remediation (2026-05-09): renamed from CHUNKS_FILTERED.
    # The chunker COALESCEs sub-threshold chunks into a neighbor rather than
    # dropping them — this counter records MERGE EVENTS, not lost content.
    # See ``ChunkingService._create_small_chunks``.
    CHUNKS_COALESCED = "chunks_coalesced_count"
    LLM_CHUNKS_TRUNCATED = "llm_chunks_truncated"
    LLM_CHUNKS_ABORTED_BY_LOOP = "llm_chunks_aborted_by_loop"
    PARSER_LINES_DROPPED = "parser_lines_dropped"
    DEDUP_ENTITIES_MERGED = "dedup_entities_merged"
    STRUCTURAL_ENTITIES_FILTERED = "structural_entities_filtered"
    ORPHAN_ENTITIES_FILTERED = "orphan_entities_filtered"
    RELATIONSHIPS_DROPPED_INVALID = "relationships_dropped_invalid"
    RELATIONSHIPS_DROPPED_CAPPED = "relationships_dropped_capped"
    CITATIONS_SKIPPED_NO_CHUNK_INDEX = "citations_skipped_no_chunk_index"
    # Phase 2 (2026-05-08): observability completeness
    EVIDENCE_ENTITIES_DROPPED = "evidence_entities_dropped"
    EVIDENCE_RELATIONSHIPS_DROPPED = "evidence_relationships_dropped"
    AGGREGATOR_RELATIONSHIPS_DROPPED = "aggregator_relationships_dropped"
    LLM_CHUNKS_TIMED_OUT = "llm_chunks_timed_out"
    LLM_CHUNKS_FAILED_PERMANENT = "llm_chunks_failed_permanent"
    STANDALONE_CHUNK_FAILURES = "standalone_chunk_failures"
    SEMANTIC_DEDUP_FALLBACKS = "semantic_dedup_fallbacks"
    RELATIONSHIPS_DIRECTION_CORRECTED = "relationships_direction_corrected"
    RELATIONSHIPS_DROPPED_TYPE_UNMATCHED = "relationships_dropped_type_unmatched"
    USER_REGEX_TIMEOUT_HITS = "user_regex_timeout_hits"
    OCR_CLEANER_SKIPPED_BY_PREDICATE = "ocr_cleaner_skipped_by_predicate"
    CHUNKER_NORMALIZE_DROPS = "chunker_normalize_drops"
    CHUNKER_PRESTRIP_LINES_REMOVED = "chunker_prestrip_lines_removed"
    CHUNKS_SKIPPED_BY_DEPTH = "chunks_skipped_by_depth"
    LOADER_REPLACEMENT_CHARS_COUNT = "loader_replacement_chars_count"
    CITATIONS_SKIPPED_INDEX_NOT_MAPPED = "citations_skipped_index_not_mapped"
    # Phase 5b (2026-05-08): per-page failure tracking for PDF loader
    LOADER_PDF_PAGES_FAILED = "loader_pdf_pages_failed"
    # Phase 6 (2026-05-08): loader observability completeness
    # Phase 7 audit-remediation (2026-05-09): JSON-shaped, renamed from LOADER_HTML_DROPPED_TAGS_COUNT.
    LOADER_HTML_DROPPED_TAGS = "loader_html_dropped_tags"
    LOADER_DOCX_PARAGRAPHS_SKIPPED = "loader_docx_paragraphs_skipped"
    LOADER_XLSX_ROWS_SKIPPED = "loader_xlsx_rows_skipped"
    LOADER_PPTX_SHAPES_SKIPPED = "loader_pptx_shapes_skipped"  # name unchanged; retyped to JSON
    LOADER_CSV_ROWS_TRUNCATED = "loader_csv_rows_truncated"
    CLEANER_PLUGIN_LOAD_FAILURES = "cleaner_plugin_load_failures"
    # Phase 7 audit-remediation (2026-05-09): new embedding counters.
    EMBEDDING_CHUNK_FAILURES = "embedding_chunk_failures"
    EMBEDDING_DIMENSION_MISMATCHES = "embedding_dimension_mismatches"
    # Vision pipeline (2026-05-13 PR 2): incremented by the per-page vision
    # handler when finish_reason == 'length' — i.e. vision_max_output_tokens
    # fired and the description was truncated to the budget. The partial
    # content is still saved (TRUNCATED counts as completed); this counter
    # surfaces the truncation rate in the Data Quality UI tab.
    VISION_PAGES_TRUNCATED = "vision_pages_truncated"
    # Vision sampling (Wave 4-5, 2026-05-23): incremented by the indexing
    # handler when ``extraction_depth='quick'`` causes the work-queue builder
    # to skip pages — increment value is ``total_image_pages - sampled``.
    # Surfaces the sampled-vs-total split in the Processing tab vision-detail
    # tile so a 12-of-400 Quick run reads as "Quick mode: 388 skipped",
    # not a partial vision failure. Stays at 0 for ``extraction_depth='full'``
    # and for sources with fewer image pages than the configured cap.
    VISION_PAGES_SAMPLED_QUICK_MODE = "vision_pages_sampled_quick_mode"
    # Per-chunk rerun feature (2026-05-15): incremented when the user
    # clicks Rerun on a chunk row. Surfaces total manual reruns per
    # source in the Processing tab.
    CHUNKS_RERUN_TOTAL = "chunks_rerun_total"
    # Audit of `_fuzzy_type_match` (2026-05-20): surface rescue rates so
    # the population sweep can decide whether the substring/word-overlap
    # tiers are useful safety nets or are hiding type drift. Companion
    # to RELATIONSHIPS_DROPPED_TYPE_UNMATCHED — together they describe
    # every outcome of the cross-chunk type-constraint check.
    #   _FUZZY_MATCHED — relationship survived because tier 2 or 3 of
    #   _fuzzy_type_match matched the LLM-emitted entity type to an
    #   allowed type. Symptom: high value = LLM types drifting from
    #   the templates; either tighten the prompt or add explicit
    #   type_aliases.
    #   _FELL_THROUGH — relationship survived because balanced mode
    #   let an unrecognized type pass without a constraint check
    #   (strict mode drops the same relationships). Symptom: high
    #   value on a balanced-mode source = same problem as fuzzy
    #   match, just routed differently.
    RELATIONSHIPS_TYPE_FUZZY_MATCHED = "relationships_type_fuzzy_matched"
    RELATIONSHIPS_TYPE_FELL_THROUGH = "relationships_type_fell_through"


# All counter columns + ``loader_encoding_used`` + the two vector-search
# fields, returned to their pristine post-upload state by
# ``reset_quality_counters``.  Exposed here so the per-stage workstreams
# can verify symmetry between "what we set" and "what we reset".
_RESET_DEFAULTS: dict[str, Any] = {
    "loader_encoding_used": None,
    "loader_warnings_count": 0,
    "loader_files_skipped": 0,
    "cleaner_lines_removed": 0,
    "cleaner_paragraphs_deduplicated": 0,
    "cleaner_chars_removed": 0,
    # Phase 7 audit-remediation (2026-05-09): renamed (was chunks_filtered_count).
    "chunks_coalesced_count": 0,
    "llm_chunks_truncated": 0,
    "llm_chunks_aborted_by_loop": 0,
    "parser_lines_dropped": 0,
    "dedup_entities_merged": 0,
    "structural_entities_filtered": 0,
    "orphan_entities_filtered": 0,
    "relationships_dropped_invalid": 0,
    "relationships_dropped_capped": 0,
    "citations_skipped_no_chunk_index": 0,
    # Phase 2 (2026-05-08): observability completeness.
    "evidence_entities_dropped": 0,
    "evidence_relationships_dropped": 0,
    "aggregator_relationships_dropped": 0,
    "llm_chunks_timed_out": 0,
    "llm_chunks_failed_permanent": 0,
    "standalone_chunk_failures": 0,
    "semantic_dedup_fallbacks": 0,
    "relationships_direction_corrected": 0,
    "relationships_dropped_type_unmatched": 0,
    "user_regex_timeout_hits": 0,
    "ocr_cleaner_skipped_by_predicate": 0,
    "chunker_normalize_drops": 0,
    "chunker_prestrip_lines_removed": 0,
    "chunks_skipped_by_depth": 0,
    "loader_replacement_chars_count": 0,
    "citations_skipped_index_not_mapped": 0,
    # Phase 5b (2026-05-08): per-page failure tracking for PDF loader
    "loader_pdf_pages_failed": 0,
    # Phase 6 (2026-05-08): loader observability completeness
    # Phase 7 audit-remediation (2026-05-09): renamed + JSON-shaped (was loader_html_dropped_tags_count: 0).
    "loader_html_dropped_tags": None,  # JSON column; nullable
    "loader_docx_paragraphs_skipped": 0,
    "loader_xlsx_rows_skipped": 0,
    # Phase 7 audit-remediation (2026-05-09): retyped to JSON; reset to None (was 0).
    "loader_pptx_shapes_skipped": None,  # JSON column; nullable
    "loader_csv_rows_truncated": 0,
    "cleaner_plugin_load_failures": 0,
    # Phase 7 audit-remediation (2026-05-09): two cumulative llm_* columns
    # missed by the original Phase 1 deferral sweep. With these added, every
    # cumulative llm_* SourceRow column is reset on force_re_extract.
    "llm_error_counts": {},
    "llm_model": None,
    # Phase 7 audit-remediation (2026-05-09): new embedding counters.
    "embedding_chunk_failures": 0,
    "embedding_dimension_mismatches": 0,
    # Vision pipeline (2026-05-13 PR 2): truncation counter.
    "vision_pages_truncated": 0,
    # Vision sampling (Wave 4-5, 2026-05-23): pages skipped by Quick mode.
    "vision_pages_sampled_quick_mode": 0,
    # Per-chunk rerun feature (2026-05-15).
    "chunks_rerun_total": 0,
    # _fuzzy_type_match audit (2026-05-20): rescue-rate counters.
    "relationships_type_fuzzy_matched": 0,
    "relationships_type_fell_through": 0,
    # Phase 7 audit-remediation (2026-05-09): stat column (not a QualityCounter).
    "chunks_completed_count": 0,
    # Phase 1 deferral (2026-05-08): the other 9 cumulative llm_* metrics also drift.
    "llm_successful_calls": 0,
    "llm_failed_calls": 0,
    "llm_retry_calls": 0,
    "llm_first_try_successes": 0,
    "llm_retry_successes": 0,
    "llm_permanent_failures": 0,
    "llm_wasted_tokens": 0,
    "llm_avg_call_duration_ms": 0,
    "llm_total_duration_ms": 0,
    "llm_total_calls": 0,
    "llm_total_input_tokens": 0,
    "llm_total_output_tokens": 0,
    "llm_estimated_cost_usd": None,
    "vector_indexed_at": None,
    "vector_indexing_status": "pending",
}


class _SupportsIncrement(Protocol):
    """Subset of the storage-adapter surface this module relies on."""

    def increment_source_counter(
        self, *, source_id: str, database_name: str, column: str, n: int
    ) -> None:
        """Atomically add ``n`` to a counter column on the source row."""
        ...

    def update_source_columns(
        self, *, source_id: str, database_name: str, updates: dict[str, Any]
    ) -> None:
        """Set the given columns on the source row in a single UPDATE."""
        ...


async def increment_quality_counter(
    *,
    adapter: _SupportsIncrement,
    source_id: str,
    database_name: str,
    counter: QualityCounter,
    n: int = 1,
) -> None:
    """Best-effort atomic increment.

    The underlying UPDATE is synchronous — wrapping it in ``async def``
    is purely a contract decision: every pipeline-stage call site lives
    in async code, so making the helper async lets it be ``await``ed
    inline without a ``run_in_executor`` dance.
    Failures are logged at WARNING and swallowed.
    """
    try:
        adapter.increment_source_counter(
            source_id=source_id,
            database_name=database_name,
            column=counter.value,
            n=n,
        )
    except Exception:
        logger.warning(
            "quality_counter_increment_failed",
            source_id=source_id,
            counter=counter.value,
            n=n,
            exc_info=True,
        )


def set_loader_encoding(
    *,
    adapter: _SupportsIncrement,
    source_id: str,
    database_name: str,
    encoding: str,
) -> None:
    """Record the encoding the loader actually used for this source.

    Best-effort: if the UPDATE fails, log and return.  Counter visibility
    is observability, not control flow.
    """
    try:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates={"loader_encoding_used": encoding},
        )
    except Exception:
        logger.warning(
            "loader_encoding_set_failed",
            source_id=source_id,
            encoding=encoding,
            exc_info=True,
        )


def reset_quality_counters(adapter: _SupportsIncrement, source_id: str, database_name: str) -> None:
    """Zero every counter and clear vector-search status to ``pending``.

    Called from ``reset_for_re_extraction`` (the row-level primitive
    backing ``services.sources.management.re_extraction.force_re_extract``).
    Synchronous because the call sites are sync — keeping it sync avoids
    plumbing ``await`` through a transaction context manager.
    """
    try:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates=dict(_RESET_DEFAULTS),
        )
    except Exception:
        logger.warning(
            "quality_counters_reset_failed",
            source_id=source_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Workstream 10 — vector-search status transitions.
#
# Four mutually-exclusive states live on ``SourceRow.vector_indexing_status``:
#
#   * ``pending``   — post-upload default; the commit pipeline confirms it
#                     when entering the post-transaction indexing phase.
#   * ``indexed``   — both node and chunk vector writes succeeded; sets
#                     ``vector_indexed_at`` on transition.
#   * ``degraded``  — at least one indexing call raised; the commit
#                     enqueued a row in ``pending_search_index`` for the
#                     orphan-sweep worker to retry.
#   * ``failed``    — the sweep worker exhausted its retry budget for this
#                     source; the row is removed from the retry queue and
#                     the operator is shown the "Search failed" badge.
#
# All four helpers are best-effort: a failed status write logs and returns
# but never propagates. Status visibility is observability, not control flow.
# ---------------------------------------------------------------------------


def mark_search_indexing_pending(
    *,
    adapter: _SupportsIncrement,
    source_id: str,
    database_name: str,
) -> None:
    """Confirm ``pending`` at the start of post-transaction indexing.

    The default column value is already ``pending``, but explicitly
    writing it covers the re-commit path (where the row may carry a
    stale ``indexed`` / ``failed`` from a prior attempt).
    """
    try:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates={"vector_indexing_status": "pending"},
        )
    except Exception:
        logger.warning(
            "search_indexing_status_pending_failed",
            source_id=source_id,
            exc_info=True,
        )


def mark_search_indexing_indexed(
    *,
    adapter: _SupportsIncrement,
    source_id: str,
    database_name: str,
) -> None:
    """Mark a source as ``indexed`` and stamp ``vector_indexed_at``."""
    try:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates={
                "vector_indexing_status": "indexed",
                "vector_indexed_at": datetime.now(UTC),
            },
        )
    except Exception:
        logger.warning(
            "search_indexing_status_indexed_failed",
            source_id=source_id,
            exc_info=True,
        )


def mark_search_indexing_degraded(
    *,
    adapter: _SupportsIncrement,
    source_id: str,
    database_name: str,
) -> None:
    """Mark a source as ``degraded`` after a retry-queued indexing failure."""
    try:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates={"vector_indexing_status": "degraded"},
        )
    except Exception:
        logger.warning(
            "search_indexing_status_degraded_failed",
            source_id=source_id,
            exc_info=True,
        )


def mark_search_indexing_failed(
    *,
    adapter: _SupportsIncrement,
    source_id: str,
    database_name: str,
) -> None:
    """Mark a source as ``failed`` once retries are exhausted.

    Called by the search-sweep worker when a ``pending_search_index``
    entry crosses ``max_attempts``. The pending row is removed by the
    sweep itself; this helper just transitions the source row so the
    UI can surface the "Search failed" badge and the operator can act.
    """
    try:
        adapter.update_source_columns(
            source_id=source_id,
            database_name=database_name,
            updates={"vector_indexing_status": "failed"},
        )
    except Exception:
        logger.warning(
            "search_indexing_status_failed_failed",
            source_id=source_id,
            exc_info=True,
        )


__all__ = [
    "QualityCounter",
    "increment_quality_counter",
    "mark_search_indexing_degraded",
    "mark_search_indexing_failed",
    "mark_search_indexing_indexed",
    "mark_search_indexing_pending",
    "reset_quality_counters",
    "set_loader_encoding",
]
