# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A scoped memory is seeded with its scope's nodes (spec 2026-10-24 § 4.3, D24).

``build_seed`` offers, in order and within ``extraction.known_entities_limit``: the top
hit of each ``about`` name, the nodes the text names, the nodes the scope's other memories
attest (most attested first), and the nodes ``mcp.memory.scope_nodes`` names for the scope;
the speaker is always last. A memory with no scope gets the first two only.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import structlog.testing

from chaoscypher_core.mcp import memory_seed
from chaoscypher_core.mcp.memory_link import LinkOutcome, link_memory, link_repository
from chaoscypher_core.mcp.memory_relink import RelinkReport, relink_pending
from chaoscypher_core.mcp.memory_seed import build_seed, scope_nodes
from chaoscypher_core.models import MemoryLinkState
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_GRAPH_HEADER,
)
from chaoscypher_core.utils.logging.privacy import WITHHELD, private_text, withhold_private_text
from tests.unit.mcp.memory_world import build_world, call, settings, store_pending


if TYPE_CHECKING:
    from tests.fakes.scripted_llm import Script


EARLIER = "Opened a long position on the ETH market."
TRADE = "Bought 0.1 coin at the open; wrote it down."
"""The trade shares no word (nor stem: "trade" finds "Trading") with either scope node, so
only the scope can offer them; the spec's "Bought 0.1 ETH; logged it in the journal." names
both by keyword, so a memory with no scope is offered them too."""
JOURNAL = "Trading journal"
MARKET = "ETH market"


def _offered(prompt: str) -> str:
    """The KNOWN ENTITIES block of a prompt seeded from the graph ("" when it has none)."""
    if KNOWN_GRAPH_HEADER not in prompt:
        return ""
    return prompt.split(KNOWN_GRAPH_HEADER, 1)[1].split("\n\n", 1)[0]


def _shown_type(block: str, name: str) -> str:
    """The type the KNOWN ENTITIES block shows for ``name``: the model writes it back."""
    line = next(line for line in block.splitlines() if line.startswith(f"- {name} ("))
    return line[len(f"- {name} (") :].split(")", 1)[0]


def _trade_script(prompt: str) -> tuple[str, str]:
    """A model that writes the trade onto the journal and the market when it is shown them."""
    block = _offered(prompt)
    if f"- {JOURNAL}" in block and f"- {MARKET}" in block:
        return (
            f"E|{MARKET}|{_shown_type(block, MARKET)}||0.9|S1|A market\n"
            f"E|{JOURNAL}|{_shown_type(block, JOURNAL)}||0.9|S1|A journal\n",
            "R|1|0|references|0.9|S1|The journal notes the trade\n",
        )
    return ("E|coin|Object||0.9|S1|A coin\n", "")


SCRIPT: dict[str, Script] = {
    EARLIER: (f"E|{MARKET}|Concept||0.9|S1|A market\n", ""),
    TRADE: _trade_script,
}


def _trader_settings(**scope_nodes_by_scope: list[str]) -> Any:
    s = settings()
    s.mcp.memory.scope_nodes = dict(scope_nodes_by_scope)
    return s


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT, s=_trader_settings(trader=[JOURNAL]))
    yield w
    w.engine.close()


async def _trader_graph(world: SimpleNamespace) -> None:
    """The trader scope's graph: a journal a caller made, a market an earlier memory attests."""
    await call(world.server, "create_template", {"name": "Journal", "template_type": "node"})
    await call(
        world.server, "create_node", {"template_id": "Journal", "label": JOURNAL, "properties": {}}
    )
    await call(world.server, "remember", {"text": EARLIER, "scope": "trader"})


def _labels(world: SimpleNamespace) -> list[str]:
    return [n.label for n in world.graph.list_nodes(limit=1000, include_embedding=False)]


async def test_a_trader_scope_memory_links_to_the_scopes_journal_and_market_nodes(
    world: SimpleNamespace,
) -> None:
    await _trader_graph(world)

    answer = await call(world.server, "remember", {"text": TRADE, "scope": "trader"})

    block = _offered(next(p for p in world.llm.prompts if TRADE in p))
    assert f"- {JOURNAL}" in block and f"- {MARKET}" in block
    linked = next(line for line in answer.splitlines() if line.startswith("Linked:"))
    assert JOURNAL in linked and MARKET in linked
    labels = _labels(world)
    assert labels.count(JOURNAL) == 1 and labels.count(MARKET) == 1
    listed = await call(world.server, "list_memories", {"about": JOURNAL})
    assert TRADE in listed


async def test_the_same_memory_without_a_scope_is_offered_neither_scope_node(
    world: SimpleNamespace,
) -> None:
    await _trader_graph(world)

    await call(world.server, "remember", {"text": TRADE})

    prompt = next(p for p in world.llm.prompts if TRADE in p)
    assert JOURNAL not in prompt and MARKET not in prompt


async def test_a_relinked_scoped_memory_is_seeded_with_its_scopes_nodes(
    world: SimpleNamespace,
) -> None:
    await _trader_graph(world)
    source_id = await store_pending(world.engine, TRADE, scope="trader")

    assert await relink_pending(world.engine, honour_backoff=False) == RelinkReport(linked=1)

    block = _offered(next(p for p in world.llm.prompts if TRADE in p))
    assert f"- {JOURNAL}" in block and f"- {MARKET}" in block
    graph = world.graph
    members = graph.get_nodes_batch(graph.member_node_ids(source_id), include_embedding=False)
    assert {JOURNAL, MARKET} <= {n.label for n in members}


async def test_relink_include_empty_links_a_scoped_memory_once_its_scope_node_exists(
    world: SimpleNamespace,
) -> None:
    """The scope variant of ``test_relink_include_empty_retries_linked_empty_memories``."""

    def journal_once_offered(prompt: str) -> tuple[str, str]:
        """Nothing until the journal is offered; then the trade's entities on either pass.

        The script alternates passes per text, and a pass 1 that finds nothing has no
        pass 2, so the relink's pass 1 is the text's second call.
        """
        if f"- {JOURNAL}" not in _offered(prompt):
            return ("", "")
        entities = _trade_script(prompt)[0]
        return (entities, entities)

    world.llm.script[TRADE] = journal_once_offered
    await call(world.server, "remember", {"text": EARLIER, "scope": "trader"})
    source_id = await store_pending(world.engine, TRADE, scope="trader")
    assert await relink_pending(world.engine, honour_backoff=False) == RelinkReport(linked_empty=1)

    await call(world.server, "create_template", {"name": "Journal", "template_type": "node"})
    await call(
        world.server, "create_node", {"template_id": "Journal", "label": JOURNAL, "properties": {}}
    )
    assert await relink_pending(world.engine, honour_backoff=False) == RelinkReport()
    report = await relink_pending(world.engine, honour_backoff=False, include_empty=True)

    assert report == RelinkReport(linked=1)
    row = link_repository(world.engine).read_link_row(source_id)
    assert row is not None and row.state is MemoryLinkState.LINKED
    graph = world.graph
    members = graph.get_nodes_batch(graph.member_node_ids(source_id), include_embedding=False)
    assert JOURNAL in {n.label for n in members}


async def test_a_memory_whose_follow_ups_failed_joins_the_journal_once_finished(
    world: SimpleNamespace,
) -> None:
    """The step after a commit that stood (``finish_memory``) joins the offered journal too."""
    from chaoscypher_core.services.graph import resolution

    await _trader_graph(world)
    source_id = await store_pending(world.engine, TRADE, scope="trader")
    real = resolution.resolve_graph
    calls = {"n": 0}

    def fails_once(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("identity pass failed")
        return real(*args, **kwargs)

    with patch.object(resolution, "resolve_graph", fails_once):
        assert await link_memory(world.engine, source_id) is LinkOutcome.FAILED
        assert _labels(world).count(JOURNAL) == 2
        assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED

    assert _labels(world).count(JOURNAL) == 1


async def test_a_typed_node_written_with_another_type_is_not_joined(
    world: SimpleNamespace,
) -> None:
    """Only a node with no type (one a person made) is joined by its name: a typed one
    keeps the resolver's type gate, so "ETH market" the market is not "ETH market" a firm.
    """
    await call(world.server, "remember", {"text": EARLIER, "scope": "trader"})
    world.llm.script[TRADE] = (f"E|{MARKET}|Organization||0.9|S1|A trading firm\n", "")

    await call(world.server, "remember", {"text": TRADE, "scope": "trader"})

    assert _labels(world).count(MARKET) == 2


# --- the seed's order and size ----------------------------------------------------


async def _scope_with(world: SimpleNamespace, scope: str, memories: dict[str, list[str]]) -> None:
    """Link one memory per text in ``scope``, each attesting the labels it maps to."""
    for i, (text, labels) in enumerate(memories.items()):
        world.llm.script[text] = (
            "".join(f"E|{label}|Concept||0.9|S1|A thing\n" for label in labels),
            "",
        )
        source_id = await store_pending(world.engine, text, f"2026-10-{10 + i}", scope=scope)
        assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED


async def test_a_scope_offers_its_most_attested_nodes_first(world: SimpleNamespace) -> None:
    await _scope_with(
        world,
        "trader",
        {
            "First note of the day.": ["Alpha", "Bravo"],
            "Second note of the day.": ["Bravo", "Charlie"],
            "Third note of the day.": ["Bravo", "Charlie"],
        },
    )
    world.engine.settings.mcp.memory.scope_nodes = {}

    nodes = scope_nodes(world.engine, "trader")

    assert [n.label for n in nodes] == ["Bravo", "Charlie", "Alpha"]


async def test_the_texts_own_names_come_before_the_scopes(world: SimpleNamespace) -> None:
    await _scope_with(
        world,
        "trader",
        {
            "First note of the day.": ["Alpha", "Bravo", "Charlie"],
            "Second note of the day.": ["Alpha", "Delta"],
        },
    )
    await _scope_with(world, "garden", {"Planted the roses.": ["Roses", "Tulips"]})
    world.engine.settings.mcp.memory.scope_nodes = {}
    world.engine.settings.extraction.known_entities_limit = 4

    records = build_seed(
        world.engine, "The roses need water.", ["Tulips"], "alice", scope="trader"
    ).records("alice")

    assert [r["name"] for r in records] == ["Tulips", "Roses", "Alpha", "alice"]


async def test_a_large_scope_stays_within_the_seed_limit(world: SimpleNamespace) -> None:
    labels = [f"Item {chr(ord('A') + i)}" for i in range(12)]
    await _scope_with(world, "trader", {"A long list of things.": labels})
    world.engine.settings.mcp.memory.scope_nodes = {}
    world.engine.settings.extraction.known_entities_limit = 5
    asked: list[int] = []
    attested = world.graph.nodes_attested_by

    def counting(source_ids: Any, limit: int) -> list[str]:
        asked.append(limit)
        return attested(source_ids, limit)

    world.graph.nodes_attested_by = counting

    records = build_seed(world.engine, "", [], "alice", scope="trader").records("alice")

    assert len(records) == 5 and records[-1]["name"] == "alice"
    assert asked == [5]  # the room (4) plus the one name already taken: never the whole scope


async def test_a_large_scope_keeps_room_for_its_named_nodes(world: SimpleNamespace) -> None:
    """Twelve attested nodes fill a limit of five; the two named ones are still offered."""
    labels = [f"Item {chr(ord('A') + i)}" for i in range(12)]
    await _scope_with(world, "trader", {"A long list of things.": labels})
    named = [f"Named {i}" for i in range(2)]
    await _scope_with(world, "garden", {"Two names here.": named})
    world.engine.settings.mcp.memory.scope_nodes = {"trader": named}
    world.engine.settings.extraction.known_entities_limit = 5

    records = build_seed(world.engine, "", [], "alice", scope="trader").records("alice")

    names = [r["name"] for r in records]
    assert len(names) == 5 and names[-1] == "alice"
    assert names[2:4] == named  # after the attested ones, in the setting's order
    assert set(names[:2]) <= set(labels)


async def test_an_unknown_scope_node_is_skipped_and_logged_once(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_seed, "_LOGGED_MISSING", set())
    world.engine.settings.mcp.memory.scope_nodes = {"trader": ["Nowhere ledger", JOURNAL]}
    await call(world.server, "create_template", {"name": "Journal", "template_type": "node"})
    await call(
        world.server, "create_node", {"template_id": "Journal", "label": JOURNAL, "properties": {}}
    )
    first = await store_pending(world.engine, EARLIER, "2026-10-19", scope="trader")
    second = await store_pending(world.engine, TRADE, "2026-10-20", scope="trader")

    with structlog.testing.capture_logs() as logs:
        assert await link_memory(world.engine, first) is LinkOutcome.LINKED
        assert await link_memory(world.engine, second) is LinkOutcome.LINKED

    missing = [e for e in logs if e["event"] == "memory_scope_node_missing"]
    assert len(missing) == 1
    assert (missing[0]["scope"], missing[0]["labels"]) == ("trader", ["Nowhere ledger"])
    # as a link logs it (inside private_text, above DEBUG): the setting and the count stand,
    # the scope and the label (a person's words) are withheld
    with private_text():
        shown = withhold_private_text(None, "warning", dict(missing[0]))
    assert (shown["setting"], shown["missing_count"]) == ("mcp.memory.scope_nodes", 1)
    assert shown["scope"] == shown["labels"] == WITHHELD
    assert f"- {JOURNAL}" in _offered(next(p for p in world.llm.prompts if TRADE in p))


def test_no_scope_has_no_scope_nodes(world: SimpleNamespace) -> None:
    assert scope_nodes(world.engine, None) == []
