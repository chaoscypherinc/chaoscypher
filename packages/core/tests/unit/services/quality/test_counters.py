# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Counter increments are atomic, additive, and reset on force_re_extract.

Workstream 2 (2026-05-07 import pipeline remediation): the counter helper
in ``chaoscypher_core.services.quality.counters`` is the single typed
entry point every pipeline stage uses to record drops, merges, and
warnings.  Counter writes are best-effort — failures must log and
continue, never raise — so the helper can be sprinkled into hot paths
without becoming a new failure mode.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.quality.counters import (
    QualityCounter,
    increment_quality_counter,
    reset_quality_counters,
)


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed ``SqliteAdapter`` with all tables created.

    CC040 forbids ``:memory:`` SQLite in tests; use ``tmp_path``.
    """
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


@pytest.fixture
def prepared_source_id(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> str:
    """Upload a tiny source so counter UPDATEs have a real row to target."""
    source_id = "src-counters-1"
    sqlite_adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    return source_id


class _BrokenAdapter:
    """Adapter stand-in whose increment / update methods always raise.

    The counter helper must swallow failures (log + continue), so this
    fixture verifies the contract: a hostile adapter must not propagate
    its exception out of the helper.
    """

    def increment_source_counter(
        self,
        *,
        source_id: str,
        database_name: str,
        column: str,
        n: int,
    ) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    def update_source_columns(
        self,
        *,
        source_id: str,
        database_name: str,
        updates: dict[str, Any],
    ) -> None:
        msg = "boom"
        raise RuntimeError(msg)


@pytest.fixture
def broken_adapter() -> _BrokenAdapter:
    return _BrokenAdapter()


@pytest.mark.asyncio
async def test_n_sequential_increments_sum_correctly(
    sqlite_adapter: SqliteAdapter, prepared_source_id: str
) -> None:
    """100 sequentially-awaited +1 increments produce exactly 100.

    The underlying UPDATE uses ``COALESCE(col, 0) + :n`` so the result is
    additive even if the column starts NULL.  ``asyncio.gather`` over a
    single SQLite session executes serially (one connection, no real
    concurrency), so this test verifies sequential summation only — that
    every call lands as +1, none silently lost.  Verifying behaviour
    under genuine concurrency is a separate concern that would need WAL
    journaling and multiple connections.
    """
    await asyncio.gather(
        *[
            increment_quality_counter(
                adapter=sqlite_adapter,
                source_id=prepared_source_id,
                database_name="default",
                counter=QualityCounter.LOADER_WARNINGS,
                n=1,
            )
            for _ in range(100)
        ]
    )
    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["loader_warnings_count"] == 100


@pytest.mark.asyncio
async def test_increment_failure_does_not_raise(
    broken_adapter: _BrokenAdapter,
) -> None:
    """If the underlying UPDATE fails, the helper logs and returns. No raise."""
    # ``_BrokenAdapter`` raises before touching any row, so no real
    # source needs to exist — pass a literal id and skip the upload
    # fixture entirely.
    # No assertion — the test passes by virtue of the await NOT raising.
    await increment_quality_counter(
        adapter=broken_adapter,
        source_id="src-broken-1",
        database_name="default",
        counter=QualityCounter.LOADER_WARNINGS,
        n=1,
    )


# ---------------------------------------------------------------------------
# Workstream 2 follow-up (2026-05-08): smoke tests for the per-counter wiring
# added after the initial enum/column landing. Each test exercises the
# upstream tracker (parser, filtering log, archive handler, citation skip
# loop) at unit-scale and asserts the counter drop count matches what the
# tracker observed. They guard against regressions where a tracker keeps
# its internal count but the row-level counter never increments.
# ---------------------------------------------------------------------------


def test_parse_extraction_output_records_dropped_lines_in_stats() -> None:
    """The optional ``stats`` arg captures malformed-line drops across both passes.

    Wiring guard for ``QualityCounter.PARSER_LINES_DROPPED``: the chunk
    extraction service reads ``parser_lines_dropped`` from the metrics dict
    and increments the source counter — that read returns 0 if the parser
    doesn't populate ``stats``. Verify a malformed E|/R|/P| line bumps the
    counter exactly once.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
        parse_extraction_output,
    )

    output = (
        # Valid entity line.
        "E|Alice|Person|alias1|0.9|S1|description\n"
        # Malformed entity line — too few fields.
        "E|broken\n"
        # Malformed property line — no value.
        "P|Alice|age\n"
    )
    stats: dict[str, int] = {}
    entities, _, _ = parse_extraction_output(output, stats=stats)
    assert len(entities) == 1
    # Two malformed structured lines should be recorded.
    assert stats["dropped_lines"] >= 2


def test_filtering_log_to_dict_preserves_stage_removed_counts() -> None:
    """``FilteringLog.to_dict`` keeps per-stage removed_count for downstream readers.

    Wiring guard for the dedup / cross-chunk relationship counter wirings —
    extraction_finalizer reads ``stages[*].removed_count`` straight from
    the serialized log to derive the row-level counters (DEDUP_ENTITIES_MERGED,
    RELATIONSHIPS_DROPPED_INVALID, RELATIONSHIPS_DROPPED_CAPPED). A rename
    in ``add_stage`` or a flatten in ``to_dict`` would silently zero them.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
        FilteringLog,
    )

    log = FilteringLog()
    log.add_stage("exact_entity_dedup", input_count=10, removed_count=3)
    log.add_stage("relationship_limit_enforcement", input_count=20, removed_count=5)
    log.add_stage("exact_entity_dedup", input_count=8, removed_count=2)  # merged log

    serialized = log.to_dict()
    stages = serialized["stages"]

    by_stage = {(s["stage"], s["removed_count"]) for s in stages}
    assert ("exact_entity_dedup", 3) in by_stage
    assert ("exact_entity_dedup", 2) in by_stage
    assert ("relationship_limit_enforcement", 5) in by_stage

    # The aggregation pattern the finalizer uses must yield 5 (3+2).
    dedup_total = sum(s["removed_count"] for s in stages if s["stage"] == "exact_entity_dedup")
    assert dedup_total == 5


def test_archive_generic_handler_surfaces_skipped_count_via_metadata(
    tmp_path: Path,
) -> None:
    """The generic archive handler stamps ``loader_files_skipped`` on the first doc.

    Wiring guard for ``QualityCounter.LOADER_FILES_SKIPPED``: the indexing
    handler drains this metadata field after archive load. If the handler
    stops setting it, that counter renders 0 even when files are skipped.
    """
    from chaoscypher_core.services.sources.loaders.archive.handlers.generic_handler import (
        GenericHandler,
    )
    from chaoscypher_core.settings import EngineSettings

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    # Two unsupported files (skipped) + one supported.
    (extracted / "skip1.binary").write_text("x")
    (extracted / "skip2.binary").write_text("y")
    (extracted / "real.txt").write_text(
        "This is a sample document with enough content to be loaded. " * 5
    )

    settings = EngineSettings()
    handler = GenericHandler()
    docs = handler.process(extracted, settings)

    # At least one doc loaded (the .txt). Two files were skipped — the
    # first surviving doc must record that.
    assert docs, "expected the .txt file to load"
    first_meta = docs[0].get("metadata", {})
    assert first_meta.get("loader_files_skipped", 0) >= 2


def test_reset_zeroes_all_counters(sqlite_adapter: SqliteAdapter, prepared_source_id: str) -> None:
    """After reset, every counter is 0 (or None for JSON columns) and metadata
    fields are reset to defaults.

    Phase 7 audit-remediation (2026-05-09): LOADER_HTML_DROPPED_TAGS and
    LOADER_PPTX_SHAPES_SKIPPED are JSON columns that reset to None rather
    than 0. All other counters reset to 0.
    """
    from chaoscypher_core.services.quality.counters import _RESET_DEFAULTS

    # JSON-typed counters that reset to None instead of 0.
    json_counter_values = {
        QualityCounter.LOADER_HTML_DROPPED_TAGS,
        QualityCounter.LOADER_PPTX_SHAPES_SKIPPED,
    }

    # Seed every counter with a non-zero value so the assertion-loop
    # below catches a future change that drops a column from
    # ``_RESET_DEFAULTS``.
    seed_updates: dict[str, Any] = {
        col.value: ({"a": 99} if col in json_counter_values else 99) for col in QualityCounter
    }
    seed_updates.update(
        {
            "loader_encoding_used": "cp1252",
            "vector_indexed_at": datetime.now(UTC),
            "vector_indexing_status": "indexed",
        }
    )
    sqlite_adapter.update_source_columns(
        source_id=prepared_source_id,
        database_name="default",
        updates=seed_updates,
    )

    reset_quality_counters(sqlite_adapter, prepared_source_id, "default")

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    for counter in QualityCounter:
        expected = _RESET_DEFAULTS.get(counter.value, 0)
        assert row[counter.value] == expected, (
            f"counter {counter.value} not reset: expected {expected!r}, got {row[counter.value]!r}"
        )
    assert row["loader_encoding_used"] is None
    assert row["vector_indexed_at"] is None
    assert row["vector_indexing_status"] == "pending"


def test_reset_quality_counters_clears_llm_metrics(
    sqlite_adapter: SqliteAdapter, prepared_source_id: str
) -> None:
    """force_re_extract must reset llm_* cumulative metrics — they drifted
    across re-extracts pre-2026-05-08 because they weren't in _RESET_DEFAULTS.

    Note: vision_pages_failed / vision_failed_pages / loader_pdf_failed_pages
    were removed by migration 0034 (2026-05-13 PR 2); the equivalent
    observability now comes from vision_page_descriptions filtered by status.
    """
    # Seed non-zero values directly via the bulk update helper.
    sqlite_adapter.update_source_columns(
        source_id=prepared_source_id,
        database_name="default",
        updates={
            "llm_total_calls": 42,
            "llm_total_input_tokens": 5000,
            "llm_total_output_tokens": 2500,
            "llm_estimated_cost_usd": 0.34,
        },
    )

    reset_quality_counters(sqlite_adapter, prepared_source_id, "default")

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["llm_total_calls"] == 0
    assert row["llm_total_input_tokens"] == 0
    assert row["llm_total_output_tokens"] == 0
    assert row["llm_estimated_cost_usd"] is None


def test_phase2_counters_enum_values_present() -> None:
    """Phase 2 added 16 new QualityCounter members. Catch accidental
    rename / removal during refactors.
    """
    expected = {
        "EVIDENCE_ENTITIES_DROPPED",
        "EVIDENCE_RELATIONSHIPS_DROPPED",
        "AGGREGATOR_RELATIONSHIPS_DROPPED",
        "LLM_CHUNKS_TIMED_OUT",
        "LLM_CHUNKS_FAILED_PERMANENT",
        "STANDALONE_CHUNK_FAILURES",
        "SEMANTIC_DEDUP_FALLBACKS",
        "RELATIONSHIPS_DIRECTION_CORRECTED",
        "RELATIONSHIPS_DROPPED_TYPE_UNMATCHED",
        "USER_REGEX_TIMEOUT_HITS",
        "OCR_CLEANER_SKIPPED_BY_PREDICATE",
        "CHUNKER_NORMALIZE_DROPS",
        "CHUNKER_PRESTRIP_LINES_REMOVED",
        "CHUNKS_SKIPPED_BY_DEPTH",
        "LOADER_REPLACEMENT_CHARS_COUNT",
        "CITATIONS_SKIPPED_INDEX_NOT_MAPPED",
    }
    actual = {member.name for member in QualityCounter}
    missing = expected - actual
    assert not missing, f"Missing Phase 2 enum members: {missing}"


def test_phase5b_loader_pdf_pages_failed_enum_present() -> None:
    """Phase 5b (2026-05-08): LOADER_PDF_PAGES_FAILED must be in the enum.

    The PDF loader writes ``loader_pdf_pages_failed`` to document metadata;
    the indexing handler reads it and increments this counter on the source
    row.  The enum value must survive refactors.
    """
    assert "LOADER_PDF_PAGES_FAILED" in {m.name for m in QualityCounter}
    assert QualityCounter.LOADER_PDF_PAGES_FAILED.value == "loader_pdf_pages_failed"


def test_phase5b_image_only_pdf_warning_routed_via_loader_warnings_counter(
    sqlite_adapter: SqliteAdapter, prepared_source_id: str
) -> None:
    """Image-only PDFs append to ``loader_warnings``; the LOADER_WARNINGS counter
    must be incrementable and must be reset by ``reset_quality_counters``.

    This is a minimal end-to-end check: the PDF loader sets
    ``metadata["loader_warnings"]`` when all pages produce empty text;
    the indexing handler sums those entries and calls
    ``increment_quality_counter(..., counter=QualityCounter.LOADER_WARNINGS, n=N)``.
    The counter must therefore be present in both the enum and _RESET_DEFAULTS.
    """
    import asyncio

    # Increment LOADER_WARNINGS to simulate what the indexing handler does
    # after detecting an image-only PDF (1 warning entry).
    asyncio.run(
        increment_quality_counter(
            adapter=sqlite_adapter,
            source_id=prepared_source_id,
            database_name="default",
            counter=QualityCounter.LOADER_WARNINGS,
            n=1,
        )
    )

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["loader_warnings_count"] == 1, "LOADER_WARNINGS increment failed"

    # After reset, the counter must return to 0 (re-extract starts clean).
    reset_quality_counters(sqlite_adapter, prepared_source_id, "default")

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["loader_warnings_count"] == 0, "LOADER_WARNINGS not reset"


def test_phase1_deferral_llm_columns_in_reset_defaults(
    sqlite_adapter: SqliteAdapter, prepared_source_id: str
) -> None:
    """Phase 1 deferral: 9 more llm_* cumulative columns drift across
    re-extracts the same way the four chosen ones did.
    """
    seed = {
        "llm_successful_calls": 5,
        "llm_failed_calls": 2,
        "llm_retry_calls": 3,
        "llm_first_try_successes": 4,
        "llm_retry_successes": 1,
        "llm_permanent_failures": 1,
        "llm_wasted_tokens": 1500,
        "llm_avg_call_duration_ms": 250,
        "llm_total_duration_ms": 12500,
    }
    sqlite_adapter.update_source_columns(
        source_id=prepared_source_id,
        database_name="default",
        updates=seed,
    )
    reset_quality_counters(sqlite_adapter, prepared_source_id, "default")
    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    for column in seed:
        assert row[column] == 0, f"{column} not reset to 0"


def test_reset_defaults_includes_llm_error_counts_and_model() -> None:
    """Phase 1 deferral cleanup: every cumulative llm_* SourceRow column
    must be in _RESET_DEFAULTS so force_re_extract returns the row to a
    pristine post-upload state.
    """
    from chaoscypher_core.services.quality.counters import _RESET_DEFAULTS

    assert "llm_error_counts" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["llm_error_counts"] == {}
    assert "llm_model" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["llm_model"] is None


def test_phase2_counter_columns_in_reset_defaults(
    sqlite_adapter: SqliteAdapter, prepared_source_id: str
) -> None:
    """Wiring guard: every Phase 2 counter column is drained on re-extract."""
    phase2_columns = [
        m.value
        for m in QualityCounter
        if m.name
        in {
            "EVIDENCE_ENTITIES_DROPPED",
            "EVIDENCE_RELATIONSHIPS_DROPPED",
            "AGGREGATOR_RELATIONSHIPS_DROPPED",
            "LLM_CHUNKS_TIMED_OUT",
            "LLM_CHUNKS_FAILED_PERMANENT",
            "STANDALONE_CHUNK_FAILURES",
            "SEMANTIC_DEDUP_FALLBACKS",
            "RELATIONSHIPS_DIRECTION_CORRECTED",
            "RELATIONSHIPS_DROPPED_TYPE_UNMATCHED",
            "USER_REGEX_TIMEOUT_HITS",
            "OCR_CLEANER_SKIPPED_BY_PREDICATE",
            "CHUNKER_NORMALIZE_DROPS",
            "CHUNKER_PRESTRIP_LINES_REMOVED",
            "CHUNKS_SKIPPED_BY_DEPTH",
            "LOADER_REPLACEMENT_CHARS_COUNT",
            "CITATIONS_SKIPPED_INDEX_NOT_MAPPED",
        }
    ]
    seed = dict.fromkeys(phase2_columns, 1)
    sqlite_adapter.update_source_columns(
        source_id=prepared_source_id,
        database_name="default",
        updates=seed,
    )
    reset_quality_counters(sqlite_adapter, prepared_source_id, "default")
    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    for column in phase2_columns:
        assert row[column] == 0, f"{column} not reset to 0"


# ---------------------------------------------------------------------------
# Phase 7 audit-remediation (2026-05-09): wiring guard tests for
# column renames + type changes + new counters in migration 0029.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counter", "column"),
    [
        (QualityCounter.CHUNKS_COALESCED, "chunks_coalesced_count"),
        (QualityCounter.LOADER_HTML_DROPPED_TAGS, "loader_html_dropped_tags"),
        (QualityCounter.LOADER_PPTX_SHAPES_SKIPPED, "loader_pptx_shapes_skipped"),
        (QualityCounter.EMBEDDING_CHUNK_FAILURES, "embedding_chunk_failures"),
        (QualityCounter.EMBEDDING_DIMENSION_MISMATCHES, "embedding_dimension_mismatches"),
    ],
)
def test_phase7_counter_columns_match(counter: QualityCounter, column: str) -> None:
    """Each Phase 7 enum member must map to the exact SQL column name."""
    assert counter.value == column


def test_phase7_columns_in_reset_defaults() -> None:
    """Phase 7 renamed/new columns must be present in _RESET_DEFAULTS with
    correct reset values; dropped columns must be absent.
    """
    from chaoscypher_core.services.quality.counters import _RESET_DEFAULTS

    # Renamed: chunks_coalesced_count replaces chunks_filtered_count.
    assert "chunks_coalesced_count" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["chunks_coalesced_count"] == 0
    assert "chunks_filtered_count" not in _RESET_DEFAULTS, "old name must be removed"

    # Renamed + JSON: loader_html_dropped_tags replaces loader_html_dropped_tags_count.
    assert "loader_html_dropped_tags" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["loader_html_dropped_tags"] is None  # JSON column; nullable
    assert "loader_html_dropped_tags_count" not in _RESET_DEFAULTS, "old name must be removed"

    # Retyped to JSON: loader_pptx_shapes_skipped now resets to None (was 0).
    assert "loader_pptx_shapes_skipped" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["loader_pptx_shapes_skipped"] is None  # was 0 pre-Phase 7

    # New embedding counters.
    assert "embedding_chunk_failures" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["embedding_chunk_failures"] == 0
    assert "embedding_dimension_mismatches" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["embedding_dimension_mismatches"] == 0

    # chunks_completed_count is a stat (not a QualityCounter) but must be in _RESET_DEFAULTS.
    assert "chunks_completed_count" in _RESET_DEFAULTS
    assert _RESET_DEFAULTS["chunks_completed_count"] == 0
    assert "CHUNKS_COMPLETED" not in {m.name for m in QualityCounter}, (
        "chunks_completed_count is a stat, not a counter — must not appear in QualityCounter"
    )


def test_phase7_old_enum_members_removed() -> None:
    """CHUNKS_FILTERED and LOADER_HTML_DROPPED_TAGS_COUNT must no longer exist
    in QualityCounter — they were renamed in Phase 7.
    """
    member_names = {m.name for m in QualityCounter}
    assert "CHUNKS_FILTERED" not in member_names, (
        "CHUNKS_FILTERED was renamed to CHUNKS_COALESCED in Phase 7"
    )
    assert "LOADER_HTML_DROPPED_TAGS_COUNT" not in member_names, (
        "LOADER_HTML_DROPPED_TAGS_COUNT was renamed to LOADER_HTML_DROPPED_TAGS in Phase 7"
    )


# ---------------------------------------------------------------------------
# Vision pipeline PR 2 (2026-05-13): VISION_PAGES_TRUNCATED counter.
# ---------------------------------------------------------------------------


def test_vision_pages_truncated_counter_exists() -> None:
    """VISION_PAGES_TRUNCATED surfaces vision-LLM truncation events.

    Wiring guard for the 16th QualityCounter member added in PR 2. The
    per-page vision handler increments this counter when
    ``finish_reason == 'length'`` — i.e. ``vision_max_output_tokens``
    fired and the page description was cut short. Partial content is
    still saved (TRUNCATED counts as completed); the counter surfaces
    the truncation rate in the Data Quality UI tab.
    """
    assert QualityCounter.VISION_PAGES_TRUNCATED.value == "vision_pages_truncated"
