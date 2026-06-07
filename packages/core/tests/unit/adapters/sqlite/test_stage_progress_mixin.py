# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Adapter mixin tests: StageProgressMixin against llm_stage_progress."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_source(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """Insert a parent source row so FK constraints on llm_stage_progress are satisfied.

    The ``filepath`` is rooted under ``tmp_path`` (the test's pytest temp dir)
    rather than a relative path.  This matters because ``delete_source`` —
    called by ``test_cascade_delete`` below — cascades through
    ``delete_source_files`` which does ``shutil.rmtree(Path(filepath).parent,
    ignore_errors=True)``.  A relative ``filepath`` would resolve the
    parent to ``Path('.')`` and rmtree would walk the CURRENT WORKING
    DIRECTORY (the project root), with ``ignore_errors=True`` silently
    skipping the files it can't delete (open file handles) — wiping
    everything else.

    Rooting the filepath under ``tmp_path`` scopes the rmtree to pytest's
    own temp dir, which pytest cleans up after the session anyway.  The
    follow-up TODO is to harden ``delete_source_files`` itself with a
    path-validation guard (out of scope for the stage-progress work).
    """
    fake_source_dir = tmp_path / "fake-source-files"
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "default",
            "filename": "test.pdf",
            "filepath": str(fake_source_dir / "test.pdf"),
            "status": "indexing",
        }
    )


@pytest.fixture
def adapter(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> SqliteAdapter:
    _seed_source(sqlite_adapter, tmp_path)
    return sqlite_adapter


@pytest.mark.asyncio
async def test_start_stage_inserts_row(adapter: SqliteAdapter) -> None:
    """start_stage creates a fresh row with the supplied total."""
    now = datetime.now(UTC)
    await adapter.start_stage(
        parent_id="src-1",
        stage_name="vision",
        total=184,
        started_at=now,
    )
    progress = adapter._fetch_stage_progress("src-1")
    assert "vision" in progress
    assert progress["vision"]["total"] == 184
    assert progress["vision"]["processed"] == 0
    assert progress["vision"]["avg_ms"] is None
    assert progress["vision"]["completed_at"] is None


@pytest.mark.asyncio
async def test_start_stage_is_idempotent(adapter: SqliteAdapter) -> None:
    """start_stage on an existing row resets processed/avg_ms/completed_at."""
    now = datetime.now(UTC)
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=184, started_at=now)
    await adapter.tick_stage(
        parent_id="src-1", stage_name="vision", processed=47, avg_ms=8200, last_activity=now
    )
    await adapter.complete_stage(parent_id="src-1", stage_name="vision", completed_at=now)
    # Re-start with a different total.
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=200, started_at=now)
    progress = adapter._fetch_stage_progress("src-1")
    assert progress["vision"]["total"] == 200
    assert progress["vision"]["processed"] == 0  # reset
    assert progress["vision"]["avg_ms"] is None  # reset
    assert progress["vision"]["completed_at"] is None  # reset


@pytest.mark.asyncio
async def test_tick_stage_updates_progress(adapter: SqliteAdapter) -> None:
    now = datetime.now(UTC)
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=184, started_at=now)
    await adapter.tick_stage(
        parent_id="src-1", stage_name="vision", processed=47, avg_ms=8200, last_activity=now
    )
    progress = adapter._fetch_stage_progress("src-1")
    assert progress["vision"]["processed"] == 47
    assert progress["vision"]["avg_ms"] == 8200


@pytest.mark.asyncio
async def test_complete_stage_sets_timestamp(adapter: SqliteAdapter) -> None:
    now = datetime.now(UTC)
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=184, started_at=now)
    await adapter.complete_stage(parent_id="src-1", stage_name="vision", completed_at=now)
    progress = adapter._fetch_stage_progress("src-1")
    stored = progress["vision"]["completed_at"]
    assert stored is not None
    # SQLite round-trips datetimes as space-separated strings ("2026-05-10 18:..."),
    # while Python isoformat() is "T"-separated. Normalise before comparing.
    stored_str = stored.isoformat() if hasattr(stored, "isoformat") else str(stored)
    assert stored_str.replace("T", " ") == now.isoformat().replace("T", " ")


@pytest.mark.asyncio
async def test_update_stage_extras_writes_json(adapter: SqliteAdapter) -> None:
    """MCP-style extras (preview counts) round-trip through extras_json."""
    now = datetime.now(UTC)
    await adapter.start_stage(
        parent_id="src-1", stage_name="mcp_extraction", total=45, started_at=now
    )
    await adapter.update_stage_extras(
        parent_id="src-1",
        stage_name="mcp_extraction",
        extras={"entities_preview": 312, "relationships_preview": 198},
        last_activity=now,
    )
    progress = adapter._fetch_stage_progress("src-1")
    assert progress["mcp_extraction"]["extras"] == {
        "entities_preview": 312,
        "relationships_preview": 198,
    }


@pytest.mark.asyncio
async def test_cascade_delete(adapter: SqliteAdapter) -> None:
    """Deleting the source cascades to its progress rows."""
    now = datetime.now(UTC)
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=10, started_at=now)
    adapter.delete_source(source_id="src-1", database_name="default")
    progress = adapter._fetch_stage_progress("src-1")
    assert progress == {}


@pytest.mark.asyncio
async def test_get_source_includes_stage_progress(adapter: SqliteAdapter) -> None:
    """get_source returns stage_progress dict alongside source row data."""
    now = datetime.now(UTC)
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=184, started_at=now)
    await adapter.tick_stage(
        parent_id="src-1", stage_name="vision", processed=47, avg_ms=8200, last_activity=now
    )

    source = adapter.get_source("src-1", "default")

    assert source is not None
    assert "stage_progress" in source
    assert "vision" in source["stage_progress"]
    assert source["stage_progress"]["vision"]["processed"] == 47
    assert source["stage_progress"]["vision"]["avg_ms"] == 8200


@pytest.mark.asyncio
async def test_get_source_no_stages_returns_empty_dict(adapter: SqliteAdapter) -> None:
    """A source with no progress rows returns stage_progress={}."""
    source = adapter.get_source("src-1", "default")
    assert source is not None
    assert source["stage_progress"] == {}


@pytest.mark.asyncio
async def test_list_sources_bulk_fetches_stage_progress(
    adapter: SqliteAdapter,
    tmp_path: Path,
) -> None:
    """list_sources populates stage_progress for every row in a single
    follow-up query (not N+1).
    """
    # Add a second source with an ABSOLUTE filepath rooted in tmp_path
    # (see _seed_source for why relative paths are forbidden).
    adapter.create_source(
        {
            "id": "src-2",
            "database_name": "default",
            "filename": "b.pdf",
            "filepath": str(tmp_path / "fake-source-files-2" / "b.pdf"),
            "status": "indexing",
        }
    )
    now = datetime.now(UTC)
    await adapter.start_stage(parent_id="src-1", stage_name="vision", total=184, started_at=now)
    await adapter.start_stage(parent_id="src-2", stage_name="embedding", total=120, started_at=now)

    # list_sources uses self.database_name (set to "default" in the fixture)
    # and returns tuple[list[dict], int].
    sources, _total = adapter.list_sources(page=1, page_size=10)
    by_id = {s["id"]: s for s in sources}
    assert "vision" in by_id["src-1"]["stage_progress"]
    assert "embedding" in by_id["src-2"]["stage_progress"]
