# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``Engine.chunk_and_index`` and ``Engine.extract_and_commit``: the stages a stored source is linked by.

``process_document`` is chunk-and-index followed by extract-and-commit; a memory whose link
has to wait is stored first and extracted later from its stored chunks, through the same
two steps. These tests pin that the later extraction builds the graph the one-call path
builds, and that a model failing on every group is reported when asked (``strict``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from chaoscypher_core import Engine
from chaoscypher_core.exceptions import ExtractionIncomplete, LLMError
from chaoscypher_core.mcp.indexing import index_source_text
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from tests.fakes.scripted_llm import ScriptedExtractionLLM
from tests.unit.mcp.memory_world import WORLD_SCRIPT, engine_at, settings


TEXT = "Pierre Bezukhov was born in Moscow."


def _model(monkeypatch: pytest.MonkeyPatch, **hooks: Any) -> ScriptedExtractionLLM:
    llm = ScriptedExtractionLLM(WORLD_SCRIPT, **hooks)
    monkeypatch.setattr(AIEntityExtractor, "_get_llm_provider", lambda self: llm)
    return llm


def _graph(engine: Engine) -> tuple[list[tuple[str, str | None]], list[str]]:
    graph = engine.graph_repository
    nodes = sorted((n.label, n.entity_type) for n in graph.list_nodes(include_embedding=False))
    edges = sorted(e.label for e in graph.list_edges())
    return nodes, edges


async def test_extract_and_commit_from_stored_chunks_builds_the_graph_process_document_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(monkeypatch)
    one_call = engine_at(tmp_path / "a", settings())
    later = engine_at(tmp_path / "b", settings())
    try:
        await one_call.process_document(TEXT, filename="book.txt")
        stored = await later.process_document(TEXT, filename="book.txt", extract=False)
        result = await later.extract_and_commit(stored.source_id)
        assert result.nodes
        expected = _graph(one_call)
        assert expected[0] and expected[1]
        assert _graph(later) == expected
    finally:
        one_call.close()
        later.close()


async def test_process_document_passes_its_own_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(monkeypatch)
    engine = engine_at(tmp_path, settings())
    seen: dict[str, Any] = {}
    real_chunk = Engine.chunk_and_index
    real_extract = Engine.extract_and_commit

    async def chunk(self: Engine, *args: Any, **kwargs: Any) -> Any:
        seen["chunks"] = result = await real_chunk(self, *args, **kwargs)
        return result

    async def extract(self: Engine, *args: Any, **kwargs: Any) -> Any:
        seen["kwargs"] = kwargs
        return await real_extract(self, *args, **kwargs)

    try:
        with (
            patch.object(Engine, "chunk_and_index", chunk),
            patch.object(Engine, "extract_and_commit", extract),
        ):
            await engine.process_document(TEXT, filename="book.txt")
        chunks_result, _ = seen["chunks"]
        assert seen["kwargs"]["groups"] is chunks_result.hierarchical_groups
        assert seen["kwargs"]["small_chunks"] is chunks_result.small_chunks
    finally:
        engine.close()


async def test_strict_extraction_raises_when_the_model_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(monkeypatch, raise_on={TEXT: LLMError("the model is not answering")})
    engine = engine_at(tmp_path, settings())
    try:
        strict = await engine.process_document(TEXT, filename="a.txt", extract=False)
        with pytest.raises(ExtractionIncomplete) as raised:
            await engine.extract_and_commit(strict.source_id, strict=True)
        assert raised.value.details == {"failed_groups": 1, "groups": 1}
        assert TEXT not in str(raised.value)
        lenient = await engine.process_document(TEXT, filename="b.txt", extract=False)
        result = await engine.extract_and_commit(lenient.source_id)
        assert result.nodes == []
        source = engine.storage_adapter.get_source(lenient.source_id, engine.database_name)
        assert source is not None and source["commit_complete"]
    finally:
        engine.close()


async def test_chunk_and_index_is_the_one_store_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(monkeypatch)
    engine = engine_at(tmp_path, settings())
    calls: list[str] = []
    real_chunk = Engine.chunk_and_index

    async def chunk(self: Engine, source_id: str, text: str) -> Any:
        calls.append(source_id)
        return await real_chunk(self, source_id, text)

    try:
        with patch.object(Engine, "chunk_and_index", chunk):
            processed = await engine.process_document(TEXT, filename="book.txt", extract=False)
            engine.storage_adapter.create_source(
                {
                    "id": "page",
                    "database_name": engine.database_name,
                    "filename": "page.txt",
                    "filepath": "page.txt",
                    "status": "indexing",
                }
            )
            await index_source_text(
                engine,
                file_id="page",
                file_path="page.txt",
                is_url=True,
                text="Anna Pavlovna moved to Paris.",
                file_type="txt",
                enable_normalization=False,
            )
        assert calls == [processed.source_id, "page"]
    finally:
        engine.close()
