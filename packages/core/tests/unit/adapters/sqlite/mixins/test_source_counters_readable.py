# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality counters must be readable, not only writable.

Regression for 2026-09-22: ``increment_source_counter`` wrote correctly, but
nothing could read the columns back. ``get_file`` uses a narrow ``load_only``
projection that omits them, so every read returned ``None`` - indistinguishable
from a clean run - and a truncated extraction scored as if complete.

Uses a ``tmp_path``-backed file SQLite (CC040 - no ``:memory:``).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.quality.counters import QualityCounter


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed SqliteAdapter."""
    db_dir = tmp_path / "cc-counters-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed(adapter: SqliteAdapter, source_id: str, database_name: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}-{database_name}",
            "status": "indexed",
        }
    )


def test_incremented_counter_reads_back(adapter: SqliteAdapter) -> None:
    """A counter written by the pipeline is visible to a reader."""
    _seed(adapter, "src-a", "test")

    adapter.increment_source_counter(
        source_id="src-a",
        database_name="test",
        column=QualityCounter.LLM_CHUNKS_TRUNCATED.value,
        n=2,
    )

    counters = adapter.get_source_counters(source_id="src-a", database_name="test")

    assert counters[QualityCounter.LLM_CHUNKS_TRUNCATED.value] == 2


def test_get_file_projection_does_not_expose_counters(adapter: SqliteAdapter) -> None:
    """Document why a dedicated accessor exists rather than reusing get_file.

    ``get_file``'s narrow projection omits the counter columns. Reading them
    through it silently yields ``None``, which is exactly how a truncated
    extraction passed as clean. If this ever starts passing, the projection
    changed and the dedicated accessor can be revisited.
    """
    _seed(adapter, "src-b", "test")
    adapter.increment_source_counter(
        source_id="src-b",
        database_name="test",
        column=QualityCounter.LLM_CHUNKS_TRUNCATED.value,
        n=1,
    )

    row = adapter.get_file("src-b", "test") or {}

    assert row.get(QualityCounter.LLM_CHUNKS_TRUNCATED.value) is None
    assert (
        adapter.get_source_counters(source_id="src-b", database_name="test")[
            QualityCounter.LLM_CHUNKS_TRUNCATED.value
        ]
        == 1
    )


def test_read_set_covers_the_write_allowlist(adapter: SqliteAdapter) -> None:
    """The readable set must cover every integer counter that can be written."""
    _seed(adapter, "src-c", "test")

    counters = adapter.get_source_counters(source_id="src-c", database_name="test")

    assert set(counters) == set(adapter._COUNTER_COLUMN_ALLOWLIST)


def test_unknown_source_yields_zeros(adapter: SqliteAdapter) -> None:
    """Observability reads never raise; callers do not branch on them."""
    counters = adapter.get_source_counters(source_id="missing", database_name="test")

    assert counters
    assert all(value == 0 for value in counters.values())
