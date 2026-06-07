# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the startup schema-drift gate.

Covers the three states ``check_schema_drift`` must handle:

* No drift                  → no log, no raise
* Drift detected + strict=False → ``schema_drift_detected`` logger.error
                                  fires, function returns normally
* Drift detected + strict=True  → raises ``SchemaIntegrityError``

Drift is simulated by dropping a column from the live SQLite DB after
``upgrade_to_head`` finishes, so the live shape no longer matches the
SQLModel.metadata Alembic was just brought up to. We rely on ``structlog``'s
``capture_logs`` test helper for log assertions to avoid hand-rolling a
fake logger.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite import models as _models  # noqa: F401
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.drift import (
    _filter_known_runtime_tables,
    _is_ignored_table,
    check_schema_drift,
)
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.exceptions import SchemaIntegrityError


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """A SQLite DB upgraded to Alembic HEAD with no drift.

    Evicts the cached engine on teardown so a sibling test using the same
    tmp_path-derived URL doesn't see stale connections.
    """
    db = tmp_path / "app.db"
    upgrade_to_head(db)
    yield db
    evict_engine(db)


def _introduce_drift(db_path: Path) -> None:
    """Add a stray column to ``sources`` so the live schema has one extra.

    Picks the additive direction (ADD COLUMN) over the subtractive one
    (DROP COLUMN) because every column in ``sources`` participates in at
    least one index and SQLite refuses to drop columns referenced by
    indexes. The autogenerate diff will surface this as a ``remove_column``
    operation (i.e., "the live DB has a column that metadata doesn't").
    The exact diff direction doesn't matter — we just need ≥1 entry.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("ALTER TABLE sources ADD COLUMN _drift_test_column TEXT")
        conn.commit()
    finally:
        conn.close()


def test_no_drift_does_not_log_or_raise(fresh_db: Path) -> None:
    """Clean schema → check_schema_drift is a silent no-op."""
    with capture_logs() as cap:
        check_schema_drift(fresh_db, strict=False)

    # No error event should have been emitted in either mode.
    assert not any(
        entry.get("event") == "schema_drift_detected" for entry in cap
    ), f"expected no schema_drift_detected log, got: {cap!r}"


def test_no_drift_strict_does_not_raise(fresh_db: Path) -> None:
    """Clean schema + strict=True → still a no-op (no diff to refuse on)."""
    check_schema_drift(fresh_db, strict=True)


def test_drift_non_strict_logs_error_but_does_not_raise(
    fresh_db: Path,
) -> None:
    """Drift + strict=False → loud logger.error event, function returns."""
    _introduce_drift(fresh_db)
    # Evict the cached engine so SQLAlchemy re-reads the post-DROP schema
    # rather than serving the pre-DROP shape from its inspector cache.
    evict_engine(fresh_db)

    with capture_logs() as cap:
        check_schema_drift(fresh_db, strict=False)

    drift_events = [
        e
        for e in cap
        if e.get("event") == "schema_drift_detected"
        and e.get("log_level") == "error"
    ]
    assert drift_events, (
        f"expected one schema_drift_detected error event, got: {cap!r}"
    )
    event = drift_events[0]
    assert event.get("strict") is False
    assert isinstance(event.get("diffs"), list)
    assert event.get("diff_count", 0) >= 1


def test_drift_strict_raises_schema_integrity_error(fresh_db: Path) -> None:
    """Drift + strict=True → SchemaIntegrityError refuses boot."""
    _introduce_drift(fresh_db)
    evict_engine(fresh_db)

    with pytest.raises(SchemaIntegrityError) as excinfo:
        check_schema_drift(fresh_db, strict=True)

    err = excinfo.value
    assert err.code == "SCHEMA_INTEGRITY_ERROR"
    assert "Schema drift detected" in err.message
    # details should carry the structured diff summary for the operator
    # to inspect post-mortem.
    assert isinstance(err.details, dict)
    assert isinstance(err.details.get("diffs"), list)
    assert err.details["diffs"], "strict-mode SchemaIntegrityError must include diff summary"


def test_drift_strict_still_logs_before_raising(fresh_db: Path) -> None:
    """Strict mode must emit the structured event *before* raising.

    Operators may not see the exception traceback (the supervisor /
    systemd may swallow it) but they will always see the structured
    log entry. Pinning this so a future refactor doesn't accidentally
    swap the order and lose the event.
    """
    _introduce_drift(fresh_db)
    evict_engine(fresh_db)

    # capture_logs replaces the processor chain with an in-memory list
    # for the duration of the with-block, so we can read entries even
    # when the function under test raises.
    with capture_logs() as cap, pytest.raises(SchemaIntegrityError):
        check_schema_drift(fresh_db, strict=True)

    assert any(
        entry.get("event") == "schema_drift_detected" and entry.get("strict") is True
        for entry in cap
    ), f"expected schema_drift_detected event with strict=True, got: {cap!r}"


# ---------------------------------------------------------------------------
# Runtime-tables ignore-list — keeps sqlite-vec + FTS5 + raw-SQL auxiliary
# tables from flooding the drift log with false positives. Pure-function
# tests; no DB fixture needed.
# ---------------------------------------------------------------------------


def test_ignored_tables_real_auxiliary_tables() -> None:
    """Raw-SQL aux tables (created via CREATE TABLE IF NOT EXISTS) are ignored."""
    assert _is_ignored_table("fulltext_content")
    assert _is_ignored_table("search_metadata")
    assert _is_ignored_table("chaoscypher_upgrade_state")


def test_ignored_tables_sqlite_vec_virtual_and_shadow() -> None:
    """vec_search_* virtual tables and their automatic shadows are ignored."""
    assert _is_ignored_table("vec_search_chunks")
    assert _is_ignored_table("vec_search_nodes")
    assert _is_ignored_table("vec_search_templates")
    # Auto-created shadow tables
    for suffix in ("_chunks", "_rowids", "_vector_chunks00", "_auxiliary", "_info"):
        assert _is_ignored_table(f"vec_search_chunks{suffix}")


def test_ignored_tables_fts5_virtual_and_shadow() -> None:
    """fulltext_index FTS5 virtual table and its automatic shadows are ignored."""
    assert _is_ignored_table("fulltext_index")
    for suffix in ("_data", "_idx", "_docsize", "_config"):
        assert _is_ignored_table(f"fulltext_index{suffix}")


def test_real_table_names_pass_through() -> None:
    """Non-ignored tables (the ones we actually care about) are NOT filtered."""
    assert not _is_ignored_table("sources")
    assert not _is_ignored_table("graph_nodes")
    assert not _is_ignored_table("llm_call_metrics")
    assert not _is_ignored_table(None)
    assert not _is_ignored_table("")


def test_filter_drops_remove_table_for_shadows() -> None:
    """A `remove_table` diff for a shadow table is filtered out."""

    class _FakeTable:
        def __init__(self, name: str) -> None:
            self.name = name

    diffs: list[object] = [
        ("remove_table", _FakeTable("vec_search_chunks_chunks")),
        ("remove_table", _FakeTable("fulltext_index_data")),
        ("remove_table", _FakeTable("sources_real_drift")),  # would survive
    ]
    filtered = _filter_known_runtime_tables(diffs)
    assert len(filtered) == 1
    assert filtered[0][1].name == "sources_real_drift"


def test_filter_drops_column_ops_targeting_shadow_tables() -> None:
    """Column-level diffs against shadow tables are filtered out too."""

    class _FakeCol:
        def __init__(self, name: str) -> None:
            self.name = name

    diffs: list[object] = [
        ("add_column", None, "vec_search_chunks_info", _FakeCol("extra")),
        ("remove_column", None, "fulltext_content", _FakeCol("legacy")),
        ("add_column", None, "sources", _FakeCol("new_real_column")),
    ]
    filtered = _filter_known_runtime_tables(diffs)
    assert len(filtered) == 1
    assert filtered[0][2] == "sources"


def test_filter_drops_nested_list_of_ops_on_shadow_table() -> None:
    """Nested column-level op lists (one per table) inherit the table-name filter."""

    class _FakeCol:
        def __init__(self, name: str) -> None:
            self.name = name

    nested_shadow: list[object] = [
        ("modify_nullable", None, "vec_search_chunks_info", _FakeCol("c"), {}, True, False),
    ]
    nested_real: list[object] = [
        ("modify_nullable", None, "sources", _FakeCol("c"), {}, True, False),
    ]
    diffs: list[object] = [nested_shadow, nested_real]
    filtered = _filter_known_runtime_tables(diffs)
    assert filtered == [nested_real]
