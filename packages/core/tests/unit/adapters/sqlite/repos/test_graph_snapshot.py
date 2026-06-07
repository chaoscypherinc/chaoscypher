# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for GraphSnapshotRepository — storage port + SQLite implementation.

Tests follow TDD convention: write failing tests first, then implement.

Fixtures:
    repo(tmp_path) — creates a fresh on-disk SQLite DB per test (CC040).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.repos.graph_snapshot import GraphSnapshotRepository
from chaoscypher_core.services.graph.snapshot.models import (
    GraphBreakdown,
    GraphStats,
    SourceBreakdown,
    TemplateEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> GraphSnapshotRepository:
    """Return a fresh GraphSnapshotRepository backed by a temp SQLite DB."""
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    return GraphSnapshotRepository(engine)


def _make_breakdown(
    database_name: str = "mydb", node_count: int = 10, edge_count: int = 5
) -> GraphBreakdown:
    """Build a minimal but valid GraphBreakdown for test assertions."""
    return GraphBreakdown(
        database_name=database_name,
        generated_at=datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC),
        stats=GraphStats(
            total_nodes=node_count,
            total_edges=edge_count,
            total_sources=2,
        ),
        sources=[
            SourceBreakdown(
                id="src-1",
                name="Source One",
                source_type="pdf",
                total_entities=node_count,
                total_internal_links=edge_count,
                templates=[
                    TemplateEntry(id="tpl-1", name="Person", color="#FF0000", count=node_count),
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_upsert_and_get_current_roundtrip(repo: GraphSnapshotRepository) -> None:
    """Write a breakdown, read it back; full equality including stats and sources."""
    breakdown = _make_breakdown()

    repo.upsert(breakdown)
    result = repo.get_current("mydb")

    assert result is not None
    assert result.database_name == breakdown.database_name
    assert result.generated_at == breakdown.generated_at
    assert result.stats == breakdown.stats
    assert result.sources == breakdown.sources
    assert result == breakdown


def test_get_current_returns_none_for_missing_db(repo: GraphSnapshotRepository) -> None:
    """get_current returns None when no snapshot exists for the database."""
    result = repo.get_current("nonexistent")
    assert result is None


def test_upsert_replaces_previous_snapshot(repo: GraphSnapshotRepository) -> None:
    """Upsert twice for the same database_name; the latest payload wins, no duplicates."""
    first = _make_breakdown(database_name="mydb", node_count=10, edge_count=5)
    second = _make_breakdown(database_name="mydb", node_count=99, edge_count=42)

    repo.upsert(first)
    repo.upsert(second)

    result = repo.get_current("mydb")
    assert result is not None
    assert result.stats.total_nodes == 99
    assert result.stats.total_edges == 42

    # Ensure no duplicate row
    from sqlalchemy import func, select
    from sqlmodel import Session

    from chaoscypher_core.adapters.sqlite.models import GraphSnapshot

    with Session(repo._engine) as session:
        count = session.exec(  # type: ignore[attr-defined]
            select(func.count())
            .select_from(GraphSnapshot)
            .where(GraphSnapshot.database_name == "mydb")
        ).one()[0]
    assert count == 1


def test_get_staleness_info_returns_counts_without_parsing_payload(
    repo: GraphSnapshotRepository,
) -> None:
    """Upsert a breakdown, get staleness info, assert counts match."""
    breakdown = _make_breakdown(node_count=7, edge_count=3)

    repo.upsert(breakdown)
    info = repo.get_staleness_info("mydb")

    assert info is not None
    assert info.generated_at == breakdown.generated_at
    assert info.node_count == 7
    assert info.edge_count == 3


def test_get_staleness_info_returns_none_for_missing_db(repo: GraphSnapshotRepository) -> None:
    """get_staleness_info returns None when no snapshot exists."""
    info = repo.get_staleness_info("does_not_exist")
    assert info is None


def test_upsert_derives_counts_from_stats(repo: GraphSnapshotRepository) -> None:
    """Row node_count/edge_count columns reflect breakdown.stats.* values."""
    breakdown = _make_breakdown(node_count=42, edge_count=17)

    repo.upsert(breakdown)

    from sqlmodel import Session

    from chaoscypher_core.adapters.sqlite.models import GraphSnapshot

    with Session(repo._engine) as session:
        row = session.get(GraphSnapshot, "mydb")

    assert row is not None
    assert row.node_count == 42
    assert row.edge_count == 17
