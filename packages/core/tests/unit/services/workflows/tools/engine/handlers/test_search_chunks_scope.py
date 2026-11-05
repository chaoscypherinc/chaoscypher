# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``search_chunks``: the source and tag scope reach the search SQL, before its limit.

Until 2026-11-03 the handler searched every source and dropped the hits of
other sources afterwards, so a source outranked by another answered nothing.
The scope now runs in the repository's query, and each chunk carries its
document's title (``source_title``) next to its filename.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers import (
    NodeToolHandlers,
)
from chaoscypher_core.settings import SearchSettings
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[tuple[Path, SqliteAdapter]]:
    path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(path)), checkfirst=True)
    adapter = SqliteAdapter(str(path), database_name="db")
    adapter.connect()
    try:
        yield path, adapter
    finally:
        adapter.disconnect()
        evict_engine(path)


def _source(path: Path, sid: str, *, title: str | None = None, tags: tuple[str, ...] = ()) -> None:
    with SafeSession(get_engine(str(path))) as session:
        session.add(
            SourceRow(id=sid, database_name="db", filename=f"{sid}.txt", filepath=sid, title=title)
        )
        session.commit()
    with sqlite3.connect(path) as con:
        for name in tags:
            con.execute(
                "INSERT OR IGNORE INTO source_tags (id, database_name, name, created_at) "
                "VALUES (?, 'db', ?, '2026-11-03')",
                (f"tag-{name}", name),
            )
            con.execute(
                "INSERT INTO source_tag_assignments (id, source_id, tag_id, database_name, "
                "assigned_at) VALUES (?, ?, ?, 'db', '2026-11-03')",
                (f"as-{sid}-{name}", sid, f"tag-{name}"),
            )


def _chunks(path: Path, source: str, count: int, content: str) -> list[str]:
    ids = [f"{source}-{i:02d}" for i in range(count)]
    with sqlite3.connect(path) as con:
        con.executemany(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', ?, ?, ?, 'indexed', '2026-11-03', 'none')",
            [(cid, source, i, content) for i, cid in enumerate(ids)],
        )
    return ids


def _handler(path: Path, adapter: SqliteAdapter) -> NodeToolHandlers:
    return NodeToolHandlers(
        graph_repository=None,  # type: ignore[arg-type]
        search_repository=open_search_repo(path),
        indexing_repository=adapter,
        search_settings=SearchSettings(enable_rerank=False),
    )


def _outranked(path: Path, **b: Any) -> list[str]:
    """Source ``doc-a``: 40 chunks that outrank the 2 of ``doc-b``; returns doc-b's chunk ids."""
    _source(path, "doc-a")
    _source(path, "doc-b", **b)
    _chunks(path, "doc-a", 40, "heron heron heron heron")
    return _chunks(path, "doc-b", 2, "heron and a long run of other words besides it")


async def test_source_scope_is_applied_before_the_limit(
    storage: tuple[Path, SqliteAdapter],
) -> None:
    path, adapter = storage
    b_ids = _outranked(path)
    result = await _handler(path, adapter).search_chunks("heron", limit=2, source_id="doc-b")
    assert result["success"] is True
    assert sorted(c["chunk_id"] for c in result["chunks"]) == b_ids


async def test_tag_scope_reaches_sql(storage: tuple[Path, SqliteAdapter]) -> None:
    path, adapter = storage
    b_ids = _outranked(path, tags=("ref",))
    result = await _handler(path, adapter).search_chunks("heron", limit=2, tags=["Ref"])
    assert result["success"] is True
    assert sorted(c["chunk_id"] for c in result["chunks"]) == b_ids
    nothing = await _handler(path, adapter).search_chunks("heron", tags=["nobody"])
    assert nothing["success"] is True
    assert nothing["chunks"] == []


async def test_results_carry_source_title(storage: tuple[Path, SqliteAdapter]) -> None:
    path, adapter = storage
    _source(path, "doc-a", title="Alpha Notes")
    _source(path, "doc-b")
    _chunks(path, "doc-a", 1, "heron alpha")
    _chunks(path, "doc-b", 1, "heron beta")
    result = await _handler(path, adapter).search_chunks("heron")
    by_source = {c["source_id"]: c for c in result["chunks"]}
    assert by_source["doc-a"]["source_title"] == "Alpha Notes"
    assert by_source["doc-a"]["filename"] == "doc-a.txt"
    assert by_source["doc-b"]["source_title"] == "doc-b.txt"


async def test_bad_tag_refused_in_plain_words(storage: tuple[Path, SqliteAdapter]) -> None:
    path, adapter = storage
    result = await _handler(path, adapter).search_chunks("heron", tags=["Work Docs"])
    assert result == {
        "success": False,
        "error": (
            "tag \"Work Docs\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
            "starting with a letter or digit, at most 64 characters"
        ),
    }


async def test_disjoint_source_id_and_source_ids_return_nothing(
    storage: tuple[Path, SqliteAdapter],
) -> None:
    path, adapter = storage
    _outranked(path)
    unsearched = AsyncMock()
    spied = NodeToolHandlers(
        graph_repository=None,  # type: ignore[arg-type]
        search_repository=unsearched,
        indexing_repository=adapter,
        search_settings=SearchSettings(enable_rerank=False),
    )
    result = await spied.search_chunks("heron", source_id="doc-a", source_ids=["doc-b"])
    assert result["success"] is True
    assert result["chunks"] == []
    unsearched.hybrid_search.assert_not_called()
    both = await _handler(path, adapter).search_chunks(
        "heron", source_id="doc-b", source_ids=["doc-a", "doc-b"]
    )
    assert {c["source_id"] for c in both["chunks"]} == {"doc-b"}


async def test_bad_match_refused_in_plain_words(storage: tuple[Path, SqliteAdapter]) -> None:
    path, adapter = storage
    result = await _handler(path, adapter).search_chunks("heron", tags=["ref"], match="some")
    assert result == {"success": False, "error": 'match is "any" or "all", not "some"'}
