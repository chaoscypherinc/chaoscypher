# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The GraphRAG handlers under audit wave 6, over a real search repository and storage adapter.

- ``hybrid_search:scope_short`` compared the scoped hybrid arm with the
  chunks in the scope (``count_in_scope``), which ignores the similarity
  floor and keyword misses: "What did the regiment eat?" got 5 chunks
  unscoped with no fault and 2 scoped with the fault. The search now says
  how many candidates it saw before its cut, and the handler compares with
  that.
- A switched-off source's chunks reached the hybrid arm, scoped or not.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.benchmark import retrieval_sample as rs
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DEFAULT_NAME_CONVENTIONS,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    HYBRID_MIN_SIMILARITY,
    HYBRID_OVERFETCH,
    GraphRAGToolHandlers,
)
from chaoscypher_core.utils.retrieval_faults import SCOPE_SHORT


DIM = 4
_ABOVE = 2
"""Chunks of source ``a`` above the similarity floor for :data:`_QUERY_VECTOR`; the other ten are below it."""
_QUERY_VECTOR = [1.0, 0.0, 0.0, 0.0]
_FAR = [0.0, 0.0, 0.0, 1.0]
"""The vector of a question about nothing the sources hold (orthogonal to every chunk)."""


def _chunk_vector(i: int) -> list[float]:
    """Chunk ``a{i}``: the first :data:`_ABOVE` near the query, the rest below the floor."""
    return [1.0, 0.1, 0.0, 0.0] if i < _ABOVE else [0.1, 1.0, 0.0, 0.0]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[Path, Any, Any]]:
    """Twelve chunks of ``a`` (two above the floor), three of ``b``; a real search repository and adapter."""
    path = tmp_path / "app.db"
    engine = get_engine(str(path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    with SafeSession(engine) as session:
        session.add_all(
            SourceRow(id=sid, database_name="db", filename=f"{sid}.txt", filepath=sid)
            for sid in ("a", "b")
        )
        session.commit()
    with sqlite3.connect(path) as con:
        rows = [(f"a{i:02d}", "a", f"Regiment orders {i}.") for i in range(12)]
        rows += [(f"b{i}", "b", f"A letter home {i}.") for i in range(3)]
        con.executemany(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', ?, 0, ?, 'indexed', '2026-10-06', 'none')",
            rows,
        )
    repo = SearchRepository(engine, vector_dim=DIM, embedding_model="m")
    repo.index_embeddings_batch(
        [(f"chunk:a{i:02d}", _chunk_vector(i)) for i in range(12)]
        + [(f"chunk:b{i}", [0.9, 0.0, 0.1, 0.0]) for i in range(3)],
        item_type="chunk",
    )
    storage = rs._storage_adapter(engine)
    yield path, repo, storage
    evict_engine(path)


async def _embed(text: str) -> dict[str, list[float]]:
    return {"embedding": _FAR if "Xyzzy" in text else _QUERY_VECTOR}


def _handlers(repo: Any, indexing: Any) -> GraphRAGToolHandlers:
    return GraphRAGToolHandlers(
        graph_repository=MagicMock(),
        search_repository=repo,
        indexing_repository=indexing,
        embedding_callback=_embed,
        name_conventions=DEFAULT_NAME_CONVENTIONS,
        database_name="db",
    )


class _LosingStorage:
    """The real storage adapter, except that its chunk batch read loses all but the first chunk."""

    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def get_chunks_by_ids_batch(self, ids: list[str], **kw: Any) -> list[dict[str, Any]]:
        return list(self._storage.get_chunks_by_ids_batch(ids, **kw))[:1]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._storage, name)


async def _faults(handlers: GraphRAGToolHandlers, question: str, scope: list[str] | None) -> Any:
    result = await handlers.hybrid_retrieval(question, 3, source_ids=scope)
    return [c["chunk_id"] for c in result["chunks"]], result["retrieval_stats"]["faults"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [["a"], None], ids=["scoped", "unscoped"])
async def test_a_question_about_nothing_is_not_a_fault(
    world: tuple[Path, Any, Any], scope: list[str] | None
) -> None:
    """No keyword hit, every vector below the floor: no chunk, and no ``scope_short``."""
    _, repo, storage = world
    chunks, faults = await _faults(_handlers(repo, storage), "Who is Xyzzy Plugh?", scope)
    assert chunks == []
    assert SCOPE_SHORT not in faults


@pytest.mark.asyncio
async def test_a_scope_holding_fewer_than_the_budget_above_the_floor_is_not_a_fault(
    world: tuple[Path, Any, Any],
) -> None:
    """Twelve chunks in scope, two above the floor, a budget of three: two chunks, no fault.

    Until wave 6 the handler compared the two with ``min(3, 12)`` and recorded the fault.
    """
    _, repo, storage = world
    assert HYBRID_MIN_SIMILARITY > 0.1  # the ten far chunks are below it
    assert repo.count_in_scope("chunk", ["a"]) == 12
    chunks, faults = await _faults(_handlers(repo, storage), "What did they eat?", ["a"])
    assert chunks == ["a00", "a01"]
    assert SCOPE_SHORT not in faults


@pytest.mark.asyncio
async def test_candidates_the_search_saw_but_the_arm_lost_are_a_fault(
    world: tuple[Path, Any, Any],
) -> None:
    """``k`` candidates above the floor (keyword hits on every chunk of ``a``), one chunk out: the fault."""
    _, repo, storage = world
    hits = await repo.hybrid_search(
        "Regiment orders", k=3 * HYBRID_OVERFETCH, embedding_provider_callback=_embed,
        min_similarity=HYBRID_MIN_SIMILARITY, item_type="chunk", source_ids=["a"],
    )  # fmt: skip
    assert hits.scope_exhausted is False
    chunks, faults = await _faults(
        _handlers(repo, _LosingStorage(storage)), "Regiment orders", ["a"]
    )
    assert len(chunks) == 1
    assert SCOPE_SHORT in faults
    chunks, faults = await _faults(_handlers(repo, storage), "Regiment orders", ["a"])
    assert len(chunks) == 3  # the budget
    assert SCOPE_SHORT not in faults


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, ["b"], ["a", "b"]], ids=["unscoped", "to-it", "both"])
async def test_a_switched_off_sources_chunks_never_reach_the_hybrid_arm(
    world: tuple[Path, Any, Any], scope: list[str] | None
) -> None:
    path, repo, storage = world
    handlers = _handlers(repo, storage)
    before, _ = await _faults(handlers, "A letter home", scope)
    assert any(c.startswith("b") for c in before)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE sources SET enabled = 0 WHERE id = 'b'")
    hidden, faults = await _faults(handlers, "A letter home", scope)
    assert not any(c.startswith("b") for c in hidden)
    assert SCOPE_SHORT not in faults
    with sqlite3.connect(path) as con:
        con.execute("UPDATE sources SET enabled = 1 WHERE id = 'b'")
    assert (await _faults(handlers, "A letter home", scope))[0] == before
