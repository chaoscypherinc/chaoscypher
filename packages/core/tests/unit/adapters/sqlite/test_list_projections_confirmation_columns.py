# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""list_sources / list_files projections must surface gate columns.

The confirmation gate stores its proposal + flags on SourceRow. The API,
CLI, and MCP list surfaces read through ``list_sources`` (sources.py) and
``list_files`` (source_files.py), both of which use ``load_only()`` column
projection. A column absent from the projection never reaches the dict —
so the awaiting-confirmation list would silently drop the proposal. These
tests pin the three columns into both projections.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def in_memory_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed SqliteAdapter with all tables created."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


def _seed_awaiting_source(adapter: SqliteAdapter, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "awaiting_confirmation",
            "confirmation_required": True,
            "detection_proposal": {
                "ranking": [{"domain": "technical", "score": 3.5}],
                "confidence": 3.5,
                "detected_domain": "technical",
                "low_confidence": False,
            },
        }
    )


def test_list_sources_projection_includes_gate_columns(in_memory_adapter: SqliteAdapter) -> None:
    """list_sources surfaces confirmation_required / extraction_confirmed_at /
    detection_proposal on each row dict.
    """
    _seed_awaiting_source(in_memory_adapter, "src-list-sources")

    rows, total = in_memory_adapter.list_sources(status="awaiting_confirmation")

    assert total == 1
    row = rows[0]
    assert "confirmation_required" in row
    assert "extraction_confirmed_at" in row
    assert "detection_proposal" in row
    assert row["confirmation_required"] is True
    assert row["detection_proposal"]["detected_domain"] == "technical"
    assert row["detection_proposal"]["ranking"][0]["score"] == pytest.approx(3.5)


def test_list_files_projection_includes_gate_columns(in_memory_adapter: SqliteAdapter) -> None:
    """list_files surfaces the same three gate columns."""
    _seed_awaiting_source(in_memory_adapter, "src-list-files")

    rows = in_memory_adapter.list_files(
        database_name=in_memory_adapter.database_name,
        status="awaiting_confirmation",
    )

    assert len(rows) == 1
    row = rows[0]
    assert "confirmation_required" in row
    assert "extraction_confirmed_at" in row
    assert "detection_proposal" in row
    assert row["confirmation_required"] is True
    assert row["detection_proposal"]["detected_domain"] == "technical"
