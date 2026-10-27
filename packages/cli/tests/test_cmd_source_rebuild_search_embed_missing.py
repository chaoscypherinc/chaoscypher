# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher source rebuild-search --embed-missing``: vectors for the rows written keyword-only."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.source import rebuild_search as cmd
from chaoscypher_cli.commands.source.rebuild_search import rebuild_search
from chaoscypher_core import Engine
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider
from chaoscypher_core.utils.id import generate_hex_id


DIMS = 8


def _engine(path: Path, *, vectors: bool) -> Engine:
    settings = EngineSettings()
    settings.search.enable_vector_search = vectors
    if vectors:
        settings.search.vector_dimensions = DIMS
    with patch(
        "chaoscypher_core.adapters.embedding.factory.create_embedding_provider",
        return_value=FakeEmbeddingProvider(dimensions=DIMS),
    ):
        return Engine(str(path / "databases" / "test"), settings=settings)


async def _remember(engine: Engine, text: str) -> str:
    source_id = generate_hex_id()
    await engine.process_document(
        text,
        source_id=source_id,
        filename="memory.txt",
        auto_confirm=True,
        source_type="memory",
        extract=False,
    )
    return source_id


def _as_context(engine: Engine) -> SimpleNamespace:
    return SimpleNamespace(
        search_repository=engine.search_repository,
        graph_repository=engine.graph_repository,
        storage_adapter=engine.storage_adapter,
        settings=engine.settings,
        embedding_service=engine.embedding_service,
    )


def _chunk_embedding(engine: Engine, source_id: str) -> Any:
    engine.storage_adapter.session.expire_all()
    (chunk,), _ = engine.storage_adapter.get_chunks_by_source(source_id, include_embeddings=True)
    embedding = chunk["embedding"]
    return base64.b64encode(embedding).decode() if isinstance(embedding, bytes) else embedding


@pytest.fixture
async def mixed(tmp_path: Path) -> AsyncGenerator[SimpleNamespace]:
    """Two memories and a node written keyword-only, then one memory with vector search on."""
    off = _engine(tmp_path, vectors=False)
    await _remember(off, "Lise sings.")
    await _remember(off, "Anna keeps bees in Lyon.")
    off.add_node("Person", "Lise")
    off.close()

    on = _engine(tmp_path, vectors=True)
    pierre = await _remember(on, "Pierre Bezukhov moved to Lyon.")
    yield SimpleNamespace(on=on, pierre=pierre, path=tmp_path)
    on.close()


def test_embed_missing_fills_only_the_rows_without_vectors(
    mixed: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    on = mixed.on
    before = _chunk_embedding(on, mixed.pierre)
    assert before is not None
    monkeypatch.setattr(cmd, "get_context", lambda **_kw: _as_context(on))

    result = CliRunner().invoke(rebuild_search, ["--embed-missing"])
    assert result.exit_code == 0, result.output
    assert "Embedding what has no vector yet..." in result.output
    assert "Chunks embedded: 2" in result.output
    assert "Nodes embedded: 1" in result.output
    assert _chunk_embedding(on, mixed.pierre) == before

    again = CliRunner().invoke(rebuild_search, ["--embed-missing"])
    assert again.exit_code == 0, again.output
    assert "Chunks embedded: 0" in again.output and "Nodes embedded: 0" in again.output


def test_embed_missing_with_vector_search_off_is_refused(
    mixed: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = _engine(mixed.path, vectors=False)
    try:
        monkeypatch.setattr(cmd, "get_context", lambda **_kw: _as_context(off))
        refused = CliRunner().invoke(rebuild_search, ["--embed-missing"])
        assert refused.exit_code == 1
        assert (
            "rebuild-search --embed-missing needs vectors, and vector search is off"
            in refused.output
        )
    finally:
        off.close()
