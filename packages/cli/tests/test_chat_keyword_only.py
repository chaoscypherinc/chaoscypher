# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher chat`` with vector search off: the tools search by keyword, nothing is embedded."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_cli.commands.chat import _create_tool_infrastructure
from chaoscypher_core import Engine
from chaoscypher_core.adapters.embedding import factory
from chaoscypher_core.repo_factories import embedding_factory
from chaoscypher_core.settings import EngineSettings


def _built(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("an embedding provider was built behind the vector switch")


@pytest.fixture
async def keyword_only(
    tmp_path: Path, isolated_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SimpleNamespace]:
    # every name of create_embedding_provider builds through this one function
    monkeypatch.setattr(factory, "_build_embedding_provider", _built)
    monkeypatch.setattr(embedding_factory, "_embedding_service", None)  # no cached singleton
    settings = EngineSettings()
    settings.search.enable_vector_search = False
    engine = Engine(str(tmp_path / "databases" / "test"), settings=settings)
    engine.add_node("Place", "Lyon")
    yield SimpleNamespace(
        engine=engine,
        graph_repository=engine.graph_repository,
        search_repository=engine.search_repository,
        storage_adapter=engine.storage_adapter,
        embedding_service=engine.embedding_service,
        llm_provider=None,
    )
    engine.close()


async def test_keyword_only_chat_searches_without_an_embedder(
    keyword_only: SimpleNamespace,
) -> None:
    _, tool_executor, tools = _create_tool_infrastructure(keyword_only)

    assert tools
    result = await tool_executor.execute_tool("search_nodes", {"query": "Lyon"})

    assert result.get("success", True), result
    assert "Lyon" in str(result)
