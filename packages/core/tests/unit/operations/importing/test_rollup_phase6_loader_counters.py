# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 7 audit-remediation (2026-05-09): _rollup_phase6_loader_counters dict path.

Closes audit finding P1 #1 (rollup int() footgun).

After Task 3.4 (HTML loader emits dict) and Task 3.5 (PPTX loader emits
dict), the ``loader_html_dropped_tags`` and ``loader_pptx_shapes_skipped``
metadata values are ``dict[str, int]``, not scalars.  The rollup must merge
them per-key rather than trying ``int()`` on a dict (which raises TypeError).

Tests:
- dict path: HTML dropped-tags dicts from two docs merge into one per-tag sum.
- dict path: PPTX shapes-skipped dicts from two docs merge correctly.
- scalar path: CSV rows-truncated scalars still sum and route via
  increment_quality_counter (not update_source_columns).
- edge: empty dict values are skipped (no write).
- edge: malformed per-doc values (non-int dict entries) are skipped
  without crashing.
- edge: documents with no metadata are skipped without crashing.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.operations.importing.indexing_handler import (
    _rollup_phase6_loader_counters,
)


# ---------------------------------------------------------------------------
# Minimal fake adapter
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Captures ``increment_source_counter`` and ``update_source_columns`` calls."""

    def __init__(self) -> None:
        self.increment_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def increment_source_counter(
        self, *, source_id: str, database_name: str, column: str, n: int
    ) -> None:
        self.increment_calls.append(
            {"source_id": source_id, "database_name": database_name, "column": column, "n": n}
        )

    def update_source_columns(
        self, *, source_id: str, database_name: str, updates: dict[str, Any]
    ) -> None:
        self.update_calls.append(
            {"source_id": source_id, "database_name": database_name, "updates": updates}
        )

    def last_dict_update(self, column: str) -> dict[str, int] | None:
        """Return the ``updates`` payload of the last ``update_source_columns``
        call that contained *column*, or ``None`` if none matched.
        """
        for call_record in reversed(self.update_calls):
            if column in call_record["updates"]:
                return call_record["updates"][column]
        return None

    def increment_column_total(self, column: str) -> int:
        """Sum of all ``increment_source_counter`` calls for *column*."""
        return sum(c["n"] for c in self.increment_calls if c["column"] == column)


# ---------------------------------------------------------------------------
# Dict path: HTML dropped tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_dict_merges_html_dropped_tags() -> None:
    """Two docs with loader_html_dropped_tags dicts merge into a per-tag sum.

    Phase 7 audit-remediation (2026-05-09): this test would previously
    crash with TypeError when the rollup called int() on the dict value.
    """
    documents = [
        {"metadata": {"loader_html_dropped_tags": {"script": 2, "nav": 1}}},
        {"metadata": {"loader_html_dropped_tags": {"script": 1, "footer": 4}}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-1",
        database_name="test",
    )

    result = adapter.last_dict_update("loader_html_dropped_tags")
    assert result == {"script": 3, "nav": 1, "footer": 4}, (
        f"Expected merged per-tag dict, got {result!r}"
    )
    # Must NOT have gone through the scalar (increment_source_counter) path.
    assert adapter.increment_column_total("loader_html_dropped_tags") == 0


@pytest.mark.asyncio
async def test_rollup_dict_merges_pptx_shapes_skipped() -> None:
    """Two docs with loader_pptx_shapes_skipped dicts merge correctly.

    Phase 7 audit-remediation (2026-05-09): verifies the PPTX dict path
    alongside the HTML dict path.
    """
    documents = [
        {"metadata": {"loader_pptx_shapes_skipped": {"table": 1, "chart": 2}}},
        {"metadata": {"loader_pptx_shapes_skipped": {"table": 3, "smartart": 1}}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-2",
        database_name="test",
    )

    result = adapter.last_dict_update("loader_pptx_shapes_skipped")
    assert result == {"table": 4, "chart": 2, "smartart": 1}, (
        f"Expected merged per-shape dict, got {result!r}"
    )
    assert adapter.increment_column_total("loader_pptx_shapes_skipped") == 0


# ---------------------------------------------------------------------------
# Scalar path: CSV rows truncated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_scalar_csv_rows_truncated_still_increments() -> None:
    """CSV rows-truncated scalars still route via increment_source_counter.

    Phase 7 audit-remediation (2026-05-09): ensures the scalar path
    (DOCX/XLSX/CSV) is unaffected by the dict-path split.
    """
    documents = [
        {"metadata": {"loader_csv_rows_truncated": 3}},
        {"metadata": {"loader_csv_rows_truncated": 7}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-3",
        database_name="test",
    )

    total = adapter.increment_column_total("loader_csv_rows_truncated")
    assert total == 10, f"Expected 10 incremented, got {total!r}"
    # Must NOT have gone through update_source_columns.
    assert adapter.last_dict_update("loader_csv_rows_truncated") is None


@pytest.mark.asyncio
async def test_rollup_scalar_docx_paragraphs_skipped() -> None:
    """DOCX paragraphs-skipped scalars still sum and increment atomically."""
    documents = [
        {"metadata": {"loader_docx_paragraphs_skipped": 5}},
        {"metadata": {"loader_docx_paragraphs_skipped": 2}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-4",
        database_name="test",
    )

    total = adapter.increment_column_total("loader_docx_paragraphs_skipped")
    assert total == 7, f"Expected 7 incremented, got {total!r}"
    assert adapter.last_dict_update("loader_docx_paragraphs_skipped") is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_empty_dict_produces_no_write() -> None:
    """An all-empty-dict input for a dict counter does not call update_source_columns."""
    documents = [
        {"metadata": {"loader_html_dropped_tags": {}}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-5",
        database_name="test",
    )

    assert adapter.last_dict_update("loader_html_dropped_tags") is None
    assert adapter.update_calls == []


@pytest.mark.asyncio
async def test_rollup_malformed_dict_values_skipped() -> None:
    """Non-int dict entry values are skipped without crashing.

    Phase 7 audit-remediation (2026-05-09): defensive handling for
    loaders that emit unexpected value types inside the per-tag dict.
    """
    documents = [
        {"metadata": {"loader_html_dropped_tags": {"script": "bad", "nav": 3}}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-6",
        database_name="test",
    )

    # "bad" is skipped; "nav": 3 survives
    result = adapter.last_dict_update("loader_html_dropped_tags")
    assert result == {"nav": 3}, f"Expected only valid entries, got {result!r}"


@pytest.mark.asyncio
async def test_rollup_documents_without_metadata_are_skipped() -> None:
    """Documents missing metadata key don't crash the rollup."""
    documents: list[dict[str, Any]] = [
        {},  # no metadata key at all
        {"metadata": None},  # metadata is None
        {"metadata": {"loader_html_dropped_tags": {"div": 1}}},  # valid
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-7",
        database_name="test",
    )

    result = adapter.last_dict_update("loader_html_dropped_tags")
    assert result == {"div": 1}, f"Expected only the valid doc's entry, got {result!r}"


@pytest.mark.asyncio
async def test_rollup_mixed_scalar_and_dict_docs() -> None:
    """A mix of scalar (legacy) and dict values in the same doc list is handled.

    Scalar int values for a dict counter (e.g. stale loader output) are
    silently ignored via the isinstance(per_doc, dict) guard.
    """
    documents = [
        # Legacy scalar value for a dict counter — must be ignored, not crash.
        {"metadata": {"loader_html_dropped_tags": 5}},
        {"metadata": {"loader_html_dropped_tags": {"script": 2}}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-8",
        database_name="test",
    )

    # Only the dict doc contributes.
    result = adapter.last_dict_update("loader_html_dropped_tags")
    assert result == {"script": 2}, f"Expected only dict doc's entry, got {result!r}"


@pytest.mark.asyncio
async def test_rollup_no_counter_data_produces_no_calls() -> None:
    """Documents with no Phase 6 counter metadata produce no adapter calls."""
    documents = [
        {"metadata": {"some_other_key": 99}},
        {"metadata": {}},
    ]
    adapter = _FakeAdapter()

    await _rollup_phase6_loader_counters(
        documents=documents,
        adapter=adapter,
        source_id="src-9",
        database_name="test",
    )

    assert adapter.increment_calls == []
    assert adapter.update_calls == []
