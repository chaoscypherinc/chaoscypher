# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher memory relink``: link the memories not linked yet, in the foreground.

The command runs the start-up sweep's link step over every memory not linked yet, oldest
first, ignoring the backoff, and prints counts only, never a memory's text. It exits 0
when nothing is left unlinked and 3 when something is.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import mcp.types
import pytest
import structlog.testing
from click.testing import CliRunner, Result

from chaoscypher_core import Engine
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.mcp.memory_link import link_repository
from chaoscypher_core.mcp.server import create_mcp_server
from chaoscypher_core.models import MEMORY_SOURCE_TYPE, MemoryLinkState
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_GRAPH_HEADER,
)
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.extraction_llm import ScriptedExtractionLLM


LYON = "Pierre Bezukhov moved to Lyon."
GREY = "The weather was grey."
PIERRE = "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
LYON_ENTITIES = PIERRE + "E|Lyon|Location||0.9|S1|A city\n"
LYON_EDGE = "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Lyon\n"


def _engine(path: Path) -> Engine:
    """A write-mode engine with no embedder (vector search off)."""
    settings = EngineSettings()
    settings.search.enable_vector_search = False
    settings.mcp.mode = "write"
    return Engine(str(path / "databases" / "test"), settings=settings)


@pytest.fixture
async def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[Engine]:
    engine = _engine(tmp_path)
    cmd = importlib.import_module("chaoscypher_cli.commands.memory.relink")
    monkeypatch.setattr(cmd, "get_context", lambda **_kw: SimpleNamespace(_engine=engine))
    yield engine
    engine.close()


async def _model_raises(*_args: Any, **_kw: Any) -> Any:
    raise LLMError(f"the model is not answering: {LYON}")


async def _call(engine: Engine, name: str, arguments: dict[str, Any]) -> None:
    """One tool call through the server, as an agent makes it."""
    handler = create_mcp_server(engine).request_handlers[mcp.types.CallToolRequest]
    request = mcp.types.CallToolRequest(
        method="tools/call",
        params=mcp.types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(request)
    assert not result.root.isError, result.root.content


async def _remember(engine: Engine, text: str, scope: str | None = None) -> str:
    """``remember`` through the server, as an agent calls it; the memory's source id."""
    await _call(engine, "remember", {"text": text} | ({"scope": scope} if scope else {}))
    rows = link_repository(engine).link_rows(engine.database_name)
    return rows[-1].id


async def _stored(engine: Engine, text: str, valid_from: str, scope: str | None = None) -> str:
    """A memory stored and not linked yet (state pending, its text kept)."""
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


async def _relink(*args: str) -> Result:
    from chaoscypher_cli.commands.memory import memory

    # the engine's logs go to stdout under the default structlog config; the command's own
    # output is what is checked, so the logs are captured away from it
    with structlog.testing.capture_logs():
        return await asyncio.to_thread(CliRunner().invoke, memory, ["relink", *args])


def _state(engine: Engine, source_id: str) -> MemoryLinkState | None:
    row = link_repository(engine).read_link_row(source_id)
    assert row is not None
    return row.state


def _no_text(output: str, *texts: str) -> None:
    """The output names no word of any memory (counts only)."""
    words = {w.strip(".,").lower() for text in texts for w in text.split()}
    printed = {w.strip(".,()").lower() for w in output.split()}
    assert not (printed & (words - {"linked", "the"})), output


async def test_a_failed_remember_is_linked_by_the_command(engine: Engine) -> None:
    with patch.object(AIEntityExtractor, "call_llm", _model_raises):
        source_id = await _remember(engine, LYON)
    assert _state(engine, source_id) is MemoryLinkState.FAILED

    llm = ScriptedExtractionLLM(pass1_answers=[LYON_ENTITIES], pass2_answers=[LYON_EDGE])
    with patch.object(AIEntityExtractor, "call_llm", llm):
        result = await _relink()
    assert result.exit_code == 0, result.output
    assert result.output == (
        "Linked 1, linked empty 0, still pending 0 (model failed), no model 0, "
        "linking elsewhere 0.\n"
    )
    _no_text(result.output, LYON)
    assert _state(engine, source_id) is MemoryLinkState.LINKED


async def test_relink_exits_3_while_something_stays_pending(engine: Engine) -> None:
    with patch.object(AIEntityExtractor, "call_llm", _model_raises):
        source_id = await _remember(engine, LYON)
        result = await _relink()
    assert result.exit_code == 3, result.output
    assert "still pending 1 (model failed)" in result.output
    _no_text(result.output, LYON)
    row = link_repository(engine).read_link_row(source_id)
    assert row is not None
    assert (row.state, row.recovery_attempts) == (MemoryLinkState.FAILED, 2)


async def test_dry_run_prints_counts_and_links_nothing(engine: Engine) -> None:
    ids = [
        await _stored(engine, LYON, "2026-10-19"),
        await _stored(engine, GREY, "2026-10-20"),
    ]
    llm = ScriptedExtractionLLM()
    with patch.object(AIEntityExtractor, "call_llm", llm):
        result = await _relink("--dry-run")
    assert result.exit_code == 0, result.output
    assert result.output == (
        "Not linked yet: 2 (2 pending, 0 linking now, 0 interrupted, 0 no model, 0 failed). "
        "Linked empty: 0.\n"
    )
    _no_text(result.output, LYON, GREY)
    assert llm.prompts == []
    assert [_state(engine, i) for i in ids] == [MemoryLinkState.PENDING] * 2


async def test_limit_links_the_oldest_first(engine: Engine) -> None:
    oldest = await _stored(engine, LYON, "2026-10-18")
    others = [
        await _stored(engine, "Boris Drubetskoy walked through the garden.", "2026-10-19"),
        await _stored(engine, GREY, "2026-10-20"),
    ]
    llm = ScriptedExtractionLLM(pass1_answers=[LYON_ENTITIES], pass2_answers=[LYON_EDGE])
    with patch.object(AIEntityExtractor, "call_llm", llm):
        result = await _relink("--limit", "1")
    assert result.exit_code == 3, result.output
    assert result.output.startswith("Linked 1, linked empty 0,")
    assert _state(engine, oldest) is MemoryLinkState.LINKED
    assert [_state(engine, i) for i in others] == [MemoryLinkState.PENDING] * 2


async def test_relink_include_empty_retries_linked_empty_memories(engine: Engine) -> None:
    source_id = await _stored(engine, GREY, "2026-10-20")
    first = ScriptedExtractionLLM(pass1_answers=[""])
    with patch.object(AIEntityExtractor, "call_llm", first):
        result = await _relink()
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Linked 0, linked empty 1,")
    assert _state(engine, source_id) is MemoryLinkState.LINKED_EMPTY

    # the model now finds a node in it
    second = ScriptedExtractionLLM(pass1_answers=[PIERRE])
    with patch.object(AIEntityExtractor, "call_llm", second):
        skipped = await _relink()
        assert skipped.output.startswith("Linked 0, linked empty 0,")
        assert second.prompts == []
        result = await _relink("--include-empty")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Linked 1, linked empty 0,")
    assert _state(engine, source_id) is MemoryLinkState.LINKED
    graph = engine.graph_repository
    nodes = graph.get_nodes_batch(graph.member_node_ids(source_id), include_embedding=False)
    assert "Pierre Bezukhov" in {n.label for n in nodes}


async def test_an_error_prints_it_and_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = importlib.import_module("chaoscypher_cli.commands.memory.relink")

    def _no_database(**_kw: Any) -> Any:
        msg = "no such database"
        raise RuntimeError(msg)

    monkeypatch.setattr(cmd, "get_context", _no_database)
    result = await _relink()
    assert result.exit_code == 1
    assert "Error: no such database" in result.output


# --- a scoped memory's relink is offered its scope's nodes ---------------------------

EARLIER = "Opened a long position on the ETH market."
TRADE = "Bought 0.1 coin at the open; wrote it down."
JOURNAL = "Trading journal"
MARKET = "ETH market"
MARKET_ENTITY = f"E|{MARKET}|Concept||0.9|S1|A market\n"
TRADE_ENTITIES = MARKET_ENTITY + f"E|{JOURNAL}|Entity||0.9|S1|A journal\n"


def _offered(prompt: str) -> str:
    """The KNOWN ENTITIES block a memory's pass-1 prompt shows."""
    return prompt.split(KNOWN_GRAPH_HEADER, 1)[1].split("\n\n", 1)[0]


async def _journal(engine: Engine) -> None:
    """The trader scope's journal, made by a caller, and named in ``scope_nodes``."""
    engine.settings.mcp.memory.scope_nodes = {"trader": [JOURNAL]}
    await _call(engine, "create_template", {"name": "Journal", "template_type": "node"})
    await _call(
        engine, "create_node", {"template_id": "Journal", "label": JOURNAL, "properties": {}}
    )


def _member_labels(engine: Engine, source_id: str) -> set[str]:
    graph = engine.graph_repository
    nodes = graph.get_nodes_batch(graph.member_node_ids(source_id), include_embedding=False)
    return {n.label for n in nodes}


async def test_a_failed_scoped_memory_is_relinked_with_its_scopes_nodes(engine: Engine) -> None:
    await _journal(engine)
    with patch.object(AIEntityExtractor, "call_llm", ScriptedExtractionLLM([MARKET_ENTITY])):
        await _remember(engine, EARLIER, scope="trader")
    with patch.object(AIEntityExtractor, "call_llm", _model_raises):
        source_id = await _remember(engine, TRADE, scope="trader")
    assert _state(engine, source_id) is MemoryLinkState.FAILED

    llm = ScriptedExtractionLLM(pass1_answers=[TRADE_ENTITIES])
    with patch.object(AIEntityExtractor, "call_llm", llm):
        result = await _relink()

    assert result.exit_code == 0, result.output
    assert result.output.startswith("Linked 1, linked empty 0,")
    block = _offered(llm.pass1_prompts[0])
    assert f"- {JOURNAL}" in block and f"- {MARKET}" in block
    assert {JOURNAL, MARKET} <= _member_labels(engine, source_id)
    labels = [n.label for n in engine.graph_repository.list_nodes(include_embedding=False)]
    assert labels.count(JOURNAL) == 1 and labels.count(MARKET) == 1


async def test_relink_include_empty_links_a_scoped_memory_once_its_scope_node_exists(
    engine: Engine,
) -> None:
    source_id = await _stored(engine, TRADE, "2026-10-20", scope="trader")
    with patch.object(AIEntityExtractor, "call_llm", ScriptedExtractionLLM([""])):
        result = await _relink()
    assert result.output.startswith("Linked 0, linked empty 1,")

    await _journal(engine)
    llm = ScriptedExtractionLLM(pass1_answers=[f"E|{JOURNAL}|Entity||0.9|S1|A journal\n"])
    with patch.object(AIEntityExtractor, "call_llm", llm):
        result = await _relink("--include-empty")

    assert result.exit_code == 0, result.output
    assert result.output.startswith("Linked 1, linked empty 0,")
    assert f"- {JOURNAL}" in _offered(llm.pass1_prompts[0])
    assert _state(engine, source_id) is MemoryLinkState.LINKED
    assert JOURNAL in _member_labels(engine, source_id)
