# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The retrieval rungs: mode names, the dataset-id suffix and the naive index spec."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.benchmark.retrieval import (
    NAIVE_CHUNK_OVERLAP,
    NAIVE_CHUNK_SIZE,
    NAIVE_TOP_K,
    RETRIEVAL_MODES,
    NaiveIndexSpec,
    embedder_slug,
    retrieval_dataset_suffix,
    validate_retrieval_mode,
)


def test_the_three_rungs_in_order_with_graphrag_first() -> None:
    assert RETRIEVAL_MODES == ("graphrag", "hybrid", "naive")
    assert [validate_retrieval_mode(m) for m in RETRIEVAL_MODES] == list(RETRIEVAL_MODES)


@pytest.mark.parametrize("bad", ["plain", "", None, 3, "GraphRAG"])
def test_an_unknown_mode_is_refused_naming_the_modes(bad: object) -> None:
    with pytest.raises(ValueError, match="graphrag, hybrid, naive"):
        validate_retrieval_mode(bad)


def test_only_the_baselines_suffix_the_dataset_id() -> None:
    """The graphrag rows keep today's id and pool with every existing row."""
    assert retrieval_dataset_suffix("graphrag") == ""
    assert retrieval_dataset_suffix("hybrid") == "__retrieval_hybrid"
    assert retrieval_dataset_suffix("naive") == "__retrieval_naive"
    with pytest.raises(ValueError, match="retrieval"):
        retrieval_dataset_suffix("plain")


def test_the_budgets_are_the_rung_definitions() -> None:
    """Ten 900-character chunks against four 512-token ones: the same order of characters."""
    from chaoscypher_core.settings import EngineSettings

    assert EngineSettings().graphrag.chunk_limit == 10
    assert (NAIVE_CHUNK_SIZE, NAIVE_CHUNK_OVERLAP, NAIVE_TOP_K) == (512, 50, 4)


def test_the_naive_index_spec_and_the_embedder_slug() -> None:
    spec = NaiveIndexSpec(
        corpus_id="war_and_peace_book1",
        embedder="ollama/qwen3-embedding:0.6b",
        cache_dir=Path("/tmp/x"),
    )
    assert embedder_slug(spec.embedder) == "ollama_qwen3-embedding:0.6b"
    assert spec == NaiveIndexSpec(
        "war_and_peace_book1", "ollama/qwen3-embedding:0.6b", Path("/tmp/x")
    )


def test_an_empty_or_short_retrieval_is_refused_and_a_full_one_passes() -> None:
    """Degraded retrievals raise instead of being pinned (audit R8)."""
    from chaoscypher_core.benchmark.retrieval import (
        DegradedRetrievalError,
        require_complete_retrieval,
    )

    full = {
        "chunks": [{"chunk_id": str(i)} for i in range(10)],
        "graph_context": {"seed_entities": [{"id": "n1"}]},
        "retrieval_stats": {"mode": "full_graphrag", "faults": []},
    }
    require_complete_retrieval(full, mode="graphrag", chunk_limit=10, indexed_chunks=179)
    require_complete_retrieval(
        {"chunks": full["chunks"][:4]}, mode="naive", chunk_limit=10, indexed_chunks=300
    )
    hybrid_stats = {"retrieval_stats": {"mode": "hybrid", "faults": []}}
    # a small index cannot fill the budget: as many as it holds is complete
    require_complete_retrieval(
        {"chunks": full["chunks"][:3], **hybrid_stats},
        mode="hybrid",
        chunk_limit=10,
        indexed_chunks=3,
    )
    with pytest.raises(DegradedRetrievalError, match="returned 7 chunks"):
        require_complete_retrieval(
            {"chunks": full["chunks"][:7], **hybrid_stats},
            mode="hybrid",
            chunk_limit=10,
            indexed_chunks=179,
        )
    # the hybrid rung is guarded like graphrag (audit wave 3): a bare chunk list has no record
    with pytest.raises(DegradedRetrievalError, match="no fault record"):
        require_complete_retrieval(
            {"chunks": full["chunks"]}, mode="hybrid", chunk_limit=10, indexed_chunks=179
        )
    with pytest.raises(DegradedRetrievalError, match="returned 0 chunks"):
        require_complete_retrieval(
            {"chunks": []}, mode="graphrag", chunk_limit=10, indexed_chunks=9
        )
    with pytest.raises(DegradedRetrievalError, match="holds no chunks"):
        require_complete_retrieval(full, mode="graphrag", chunk_limit=10, indexed_chunks=0)


def test_a_packs_indexed_chunk_count_reads_the_vector_table(tmp_path: Path) -> None:
    """What the guard compares against: the chunk vectors the pack's index holds."""
    import sqlite3
    from types import SimpleNamespace

    from chaoscypher_core.benchmark.reference import ReferencePack

    db = tmp_path / "index" / "databases" / "app" / "app.db"
    pack = SimpleNamespace(index_db_path=db)
    assert ReferencePack.indexed_chunk_count(pack) == 0  # type: ignore[arg-type]
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE vec_search_chunks_rowids (rowid INTEGER PRIMARY KEY, id TEXT)")
        con.executemany("INSERT INTO vec_search_chunks_rowids (id) VALUES (?)", [("a",), ("b",)])
    assert ReferencePack.indexed_chunk_count(pack) == 2  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"retrieval_stats": {"mode": "vector_only", "faults": []}}, "mode 'vector_only'"),
        ({"retrieval_stats": {"mode": "keyword_only", "faults": []}}, "mode 'keyword_only'"),
        ({"graph_context": {}}, "an empty graph context"),
        (
            {"retrieval_stats": {"mode": "full_graphrag", "faults": ["ppr", "vector_search"]}},
            "swallowed exceptions in ppr, vector_search",
        ),
        ({"retrieval_stats": {"mode": "full_graphrag"}}, "no fault record"),
    ],
)
def test_a_degraded_graphrag_retrieval_is_refused_although_it_fills_its_budget(
    change: dict[str, Any], why: str
) -> None:
    """A fallback, a lost graph context or a swallowed exception is never pinned (audit wave 2).

    The chunk count alone passed all of these: the vector_only fallback fills
    the budget from the hybrid arm.
    """
    from chaoscypher_core.benchmark.retrieval import (
        DegradedRetrievalError,
        require_complete_retrieval,
    )

    complete = {
        "chunks": [{"chunk_id": str(i)} for i in range(10)],
        "graph_context": {"seed_entities": [{"id": "n1"}]},
        "retrieval_stats": {"mode": "full_graphrag", "faults": []},
    }
    with pytest.raises(DegradedRetrievalError, match=why):
        require_complete_retrieval(
            {**complete, **change}, mode="graphrag", chunk_limit=10, indexed_chunks=179
        )
    # the naive rung carries no graph context and no stats: the count is its check
    require_complete_retrieval(
        {"chunks": complete["chunks"]}, mode="naive", chunk_limit=10, indexed_chunks=179
    )


@pytest.mark.asyncio
async def test_the_benchmark_handlers_are_wired_as_the_product_wires_them() -> None:
    """One builder for every benchmark retrieval (the chat stage, the MCP suite, the reach tools).

    The storage adapter serves the chunk hydration and the citations; the
    engine's embedder unless one is given; the shape router's model when given.
    """
    from types import SimpleNamespace

    from chaoscypher_core.benchmark.retrieval import retrieval_handlers
    from chaoscypher_core.settings import EngineSettings

    async def embed(text: str) -> list[float]:
        return [0.0]

    async def cached(text: str) -> list[float]:
        return [1.0]

    async def router(*args: object, **kwargs: object) -> dict[str, str]:
        return {"content": "{}"}

    settings, other = EngineSettings(), EngineSettings()
    engine = SimpleNamespace(
        graph_repository=object(),
        search_repository=object(),
        storage_adapter=object(),
        embedding_service=SimpleNamespace(embed=embed),
        settings=settings,
        database_name="db",
    )
    h = retrieval_handlers(engine)
    assert (h.graph, h.search, h.indexing, h.source_storage) == (
        engine.graph_repository,
        engine.search_repository,
        engine.storage_adapter,
        engine.storage_adapter,
    )
    assert h.embedding_callback is not None
    assert await h.embedding_callback("q") == [0.0]  # the engine's embedder
    assert (h._settings, h.database_name, h.chat_callback) == (settings, "db", None)
    h = retrieval_handlers(engine, settings=other, embedding_callback=cached, chat_callback=router)
    assert h.embedding_callback is not None
    assert await h.embedding_callback("q") == [1.0]
    assert (h._settings, h.chat_callback) == (other, router)
