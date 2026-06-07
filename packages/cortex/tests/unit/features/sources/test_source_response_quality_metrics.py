# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 2: ``SourceResponse.quality_metrics`` exposes drop / merge counters.

Per-stage drops, merges, and warnings live on the source row as the 18+2
counter columns added by Alembic 0021.  ``SourceResponse`` aggregates
them into a single nested ``QualityMetrics`` object so the new "Data
Quality" tab can render the full picture without iterating sibling
fields.  Mirrors the ``upload_options`` pattern from W1.
"""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.models import (
    QualityMetrics,
    SourceResponse,
)


def _minimal_row(**overrides: object) -> dict[str, object]:
    """Build a minimal source-row dict acceptable to ``SourceResponse``."""
    base: dict[str, object] = {
        "id": "src_x",
        "database_name": "default",
        "filename": "doc.txt",
        "status": SourceStatus.INDEXED,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def test_quality_metrics_assembles_from_row_columns() -> None:
    """Every counter on the row maps 1:1 onto ``QualityMetrics``."""
    indexed_at = datetime.now(UTC)
    row = _minimal_row(
        loader_encoding_used="utf-8",
        loader_warnings_count=3,
        loader_files_skipped=1,
        cleaner_lines_removed=42,
        cleaner_paragraphs_deduplicated=2,
        cleaner_chars_removed=128,
        chunks_coalesced_count=5,
        llm_chunks_truncated=1,
        llm_chunks_aborted_by_loop=2,
        parser_lines_dropped=4,
        dedup_entities_merged=7,
        structural_entities_filtered=8,
        orphan_entities_filtered=9,
        relationships_dropped_invalid=10,
        relationships_dropped_capped=11,
        citations_skipped_no_chunk_index=12,
        vector_indexed_at=indexed_at,
        vector_indexing_status="indexed",
    )

    response = SourceResponse(**row)

    assert isinstance(response.quality_metrics, QualityMetrics)
    metrics = response.quality_metrics
    assert metrics.loader_encoding_used == "utf-8"
    assert metrics.loader_warnings_count == 3
    assert metrics.loader_files_skipped == 1
    assert metrics.cleaner_lines_removed == 42
    assert metrics.cleaner_paragraphs_deduplicated == 2
    assert metrics.cleaner_chars_removed == 128
    assert metrics.chunks_coalesced_count == 5
    assert metrics.llm_chunks_truncated == 1
    assert metrics.llm_chunks_aborted_by_loop == 2
    assert metrics.parser_lines_dropped == 4
    assert metrics.dedup_entities_merged == 7
    assert metrics.structural_entities_filtered == 8
    assert metrics.orphan_entities_filtered == 9
    assert metrics.relationships_dropped_invalid == 10
    assert metrics.relationships_dropped_capped == 11
    assert metrics.citations_skipped_no_chunk_index == 12
    assert metrics.vector_indexed_at == indexed_at
    assert metrics.vector_indexing_status == "indexed"


def test_quality_metrics_defaults_when_row_omits_counters() -> None:
    """A pristine source row yields zero counters and ``pending`` status."""
    response = SourceResponse(**_minimal_row())

    assert response.quality_metrics is not None
    metrics = response.quality_metrics
    # All numeric counters default to 0.
    assert metrics.loader_warnings_count == 0
    assert metrics.loader_files_skipped == 0
    assert metrics.cleaner_lines_removed == 0
    assert metrics.cleaner_paragraphs_deduplicated == 0
    assert metrics.cleaner_chars_removed == 0
    assert metrics.chunks_coalesced_count == 0
    assert metrics.llm_chunks_truncated == 0
    assert metrics.llm_chunks_aborted_by_loop == 0
    assert metrics.parser_lines_dropped == 0
    assert metrics.dedup_entities_merged == 0
    assert metrics.structural_entities_filtered == 0
    assert metrics.orphan_entities_filtered == 0
    assert metrics.relationships_dropped_invalid == 0
    assert metrics.relationships_dropped_capped == 0
    assert metrics.citations_skipped_no_chunk_index == 0
    # Optional fields default to None / "pending".
    assert metrics.loader_encoding_used is None
    assert metrics.vector_indexed_at is None
    assert metrics.vector_indexing_status == "pending"


def test_quality_metrics_only_appear_under_quality_metrics() -> None:
    """Sibling counter fields are excluded from the serialized payload.

    ``quality_metrics`` is the sole public surface; the 18+2 row columns
    hydrate the model via ``from_attributes`` but never appear in the
    JSON output, mirroring the ``upload_options`` pattern from W1.
    """
    row = _minimal_row(
        loader_warnings_count=3,
        cleaner_lines_removed=42,
        dedup_entities_merged=7,
        vector_indexing_status="degraded",
    )

    response = SourceResponse.model_validate(row, from_attributes=True)
    dumped = response.model_dump()

    assert "quality_metrics" in dumped
    assert dumped["quality_metrics"]["loader_warnings_count"] == 3
    assert dumped["quality_metrics"]["cleaner_lines_removed"] == 42
    assert dumped["quality_metrics"]["dedup_entities_merged"] == 7
    assert dumped["quality_metrics"]["vector_indexing_status"] == "degraded"

    # The 18 counter siblings + vector_indexed_at + vector_indexing_status
    # must NOT appear at the top level.
    for hidden in (
        "loader_encoding_used",
        "loader_warnings_count",
        "loader_files_skipped",
        "cleaner_lines_removed",
        "cleaner_paragraphs_deduplicated",
        "cleaner_chars_removed",
        "chunks_coalesced_count",
        "llm_chunks_truncated",
        "llm_chunks_aborted_by_loop",
        "parser_lines_dropped",
        "dedup_entities_merged",
        "structural_entities_filtered",
        "orphan_entities_filtered",
        "relationships_dropped_invalid",
        "relationships_dropped_capped",
        "citations_skipped_no_chunk_index",
        "vector_indexed_at",
        "vector_indexing_status",
    ):
        assert hidden not in dumped, (
            f"{hidden} must only appear under quality_metrics, "
            f"not as a top-level sibling on SourceResponse."
        )
