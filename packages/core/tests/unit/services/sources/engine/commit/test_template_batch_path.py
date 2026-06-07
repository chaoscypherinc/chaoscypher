# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Node template creation must produce one template per (source_id, type, name).

Regression: under SafeSession busy-retry rollback, the old sequential loop
in create_suggested_templates created duplicate (database_name, source_id,
type, name) rows because session.get() missed the just-added template
after the identity map was cleared.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.engine.commit.template import TemplateCommitHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Path) -> SqliteAdapter:
    """Return a connected SqliteAdapter backed by a real file SQLite DB."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


def _seed_source_raw(session, source_id: str, database_name: str) -> None:
    """Seed a minimal SourceRow via the session directly.

    Uses session.commit() (not maybe_commit) so the seeding path is not
    affected by any monkeypatch on SafeSession.maybe_commit.
    """
    row = SourceRow(
        id=source_id,
        database_name=database_name,
        filename="batch_test.md",
        filepath="/tmp/batch_test.md",
        file_type="markdown",
        file_size=1,
        content_hash=f"hash-{source_id}",
        status=SourceStatus.PENDING.value,
    )
    session.add(row)
    session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def template_handler_factory(tmp_path: Path):
    """Factory that wires a real TemplateCommitHandler against a fresh SQLite DB.

    Returns a zero-argument callable so each test invocation gets a fresh
    handler, session, and source_id triple.  The factory seeds the required
    SourceRow so graph_templates.source_id FK constraints are satisfied.
    """
    adapters: list[SqliteAdapter] = []

    def _factory():
        adapter = _make_adapter(tmp_path)
        adapters.append(adapter)
        source_id = "src_batch_test"
        assert adapter.session is not None
        _seed_source_raw(adapter.session, source_id, adapter.database_name)
        graph_repo = GraphRepository(
            session=adapter.session,
            database_name=adapter.database_name,
        )
        handler = TemplateCommitHandler(graph_repository=graph_repo)
        return handler, adapter.session, source_id

    yield _factory

    for adapter in adapters:
        adapter.disconnect()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_node_template_names_collapse_to_one_row(
    template_handler_factory,
):
    """If suggested_templates contains 'Character' three times, only one row is written."""
    handler, session, source_id = template_handler_factory()

    commit_data = {
        "create_templates": True,
        "suggested_templates": [
            {"name": "Character", "description": "A person", "properties": []},
            {"name": "Character", "description": "Different desc, same name", "properties": []},
            {"name": "Character", "description": "Yet another", "properties": []},
            {"name": "Place", "description": "A location", "properties": []},
        ],
    }

    result = await handler.create_suggested_templates(commit_data, source_id=source_id)
    # Signature from Phase 1: (created_template_ids, name_to_id, all_used, inserted_count)
    _, name_to_id, _, inserted = result

    rows = session.exec(
        select(GraphTemplate).where(
            GraphTemplate.source_id == source_id,
            GraphTemplate.template_type == "node",
        )
    ).all()
    names = sorted(r.name for r in rows)
    assert names == ["Character", "Place"], f"expected one Character + one Place, got {names}"
    assert len(set(name_to_id.values())) == 2, (
        "name_to_id must point to exactly two distinct template IDs"
    )
    assert inserted == 2, f"expected 2 newly inserted rows, got {inserted}"


@pytest.mark.asyncio
async def test_recommit_does_not_duplicate_templates(
    template_handler_factory,
):
    """Re-running create_suggested_templates (crash-and-resume) must not create duplicate rows.

    Simulates the SafeSession busy-retry scenario: if the commit handler is
    called twice for the same source (e.g., because a previous attempt
    partially succeeded and the source is retried), the stable content key
    deduplication in upsert_templates_batch must produce exactly the same
    rows on the second call — not duplicates.

    The OLD sequential upsert_template loop was also susceptible to this:
    after a busy-retry rollback cleared the session identity map, a second
    call would see session.get() return None for already-persisted rows and
    attempt a second INSERT.
    """
    handler, session, source_id = template_handler_factory()

    commit_data = {
        "create_templates": True,
        "suggested_templates": [
            {"name": "Character", "description": "A person", "properties": []},
            {"name": "Character", "description": "A duplicate name", "properties": []},
            {"name": "Place", "description": "A location", "properties": []},
        ],
    }

    # First call: should create 2 rows (Character + Place)
    result1 = await handler.create_suggested_templates(commit_data, source_id=source_id)
    _, name_to_id1, _, inserted1 = result1
    assert inserted1 == 2, f"first call: expected 2 insertions, got {inserted1}"

    # Second call (re-commit / identity-map cleared): should reuse existing rows
    result2 = await handler.create_suggested_templates(commit_data, source_id=source_id)
    _, name_to_id2, _, inserted2 = result2
    assert inserted2 == 0, f"second call: expected 0 insertions (all reused), got {inserted2}"

    # Exactly 2 rows must exist (Character + Place) — no duplicates
    rows = session.exec(
        select(GraphTemplate).where(
            GraphTemplate.source_id == source_id,
            GraphTemplate.template_type == "node",
        )
    ).all()
    names = sorted(r.name for r in rows)
    assert names == ["Character", "Place"], (
        f"expected exactly one Character + one Place across two commits, got {names}"
    )

    # Both calls must return the same IDs for the same names
    assert name_to_id1.get("character") == name_to_id2.get("character"), (
        "Character template ID must be stable across re-commits"
    )
    assert name_to_id1.get("place") == name_to_id2.get("place"), (
        "Place template ID must be stable across re-commits"
    )


@pytest.mark.asyncio
async def test_invalid_template_names_are_skipped(
    template_handler_factory,
):
    """Templates with invalid names (unknown, untitled, n/a, none) must be skipped."""
    handler, session, source_id = template_handler_factory()

    commit_data = {
        "create_templates": True,
        "suggested_templates": [
            {"name": "unknown", "description": "Should be skipped", "properties": []},
            {"name": "Untitled", "description": "Also skipped", "properties": []},
            {"name": "n/a", "description": "Skipped too", "properties": []},
            {"name": "none", "description": "Skipped", "properties": []},
            {"name": "ValidType", "description": "Should be created", "properties": []},
        ],
    }

    result = await handler.create_suggested_templates(commit_data, source_id=source_id)
    created_ids, name_to_id, _, inserted = result

    rows = session.exec(
        select(GraphTemplate).where(
            GraphTemplate.source_id == source_id,
            GraphTemplate.template_type == "node",
        )
    ).all()
    assert len(rows) == 1, f"expected only ValidType, got {[r.name for r in rows]}"
    assert rows[0].name == "ValidType"
    assert inserted == 1


@pytest.mark.asyncio
async def test_create_templates_false_returns_empty(
    template_handler_factory,
):
    """When create_templates is False, no templates are created and empty tuples returned."""
    handler, session, source_id = template_handler_factory()

    commit_data = {
        "create_templates": False,
        "suggested_templates": [
            {"name": "Character", "description": "A person", "properties": []},
        ],
    }

    created_ids, name_to_id, all_used, inserted = await handler.create_suggested_templates(
        commit_data, source_id=source_id
    )

    assert created_ids == []
    assert name_to_id == {}
    assert all_used == []
    assert inserted == 0
