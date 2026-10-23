# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""graphrag_search decides a shape, records it, and runs the shape's levers from the context on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.cache.call_site import current_call_site
from chaoscypher_core.question_shapes import SHAPE_NAMES
from chaoscypher_core.services.workflows.tools.engine.chunk_hydration import UNTRUSTED_FENCE_CLOSE
from chaoscypher_core.services.workflows.tools.engine.context_render import (
    EVIDENCE_FENCE_NOTE,
    GRAPH_EVIDENCE_HEADER,
    SHAPE_NOTES,
)
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import QuestionShape
from chaoscypher_core.settings import EngineSettings
from tests.fakes.graphrag_world import batch_lookup, make_chunk, node, walk_handler


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )


PATH_QUESTION = "How is Lise connected to Prince Andrew?"
"""Names two seeds (Lise Meinen, Prince Andrew) and a path cue: the rules call it ``path``."""


def _handler(chat: Any = None, **graphrag: Any) -> GraphRAGToolHandlers:
    """The shared walk world (``graphrag_world.walk_handler``) seeded on Lise and Prince Andrew."""
    return walk_handler(
        chat, ppr={"L": 0.5, "A": 0.4}, keyword=[("L", 1.0), ("A", 0.9)], **graphrag
    )


@pytest.mark.asyncio
async def test_the_rules_route_a_path_question_and_the_stats_say_so() -> None:
    h = _handler(router="rules")
    result = await h.graphrag_search(PATH_QUESTION)
    stats = result["retrieval_stats"]
    assert (stats["shape"], stats["shape_source"]) == ("path", "rules")
    assert "connected" in stats["shape_reason"]
    assert result["shape"] == "path"
    assert stats["faults"] == []


@pytest.mark.asyncio
async def test_router_off_is_lookup_and_changes_nothing_else() -> None:
    off = await _handler(router="off").graphrag_search(PATH_QUESTION)
    assert off["retrieval_stats"]["shape"] == "lookup"
    assert off["retrieval_stats"]["shape_source"] == "default"
    # the shipped path bundle never ran: the result is the one with no bundles at all, but
    # for the walk block (its fenced evidence region) and the explanation's sentences on it,
    # which are the path shape's own assembly rather than a lever
    bare = await _handler(router="rules", shape_levers={}).graphrag_search(PATH_QUESTION)
    assert "walks" not in off["graph_context"] and bare["graph_context"]["walks"]
    notes = f" {SHAPE_NOTES['path']} {EVIDENCE_FENCE_NOTE}"
    assert notes in bare["context_text"] and notes not in off["context_text"]
    bare_lines = [ln.replace(notes, "") for ln in bare["context_text"].splitlines()]
    start = bare_lines.index(GRAPH_EVIDENCE_HEADER) - 1  # the region's opening fence
    end = bare_lines.index(UNTRUSTED_FENCE_CLOSE, start)
    assert all(ln.startswith("- walk:") for ln in bare_lines[start + 2 : end])
    del bare_lines[start : end + 1]
    assert off["context_text"].splitlines() == bare_lines
    assert off["chunks"] == bare["chunks"]


@pytest.mark.asyncio
async def test_the_shape_levers_apply_from_the_context_on() -> None:
    seen: list[tuple[int, int]] = []
    h = _handler(router="rules", shape_levers={"path": {"vector_reserve": 1, "max_triples": 11}})
    original_merge = h._merge_and_rank
    original_assemble = h._assemble_graph_context

    def assemble(*a: Any, **k: Any) -> Any:
        seen.append((h.levers.max_triples, h.levers.vector_reserve))
        return original_assemble(*a, **k)

    def merge(*a: Any, **k: Any) -> Any:
        seen.append((h.levers.max_triples, h.levers.vector_reserve))
        return original_merge(*a, **k)

    h._assemble_graph_context = assemble  # type: ignore[method-assign]
    h._merge_and_rank = merge  # type: ignore[method-assign]
    h.search.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9)])  # type: ignore[method-assign]
    h.indexing.get_chunks_by_ids_batch.side_effect = batch_lookup(make_chunk("c1"))  # type: ignore[union-attr]
    await h.graphrag_search(PATH_QUESTION)
    assert seen == [(11, 1), (11, 1)]
    # reset after the call: the next call (and anything outside one) reads the base settings
    assert h.levers is h._graphrag
    assert h.levers.vector_reserve != 1


@pytest.mark.asyncio
async def test_a_failed_call_never_leaks_its_levers_into_the_next() -> None:
    h = _handler(router="rules", shape_levers={"path": {"vector_reserve": 1}})

    def boom(*a: Any, **k: Any) -> Any:
        raise KeyError("merge")

    h._retrieve_vector_chunks = boom  # type: ignore[method-assign]
    with pytest.raises(KeyError):
        await h.graphrag_search(PATH_QUESTION)
    assert h.levers is h._graphrag


@pytest.mark.asyncio
async def test_a_failed_router_model_records_a_fault_and_falls_back() -> None:
    async def broken(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        raise RuntimeError("down")

    h = _handler(chat=broken, router="llm")
    result = await h.graphrag_search("What did Lise say?")
    assert result["retrieval_stats"]["faults"] == ["router"]
    assert result["retrieval_stats"]["shape"] == "lookup"
    assert result["shape"] == "lookup"


@pytest.mark.asyncio
async def test_the_router_model_is_called_under_the_router_call_site() -> None:
    sites: list[str] = []

    async def chat(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        sites.append(current_call_site())
        return {"content": '{"shape": "aggregate", "reason": "asks for everyone"}'}

    result = await _handler(chat=chat, router="llm").graphrag_search("What did Lise say?")
    assert sites == ["router"]
    stats = result["retrieval_stats"]
    assert (stats["shape"], stats["shape_reason"], stats["shape_source"]) == (
        "aggregate",
        "asks for everyone",
        "llm",
    )


@pytest.mark.asyncio
async def test_an_unreadable_source_listing_records_the_relation_vocabulary_fault() -> None:
    h = _handler(router="rules")
    sources = MagicMock()
    sources.list_sources.side_effect = RuntimeError("db gone")
    sources.get_citations_batch.return_value = []
    h.source_storage = sources
    result = await h.graphrag_search(PATH_QUESTION)
    assert result["retrieval_stats"]["faults"] == ["relation_vocabulary"]
    assert result["shape"] == "path"  # the rules still decide on the path cue


def test_question_relations_are_the_vocabulary_words_the_question_uses_in_order() -> None:
    h = _handler()
    assert h._question_relations("Whose wife was Lise, and whose husband Andrew?") == ["spouse"]
    assert h._question_relations("Who is Lise?") == []


def test_the_executor_hands_its_chat_callback_to_the_graphrag_handler() -> None:
    async def chat(messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        return {"content": ""}

    executor = ToolExecutorService(
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        llm_chat_callback=chat,
        engine_settings=EngineSettings(),
    )
    assert executor.graphrag_handlers.chat_callback is chat


def test_the_shape_names_are_the_question_shapes() -> None:
    assert tuple(s.value for s in QuestionShape) == SHAPE_NAMES


@pytest.mark.asyncio
async def test_a_provider_response_from_the_router_model_is_the_models_shape() -> None:
    """The product's callbacks returned an ``LLMChatResponse``: the router read it with no fault."""
    from chaoscypher_core.models import LLMChatResponse

    async def chat(messages: list[dict[str, Any]], **_: Any) -> LLMChatResponse:
        return LLMChatResponse(
            content='{"shape": "aggregate", "reason": "asks for everyone"}', provider="ollama"
        )

    result = await _handler(chat=chat, router="llm").graphrag_search("What did Lise say?")
    stats = result["retrieval_stats"]
    assert (stats["shape"], stats["shape_source"], stats["faults"]) == ("aggregate", "llm", [])


@pytest.mark.asyncio
async def test_a_bundle_the_settings_refuse_is_a_fault_and_the_call_runs_on_the_base() -> None:
    """A value the field rejects (caught only when the bundle is applied) never fails the call."""
    h = _handler(router="rules", shape_levers={"path": {"vector_reserve": -1}})
    base = await _handler(router="rules", shape_levers={}).graphrag_search(PATH_QUESTION)
    result = await h.graphrag_search(PATH_QUESTION)
    assert result["retrieval_stats"]["faults"] == ["shape_levers"]
    assert result["shape"] == "path"
    assert result["context_text"] == base["context_text"]
    assert result["chunks"] == base["chunks"]


@pytest.mark.asyncio
async def test_the_chunk_budget_follows_the_shape_and_the_stats_record_it() -> None:
    h = _handler(router="rules", shape_levers={"path": {"chunk_limit": 3}})
    assert (await h.graphrag_search(PATH_QUESTION))["retrieval_stats"]["chunk_limit"] == 3
    asked = await h.graphrag_search(PATH_QUESTION, limit=7)
    assert asked["retrieval_stats"]["chunk_limit"] == 7  # an explicit budget wins
    lookup = await h.graphrag_search("Who is Lise?")
    assert lookup["retrieval_stats"]["chunk_limit"] == h._graphrag.chunk_limit


def test_every_base_settings_read_of_the_handler_is_a_pre_shape_field() -> None:
    """A read of ``self._graphrag`` runs before the shape is known, so a bundle may not set it.

    The guard on :data:`PRE_SHAPE_FIELDS`: a new base read must join the set (and so be refused
    in a bundle), or read ``self.levers``. ``chunk_limit`` is ``hybrid_retrieval``'s own budget
    (no shape there); graphrag_search's budget follows the shape.
    """
    import inspect
    import re

    from chaoscypher_core.question_shapes import PRE_SHAPE_FIELDS
    from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers

    reads = set(re.findall(r"self\._graphrag\.(\w+)", inspect.getsource(graphrag_handlers)))
    assert reads - {"chunk_limit"} <= set(PRE_SHAPE_FIELDS)
    assert set(PRE_SHAPE_FIELDS) - {"router"} <= reads  # no stale entry


@pytest.mark.asyncio
async def test_a_registry_that_cannot_build_the_sources_packs_is_the_vocabulary_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_core.services.sources.engine.extraction import domain_resolver

    h = _handler(router="rules")
    sources = MagicMock()
    sources.list_sources.return_value = ([{"forced_domain": "literary"}], 1)
    sources.get_citations_batch.return_value = []
    h.source_storage = sources
    monkeypatch.setattr(domain_resolver, "packs_stack", lambda packs, settings: None)
    result = await h.graphrag_search(PATH_QUESTION)
    assert result["retrieval_stats"]["faults"] == ["relation_vocabulary"]


def test_the_rules_count_the_mentions_that_name_a_seed_not_the_seeds_they_match() -> None:
    """One surname naming three Rostóv seeds is one mention; a full name is one; two people are two.

    Until 2026-10-09 the count was the seed nodes the question's words named,
    so "the Rostóvs" counted three and a lookup about them abstained.
    """
    rostovs = [
        node("R", "Count Rostóv"),
        node("C", "Countess Rostóva"),
        node("N", "Nicholas Rostóv"),
        node("P", "Anna Pávlovna Schérer"),
    ]
    h = _handler()
    assert h._named_seed_count("What did the Rostóvs give at the dinner?", rostovs) == 1
    assert h._named_seed_count("What did Anna Pávlovna serve?", rostovs) == 1
    assert h._named_seed_count("How is Nicholas Rostóv connected to Anna Pávlovna?", rostovs) == 2
    assert h._named_seed_count("Did Nicholas, the count and Anna dance?", rostovs) == 2
    # the aggregate's entities are still the seeds the words name
    by_id = {n.id: n for n in rostovs}
    assert h._question_named("Which people are the Rostóvs' children?", list(by_id), by_id) == {
        "R",
        "C",
        "N",
    }
