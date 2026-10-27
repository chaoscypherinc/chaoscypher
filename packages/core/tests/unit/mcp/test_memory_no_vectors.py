# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The memory tools with vector search off: no embedding model is built or called, writes stay keyword-indexed.

Every test runs with ``search.enable_vector_search = false`` and an embedder that fails the
test on any call (``no_embedder``): the factory and every concrete provider's ``embed`` and
``batch_embed`` raise.
"""

from __future__ import annotations

import socket
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.adapters.embedding import (
    GeminiEmbeddingProvider,
    LocalEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    factory,
)
from chaoscypher_core.adapters.sqlite.repos import search as search_repo_module
from chaoscypher_core.exceptions import VectorSearchOff
from chaoscypher_core.mcp.memory import memory_handle
from tests.unit.mcp.memory_world import (
    THREE_MEMORIES,
    build_world,
    call,
    call_raw,
    engine_at,
    memory_sources,
    settings,
)


def _embedded(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("embedded behind the switch")


@pytest.fixture
def no_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "create_embedding_provider", _embedded)
    # every other name of create_embedding_provider builds through this one function
    monkeypatch.setattr(factory, "_build_embedding_provider", _embedded)
    for provider in (
        LocalEmbeddingProvider,
        OllamaEmbeddingProvider,
        OpenAIEmbeddingProvider,
        GeminiEmbeddingProvider,
    ):
        monkeypatch.setattr(provider, "embed", _embedded)
        monkeypatch.setattr(provider, "batch_embed", _embedded)


@pytest.fixture
def world_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_embedder: None
) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, s=settings(vectors=False))
    yield w
    w.engine.close()


MERGE_SCRIPT = {
    "Pierre moved to Lyon.": (
        "E|Pierre|Person||0.9|S1|A man\nE|Lyon|Place||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|He moved to Lyon\n",
    ),
    "Pierre Bezukhov inherited a fortune.": (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count who inherited a fortune\n",
        "",
    ),
}


def test_vectors_off_builds_no_embedding_provider(tmp_path: Path, no_embedder: None) -> None:
    engine = engine_at(tmp_path, settings(vectors=False))
    try:
        assert engine.embedding_service is None
        assert engine.embedding_provider is None
        assert engine.commit_service._get_embedding_provider() is None
        assert engine.extraction_service.embedding_service is None
        assert engine.indexing_service.embedding_service is None
        assert engine.search_repository.vectors is False
    finally:
        engine.close()


async def test_remember_with_vectors_off_makes_no_embedding_call(
    world_off: SimpleNamespace,
) -> None:
    text = await call(
        world_off.server, "remember", {"text": "Pierre Bezukhov moved to Lyon.", "by": "alice"}
    )
    assert "Linked: " in text
    assert "Pierre Bezukhov (Person)" in text and "Lyon (Location)" in text
    assert "Facts: Pierre Bezukhov located_in Lyon" in text


async def test_a_closed_port_ollama_env_with_vectors_off_never_dials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = settings(vectors=False)
    s.embedding.provider = "ollama"
    s.embedding.api_base = "http://127.0.0.1:9"
    dialled: list[Any] = []

    def _no_network(_sock: Any, address: Any) -> Any:
        dialled.append(address)
        raise AssertionError(f"dialled {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)
    world = build_world(tmp_path, monkeypatch, s=s)
    try:
        for name, args in (
            ("remember", {"text": "Pierre Bezukhov moved to Lyon."}),
            ("recall", {"query": "Where does Pierre live?"}),
            ("forget", {"query": "Lyon"}),
            ("digest", {}),
        ):
            text, is_error = await call_raw(world.server, name, args)
            assert not is_error, (name, text)
    finally:
        world.engine.close()
    assert dialled == []


async def test_recall_forget_and_digest_with_vectors_off_make_no_embedding_call(
    world_off: SimpleNamespace,
) -> None:
    for args in THREE_MEMORIES:
        answer = await call(world_off.server, "remember", args)
        assert "not linked" not in answer and "Facts: " in answer, answer

    recalled = await call(world_off.server, "recall", {"query": "Where does Pierre live?"})
    assert recalled.splitlines()[0] == "Pierre Bezukhov (Person)"
    assert "Pierre Bezukhov located_in Lyon" in recalled

    rows = memory_sources(world_off.storage)
    ids = [r["id"] for r in rows]
    lyon = next(
        r["id"]
        for r in rows
        if world_off.storage.get_chunks_by_source(r["id"], page_size=1)[0][0]["content"]
        == "Pierre Bezukhov moved to Lyon."
    )
    handle = memory_handle(lyon, ids)
    with capture_logs() as logs:
        forgotten = await call(world_off.server, "forget", {"handle": handle, "confirm": True})
    assert forgotten.startswith("Forgotten ")
    assert not [e for e in logs if e["event"].endswith("_non_fatal")]

    assert await call(world_off.server, "digest", {})


async def test_an_identity_merge_with_vectors_off_reindexes_the_kept_node_for_keywords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_embedder: None
) -> None:
    world = build_world(tmp_path, monkeypatch, script=MERGE_SCRIPT, s=settings(vectors=False))
    try:
        with capture_logs() as logs:
            await call(world.server, "remember", {"text": "Pierre moved to Lyon.", "by": "alice"})
            await call(
                world.server,
                "remember",
                {"text": "Pierre Bezukhov inherited a fortune.", "by": "alice"},
            )
        pierres = [
            n for n in world.graph.list_nodes(include_embedding=False) if "Pierre" in n.label
        ]
        assert len(pierres) == 1
        hits = world.search.keyword_search("Bezukhov", item_type="node")
        assert [i for i, _ in hits] == [pierres[0].id]
        hits = world.search.keyword_search("Pierre", item_type="node")
        assert [i for i, _ in hits] == [pierres[0].id]
        assert not [e for e in logs if e["event"].endswith("_failed_non_fatal")]
    finally:
        world.engine.close()


async def test_engine_search_with_vectors_off_is_keyword_only(
    world_off: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await call(world_off.server, "remember", {"text": "Pierre Bezukhov moved to Lyon."})
    faults: list[str] = []
    monkeypatch.setattr(search_repo_module, "record_retrieval_fault", faults.append)

    hybrid = await world_off.engine.search("Lyon")
    keyword = await world_off.engine.search("Lyon", mode="keyword")

    # keyword mode reads the node index; hybrid's keyword arm adds the chunk (scores differ)
    assert keyword
    assert [r.id for r in hybrid if r.result_type == "node"] == [r.id for r in keyword]
    assert [r.result_type for r in hybrid if r.result_type != "node"] == ["chunk"]
    assert faults == []


async def test_semantic_search_with_vectors_off_is_refused(world_off: SimpleNamespace) -> None:
    with pytest.raises(VectorSearchOff, match="Semantic search needs vectors"):
        await world_off.engine.search("Lyon", mode="semantic")
    with pytest.raises(VectorSearchOff, match="Embedding needs vectors"):
        await world_off.engine.embed("Lyon")
    with pytest.raises(VectorSearchOff, match="Embedding needs vectors"):
        await world_off.engine.batch_embed(["Lyon"])
