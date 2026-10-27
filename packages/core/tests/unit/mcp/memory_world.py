# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The memory tools' test world: a real engine, a scripted extraction model, the in-process server.

Shared by the memory tool tests (``test_memory_*.py``): one engine on a temporary directory,
the embedder faked, the extraction model scripted per text (``tests/fakes/scripted_llm.py``),
every call driven through the MCP server's ``CallToolRequest`` handler the way a host calls it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import mcp.types
import pytest

from chaoscypher_core import Engine
from chaoscypher_core.mcp.memory_link import MemoryLinker
from chaoscypher_core.mcp.server import create_mcp_server
from chaoscypher_core.models import MEMORY_SOURCE_TYPE, MemoryLinkState
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider
from tests.fakes.scripted_llm import Script, ScriptedExtractionLLM


DIMS = 8

# The relation labels are the generic domain's (a model reads them off the prompt): a memory
# about where someone lives is `located_in`, a friendship `friend_of`.
WORLD_SCRIPT: dict[str, Script] = {
    "Pierre Bezukhov moved to Lyon.": (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Lyon|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Lyon\n",
    ),
    "Pierre Bezukhov is a friend of Anna Pavlovna.": (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
        "E|Anna Pavlovna|Person|Anna|0.9|S1|A hostess\n",
        "R|0|1|friend_of|0.9|S1|They are friends\n",
    ),
    "Anna Pavlovna moved to Paris.": (
        "E|Anna Pavlovna|Person|Anna|0.9|S1|A hostess\nE|Paris|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Anna Pavlovna moved to Paris\n",
    ),
    "Pierre Bezukhov was born in Moscow.": (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Moscow|Location||0.9|S1|A city\n",
        "R|0|1|born_in|0.9|S1|Pierre Bezukhov was born in Moscow\n",
    ),
    "The weather was grey.": ("", ""),
    "Boris Drubetskoy walked through the garden": (
        "E|Boris Drubetskoy|Person|Boris|0.9|S1|An officer\n",
        "",
    ),
}

THREE_MEMORIES: tuple[dict[str, Any], ...] = (
    {"text": "Pierre Bezukhov moved to Lyon.", "by": "alice", "valid_from": "2026-10-20"},
    {
        "text": "Pierre Bezukhov is a friend of Anna Pavlovna.",
        "by": "alice",
        "valid_from": "2026-10-19",
    },
    {"text": "Anna Pavlovna moved to Paris.", "by": "bob", "valid_from": "2026-10-18"},
)
DOCUMENT_TEXT = "Pierre Bezukhov was born in Moscow."


def settings(*, mode: str = "write", vectors: bool = True) -> EngineSettings:
    s = EngineSettings()
    s.search.vector_dimensions = DIMS
    s.search.enable_vector_search = vectors
    s.mcp.mode = mode  # type: ignore[assignment]
    return s


def engine_at(tmp_path: Path, s: EngineSettings) -> Engine:
    with patch(
        "chaoscypher_core.adapters.embedding.factory.create_embedding_provider",
        return_value=FakeEmbeddingProvider(dimensions=DIMS),
    ):
        return Engine(str(tmp_path / "databases" / "test"), settings=s)


def build_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    script: dict[str, Script] | None = None,
    s: EngineSettings | None = None,
) -> SimpleNamespace:
    """An engine, its server, its memory linker and the scripted model; ``engine.close()`` it."""
    llm = ScriptedExtractionLLM(script if script is not None else WORLD_SCRIPT)
    monkeypatch.setattr(AIEntityExtractor, "_get_llm_provider", lambda self: llm)
    engine = engine_at(tmp_path, s or settings())
    linker = MemoryLinker(engine)
    return SimpleNamespace(
        engine=engine,
        server=create_mcp_server(engine, linker=linker),
        linker=linker,
        llm=llm,
        graph=engine.graph_repository,
        search=engine.search_repository,
        storage=engine.storage_adapter,
    )


async def call_raw(server: Any, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    handler = server.request_handlers[mcp.types.CallToolRequest]
    request = mcp.types.CallToolRequest(
        method="tools/call",
        params=mcp.types.CallToolRequestParams(name=name, arguments=args),
    )
    result = await handler(request)
    return result.root.content[0].text, bool(result.root.isError)


async def call(server: Any, name: str, args: dict[str, Any]) -> str:
    text, is_error = await call_raw(server, name, args)
    assert not is_error, text
    return text


async def list_tools(server: Any) -> list[mcp.types.Tool]:
    handler = server.request_handlers[mcp.types.ListToolsRequest]
    result = await handler(mcp.types.ListToolsRequest(method="tools/list"))
    return list(result.root.tools)


def memory_sources(storage: Any) -> list[dict[str, Any]]:
    rows, _ = storage.list_sources(source_type="memory", page_size=50)
    return rows


async def remember_three_and_a_document(world: SimpleNamespace) -> None:
    """The fixture graph of the recall tests: three memories and one document."""
    for args in THREE_MEMORIES:
        await call(world.server, "remember", args)
    await world.engine.process_document(DOCUMENT_TEXT, filename="book.txt")


async def store_pending(
    engine: Engine, text: str, valid_from: str = "2026-10-20", *, scope: str | None = None
) -> str:
    """A memory stored and not linked: its row (state pending, its text kept) and its chunk."""
    result = await engine.process_document(
        text,
        filename=f"memory-{valid_from}.txt",
        forced_domain="generic",
        source_type=MEMORY_SOURCE_TYPE,
        source_fields={
            "memory_by": "alice",
            "memory_scope": scope,
            "valid_from": valid_from,
            "full_text": text,
            "memory_link_state": MemoryLinkState.PENDING.value,
        },
        extract=False,
    )
    return result.source_id


async def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    """Wait until ``check()`` holds, failing the test after ``seconds``."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)
