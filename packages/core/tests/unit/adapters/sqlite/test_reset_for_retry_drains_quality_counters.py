# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 (2026-05-08): reset_for_retry zeroes every quality counter.

ERROR-state retry must drain quality counters so a re-run starts from a
clean slate, matching the behaviour of ``reset_for_re_extraction`` which
was fixed in Phase 1 Task 3.  The two paths cover all status transitions
that restart the extraction pipeline:

* ``reset_for_re_extraction`` — COMMITTED → INDEXED (force re-extract)
* ``reset_for_retry`` — ERROR → any (manual retry from error state)

This test exercises the storage-layer primitive directly (no graph
repository needed) to keep it fast and focused.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed adapter with the full schema applied."""
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
def errored_source_id(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> str:
    """A source row in the ERROR state, ready to be retried."""
    source_id = "src-retry-counters-1"
    sqlite_adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="x.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    sqlite_adapter.update_file(
        source_id,
        "default",
        {
            "status": SourceStatus.ERROR,
            "error_message": "Extraction failed",
            "error_stage": "extraction",
        },
    )
    return source_id


def test_reset_for_retry_drains_quality_counters(
    sqlite_adapter: SqliteAdapter, errored_source_id: str
) -> None:
    """ERROR-state retry path must reset quality counters, matching
    reset_for_re_extraction's behavior. Phase 1 fixed the parallel gap
    in cortex's reextract_source else branch; this covers the storage-
    layer ERROR retry primitive.
    """
    sqlite_adapter.update_source_columns(
        source_id=errored_source_id,
        database_name="default",
        updates={
            "loader_warnings_count": 5,
            "llm_chunks_truncated": 2,
            "evidence_entities_dropped": 3,  # Phase 2 counter
        },
    )

    sqlite_adapter.reset_for_retry(errored_source_id, "default", new_status=SourceStatus.PENDING)

    row = sqlite_adapter.get_source(errored_source_id, "default")
    assert row is not None
    assert row["loader_warnings_count"] == 0
    assert row["llm_chunks_truncated"] == 0
    assert row["evidence_entities_dropped"] == 0


def test_reset_for_retry_drains_all_phase2_counters(
    sqlite_adapter: SqliteAdapter, errored_source_id: str
) -> None:
    """All Phase 2 counters are zeroed on ERROR → PENDING retry."""
    from chaoscypher_core.services.quality.counters import QualityCounter

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
    seed = dict.fromkeys(phase2_columns, 7)
    sqlite_adapter.update_source_columns(
        source_id=errored_source_id,
        database_name="default",
        updates=seed,
    )

    sqlite_adapter.reset_for_retry(errored_source_id, "default", new_status=SourceStatus.PENDING)

    row = sqlite_adapter.get_source(errored_source_id, "default")
    assert row is not None
    for column in phase2_columns:
        assert row[column] == 0, f"Phase 2 counter {column} not reset to 0"


def test_reset_for_retry_no_op_when_not_error(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """reset_for_retry is a no-op when the source is NOT in ERROR state.
    Quality counters must remain untouched in this case.
    """
    source_id = "src-retry-noop-1"
    sqlite_adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="y.txt",
        file_content=b"y",
        staging_dir=str(tmp_path),
    )
    # Leave source in the default PENDING state (not ERROR).
    sqlite_adapter.update_source_columns(
        source_id=source_id,
        database_name="default",
        updates={"loader_warnings_count": 4},
    )

    # reset_for_retry guards on status == "error"; PENDING source is a no-op.
    sqlite_adapter.reset_for_retry(source_id, "default", new_status=SourceStatus.PENDING)

    row = sqlite_adapter.get_source(source_id, "default")
    assert row is not None
    # Counter untouched because the guard prevented the reset.
    assert row["loader_warnings_count"] == 4
