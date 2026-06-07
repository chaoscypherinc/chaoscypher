# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for services/chat/engine/research.py (ResearchAgent).

Covers ``__init__`` delegation, ``research_topic`` pass-through to the
``TopicResearcher`` collaborator, and the three locally-implemented operations:
``build_topic_hierarchy``, ``identify_knowledge_gaps``, and
``extract_entities_from_text`` — across both the direct-LLM-call branch and the
queue branch, plus their JSON-parse / node-creation / exception paths.

All collaborators (graph/search/config/llm managers and the LLM provider) are
MagicMock/AsyncMock stubs. The module-level ``get_settings`` (used only for the
extraction context-window) is patched at the research-module source path.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.chat.engine import research as research_mod
from chaoscypher_core.services.chat.engine.research import ResearchAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(enable_queue: bool = False) -> SimpleNamespace:
    """Settings object as returned by config_manager.get_settings()."""
    return SimpleNamespace(
        enable_llm_queueing=enable_queue,
        llm=SimpleNamespace(thinking_for_tools=False),
    )


def _make_agent(
    *,
    provider_response: Any = None,
    enable_queue: bool = False,
    llm: Any = None,
) -> tuple[ResearchAgent, dict[str, MagicMock]]:
    """Build a ResearchAgent with mocked managers; return (agent, mocks)."""
    graph = MagicMock()
    search = MagicMock()
    config = MagicMock()
    config.get_settings.return_value = _settings(enable_queue)

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=provider_response)
    llm_manager = MagicMock()
    llm_manager.get_chat_provider.return_value = provider

    agent = ResearchAgent(graph, search, config, llm_manager, llm=llm)
    return agent, {
        "graph": graph,
        "search": search,
        "config": config,
        "provider": provider,
        "llm_manager": llm_manager,
    }


def _queue_llm(response: Any) -> MagicMock:
    """LLM queue stub: queue_operation -> task id, wait_for_result -> {response}."""
    llm = MagicMock()
    llm.queue_operation = AsyncMock(return_value="task-1")
    llm.wait_for_result = AsyncMock(return_value={"response": response})
    return llm


# ---------------------------------------------------------------------------
# __init__ / delegation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitAndDelegation:
    def test_init_wires_collaborators_and_topic_researcher(self) -> None:
        agent, mocks = _make_agent()
        assert agent.graph is mocks["graph"]
        assert agent.search is mocks["search"]
        # llm defaults to llm_manager when not provided
        assert agent.llm is mocks["llm_manager"]
        assert agent.topic_researcher is not None

    def test_explicit_llm_overrides_manager(self) -> None:
        llm = MagicMock()
        agent, _ = _make_agent(llm=llm)
        assert agent.llm is llm

    @pytest.mark.asyncio
    async def test_research_topic_delegates(self) -> None:
        agent, _ = _make_agent()
        expected = {"success": True, "topic": "AI"}
        agent.topic_researcher.research_topic = AsyncMock(return_value=expected)

        result = await agent.research_topic("AI", depth="deep")

        agent.topic_researcher.research_topic.assert_awaited_once_with("AI", "deep")
        assert result == expected


# ---------------------------------------------------------------------------
# build_topic_hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildTopicHierarchy:
    @pytest.mark.asyncio
    async def test_direct_call_with_content(self) -> None:
        agent, _ = _make_agent(provider_response={"content": "## Hierarchy text"})
        result = await agent.build_topic_hierarchy("Biology", max_levels=2)

        assert result["success"] is True
        assert result["topic"] == "Biology"
        assert result["max_levels"] == 2
        assert "Hierarchy text" in result["hierarchy"]

    @pytest.mark.asyncio
    async def test_direct_json_response_serialized(self) -> None:
        # Response that is already a direct hierarchy JSON dict.
        direct = {"main_topic": "Biology", "hierarchy": [{"level": 1, "name": "Cells"}]}
        agent, _ = _make_agent(provider_response=direct)
        result = await agent.build_topic_hierarchy("Biology")

        assert result["success"] is True
        parsed = json.loads(result["hierarchy"])
        assert parsed["main_topic"] == "Biology"

    @pytest.mark.asyncio
    async def test_queue_branch_with_dict_response(self) -> None:
        llm = _queue_llm({"main_topic": "AI", "hierarchy": []})
        agent, _ = _make_agent(enable_queue=True, llm=llm)
        result = await agent.build_topic_hierarchy("AI")

        llm.queue_operation.assert_awaited_once()
        llm.wait_for_result.assert_awaited_once()
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_queue_branch_with_json_string_response(self) -> None:
        # Queue returns a JSON *string* that must be parsed.
        payload = json.dumps({"main_topic": "AI", "hierarchy": []})
        llm = _queue_llm(payload)
        agent, _ = _make_agent(enable_queue=True, llm=llm)
        result = await agent.build_topic_hierarchy("AI")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_queue_branch_with_invalid_json_string(self) -> None:
        # Invalid JSON string -> response reset to {} -> fallback content path.
        llm = _queue_llm("not json at all")
        agent, _ = _make_agent(enable_queue=True, llm=llm)
        result = await agent.build_topic_hierarchy("AI")
        # Still succeeds (fallback message embedded), success flag True.
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self) -> None:
        agent, mocks = _make_agent()
        mocks["llm_manager"].get_chat_provider.side_effect = RuntimeError("provider down")
        result = await agent.build_topic_hierarchy("AI")
        assert result == {"success": False, "error": "Research operation failed"}


# ---------------------------------------------------------------------------
# identify_knowledge_gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdentifyKnowledgeGaps:
    @pytest.mark.asyncio
    async def test_direct_call_with_existing_nodes(self) -> None:
        agent, mocks = _make_agent(provider_response={"content": "gap analysis text"})

        mocks["search"].search.return_value = [{"id": "n1"}, {"id": "n2"}]
        node1 = SimpleNamespace(id="n1", label="Node1", template_id="t1")
        node2 = SimpleNamespace(id="n2", label="Node2", template_id="t2")
        mocks["graph"].get_node.side_effect = lambda nid: {"n1": node1, "n2": node2}[nid]
        mocks["graph"].list_edges.return_value = [object(), object()]

        result = await agent.identify_knowledge_gaps("Genetics")

        assert result["success"] is True
        assert result["existing_nodes_count"] == 2
        assert "gap analysis text" in result["gap_analysis"]
        assert result["existing_nodes"][0]["num_connections"] == 2

    @pytest.mark.asyncio
    async def test_skips_missing_nodes(self) -> None:
        agent, mocks = _make_agent(provider_response={"content": "analysis"})
        mocks["search"].search.return_value = [{"id": "n1"}, {"id": "gone"}]
        mocks["graph"].get_node.side_effect = lambda nid: (
            SimpleNamespace(id="n1", label="N1", template_id="t") if nid == "n1" else None
        )
        mocks["graph"].list_edges.return_value = []

        result = await agent.identify_knowledge_gaps("X")
        assert result["existing_nodes_count"] == 1

    @pytest.mark.asyncio
    async def test_no_existing_nodes(self) -> None:
        agent, mocks = _make_agent(provider_response={"content": "analysis"})
        mocks["search"].search.return_value = []
        result = await agent.identify_knowledge_gaps("X")
        assert result["success"] is True
        assert result["existing_nodes_count"] == 0

    @pytest.mark.asyncio
    async def test_queue_branch(self) -> None:
        llm = _queue_llm({"content": "queued gap analysis"})
        agent, mocks = _make_agent(enable_queue=True, llm=llm)
        mocks["search"].search.return_value = []
        result = await agent.identify_knowledge_gaps("X")
        llm.queue_operation.assert_awaited_once()
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_queue_branch_invalid_json_string(self) -> None:
        llm = _queue_llm("bad json")
        agent, mocks = _make_agent(enable_queue=True, llm=llm)
        mocks["search"].search.return_value = []
        result = await agent.identify_knowledge_gaps("X")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self) -> None:
        agent, mocks = _make_agent()
        mocks["search"].search.side_effect = RuntimeError("search down")
        result = await agent.identify_knowledge_gaps("X")
        assert result == {"success": False, "error": "Research operation failed"}


# ---------------------------------------------------------------------------
# extract_entities_from_text
# ---------------------------------------------------------------------------

_ENTITIES_JSON = json.dumps(
    [
        {"entity": "Alice", "type": "person", "description": "engineer", "template_id": "person"},
        {"entity": "Acme", "type": "organization", "description": "co"},
    ]
)


def _patch_extraction_settings() -> Any:
    """Patch module-level get_settings for the extraction context window."""
    settings = SimpleNamespace(extraction=SimpleNamespace(research_context_window_chars=5000))
    return patch.object(research_mod, "get_settings", return_value=settings)


@pytest.mark.unit
class TestExtractEntities:
    @pytest.mark.asyncio
    async def test_direct_parse_without_template(self) -> None:
        agent, mocks = _make_agent(provider_response={"content": _ENTITIES_JSON})
        mocks["graph"].list_templates.return_value = [
            SimpleNamespace(id="t1", name="Person", template_type="entity")
        ]
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("some text")

        assert result["success"] is True
        assert result["num_entities"] == 2
        assert result["created_nodes"] == []
        # list_templates consulted because template_id was None
        mocks["graph"].list_templates.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_template_id_skips_template_listing(self) -> None:
        agent, mocks = _make_agent(provider_response={"content": _ENTITIES_JSON})
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("txt", template_id="custom")
        assert result["success"] is True
        mocks["graph"].list_templates.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_nodes_true_creates_nodes(self) -> None:
        # research.py imports NodeCreate from chaoscypher_core.models and creates
        # a graph node per parsed entity, returning their id/label in created_nodes.
        agent, mocks = _make_agent(provider_response={"content": _ENTITIES_JSON})
        mocks["graph"].create_node.return_value = SimpleNamespace(id="node-1", label="Alice")
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text(
                "txt", template_id="person", create_nodes=True
            )

        assert result["success"] is True
        mocks["graph"].create_node.assert_called()
        assert result["created_nodes"]  # one entry per parsed entity
        assert len(result["created_nodes"]) == mocks["graph"].create_node.call_count
        assert all(n == {"id": "node-1", "label": "Alice"} for n in result["created_nodes"])

    @pytest.mark.asyncio
    async def test_create_nodes_false_does_not_trigger_import(self) -> None:
        # With create_nodes=False the broken `from .models import NodeCreate`
        # line is never reached, so the happy path succeeds.
        agent, mocks = _make_agent(provider_response={"content": _ENTITIES_JSON})
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text(
                "txt", template_id="person", create_nodes=False
            )
        assert result["success"] is True
        assert result["created_nodes"] == []
        mocks["graph"].create_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_json_embedded_in_prose_recovered_via_regex(self) -> None:
        prose = f"Here are the entities:\n{_ENTITIES_JSON}\nThanks!"
        agent, mocks = _make_agent(provider_response={"content": prose})
        mocks["graph"].list_templates.return_value = []
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("txt")
        # Regex array recovery yields the 2 entities.
        assert result["num_entities"] == 2

    @pytest.mark.asyncio
    async def test_unparseable_response_yields_empty_entities(self) -> None:
        agent, mocks = _make_agent(provider_response={"content": "no json here"})
        mocks["graph"].list_templates.return_value = []
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("txt")
        # extract_content_with_fallback returns content; not valid JSON -> []
        assert result["num_entities"] == 0
        assert result["entities"] == []

    @pytest.mark.asyncio
    async def test_queue_branch(self) -> None:
        llm = _queue_llm({"content": _ENTITIES_JSON})
        agent, mocks = _make_agent(enable_queue=True, llm=llm)
        mocks["graph"].list_templates.return_value = []
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("txt")
        llm.queue_operation.assert_awaited_once()
        assert result["num_entities"] == 2

    @pytest.mark.asyncio
    async def test_queue_branch_invalid_json_string(self) -> None:
        llm = _queue_llm("totally not json")
        agent, mocks = _make_agent(enable_queue=True, llm=llm)
        mocks["graph"].list_templates.return_value = []
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("txt")
        assert result["success"] is True
        assert result["num_entities"] == 0

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self) -> None:
        agent, mocks = _make_agent()
        mocks["llm_manager"].get_chat_provider.side_effect = RuntimeError("boom")
        with _patch_extraction_settings():
            result = await agent.extract_entities_from_text("txt")
        assert result == {"success": False, "error": "Research operation failed"}
