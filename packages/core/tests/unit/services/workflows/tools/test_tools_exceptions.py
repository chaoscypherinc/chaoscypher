# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for Core-exception raises in workflow tools/plugins.

Covers the 11 sites replaced in Task 8 of the core-exception-hygiene sweep:
  - system_tools.execute_system_tool (1 site)
  - ai_vector_search_plugin.SearchPlugin.execute (1 site)
  - ai_prompt_plugin.PromptPlugin.execute (1 site)
  - ai_generate_embedding_plugin.EmbeddingPlugin.execute / _extract_text_from_inputs (4 sites)
  - ai_extract_json_plugin.ExtractJsonPlugin.execute (2 sites)
  - executor.execute_tool (1 site)
  - system_tools.execute_system_tool (already counted above; 11 total)

Each test asserts:
  - The correct ChaosCypherException subclass is raised (not bare stdlib).
  - The exception carries meaningful attributes (message, code, field/operation).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import (
    NotFoundError,
    OperationError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Helpers / shared fakes
# ---------------------------------------------------------------------------


def _make_context(**kwargs: Any) -> Any:
    """Build a minimal ToolExecutionContext-like namespace."""
    defaults = {
        "graph_manager": MagicMock(),
        "llm_service": None,
        "search_repository": None,
        "discovery_service": None,
        "settings": None,
        "thinking_mode": None,
        "embedding_provider": None,
        "structured_extractor": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# system_tools.execute_system_tool
# ---------------------------------------------------------------------------


class TestExecuteSystemToolExceptions:
    """system_tools.py:85 — missing graph_manager raises ValidationError."""

    @pytest.mark.asyncio
    async def test_missing_graph_manager_raises_validation_error(self) -> None:
        from chaoscypher_core.services.workflows.tools.system_tools import execute_system_tool

        with pytest.raises(ValidationError) as exc_info:
            await execute_system_tool(
                tool_name="templates.list",
                tool_input={},
                managers={},  # graph_manager absent
                settings=MagicMock(),
            )

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert err.field == "graph_manager"
        assert "graph_manager" in err.message

    @pytest.mark.asyncio
    async def test_graph_manager_present_does_not_raise_validation_error(self) -> None:
        """Sanity check: valid managers dict does not immediately raise ValidationError."""
        from chaoscypher_core.services.workflows.tools.system_tools import execute_system_tool

        # Will raise something else (NotFoundError for unknown tool), but NOT ValidationError
        with pytest.raises(Exception) as exc_info:
            await execute_system_tool(
                tool_name="__nonexistent_tool__",
                tool_input={},
                managers={"graph_manager": MagicMock()},
                settings=MagicMock(current_database="default"),
            )

        assert not isinstance(exc_info.value, ValidationError) or (
            exc_info.value.field != "graph_manager"
        )


# ---------------------------------------------------------------------------
# ai_vector_search_plugin.SearchPlugin.execute
# ---------------------------------------------------------------------------


class TestSearchPluginExceptions:
    """ai_vector_search_plugin.py:131 — missing search_manager raises OperationError."""

    @pytest.mark.asyncio
    async def test_missing_search_manager_raises_operation_error(self) -> None:
        from chaoscypher_core.services.workflows.tools.plugins.ai_vector_search_plugin import (
            SearchPlugin,
        )

        plugin = SearchPlugin()
        # graph_manager has no search_manager attribute → getattr returns None
        graph_manager = MagicMock(spec=[])  # no search_manager attr
        context = _make_context(graph_manager=graph_manager)

        with pytest.raises(OperationError) as exc_info:
            await plugin.execute({"query": "test"}, context)

        err = exc_info.value
        assert err.code == "OPERATION_ERROR"
        assert err.operation == "ai.vector_search"
        assert "Search manager" in err.message


# ---------------------------------------------------------------------------
# ai_prompt_plugin.PromptPlugin.execute
# ---------------------------------------------------------------------------


class TestPromptPluginExceptions:
    """ai_prompt_plugin.py:227 — missing llm_service raises OperationError."""

    @pytest.mark.asyncio
    async def test_missing_llm_service_raises_operation_error(self) -> None:
        from chaoscypher_core.services.workflows.tools.plugins.ai_prompt_plugin import (
            PromptPlugin,
        )

        plugin = PromptPlugin()
        context = _make_context(llm_service=None)

        with pytest.raises(OperationError) as exc_info:
            await plugin.execute({"prompt": "Hello"}, context)

        err = exc_info.value
        assert err.code == "OPERATION_ERROR"
        assert err.operation == "ai.prompt"
        assert "LLM service" in err.message


# ---------------------------------------------------------------------------
# ai_generate_embedding_plugin.EmbeddingPlugin
# ---------------------------------------------------------------------------


class TestEmbeddingPluginExceptions:
    """Four sites in ai_generate_embedding_plugin.py."""

    @pytest.mark.asyncio
    async def test_missing_settings_raises_operation_error(self) -> None:
        """Line 140 — no settings and no injected provider → OperationError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_generate_embedding_plugin import (
            EmbeddingPlugin,
        )

        plugin = EmbeddingPlugin()
        graph_manager = MagicMock()
        # Simulate injected provider absent; settings also absent
        context = _make_context(
            graph_manager=graph_manager,
            embedding_provider=None,
            settings=None,
        )

        with pytest.raises(OperationError) as exc_info:
            await plugin.execute({"text": "hello"}, context)

        err = exc_info.value
        assert err.code == "OPERATION_ERROR"
        assert err.operation == "ai.generate_embedding"
        assert "settings" in err.message.lower()

    def test_node_not_found_raises_not_found_error(self) -> None:
        """Line 181 — node lookup returns None → NotFoundError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_generate_embedding_plugin import (
            EmbeddingPlugin,
        )

        plugin = EmbeddingPlugin()
        graph_manager = MagicMock()
        graph_manager.get_node.return_value = None  # node not found

        with pytest.raises(NotFoundError) as exc_info:
            plugin._extract_text_from_inputs(
                {"entity_id": "node-abc", "entity_type": "node"},
                graph_manager,
            )

        err = exc_info.value
        assert err.code == "NOT_FOUND"
        assert err.resource_type == "Node"
        assert "node-abc" in err.identifier

    def test_edge_not_found_raises_not_found_error(self) -> None:
        """Line 196 — edge lookup returns None → NotFoundError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_generate_embedding_plugin import (
            EmbeddingPlugin,
        )

        plugin = EmbeddingPlugin()
        graph_manager = MagicMock()
        graph_manager.get_edge.return_value = None  # edge not found

        with pytest.raises(NotFoundError) as exc_info:
            plugin._extract_text_from_inputs(
                {"entity_id": "edge-xyz", "entity_type": "edge"},
                graph_manager,
            )

        err = exc_info.value
        assert err.code == "NOT_FOUND"
        assert err.resource_type == "Edge"
        assert "edge-xyz" in err.identifier

    def test_unsupported_entity_type_raises_validation_error(self) -> None:
        """Line 200 — unknown entity_type → ValidationError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_generate_embedding_plugin import (
            EmbeddingPlugin,
        )

        plugin = EmbeddingPlugin()
        graph_manager = MagicMock()

        with pytest.raises(ValidationError) as exc_info:
            plugin._extract_text_from_inputs(
                {"entity_id": "some-id", "entity_type": "relationship"},
                graph_manager,
            )

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert err.field == "entity_type"
        assert "relationship" in err.message

    def test_missing_required_input_raises_validation_error(self) -> None:
        """Line 203 — no text/entity/entity_id → ValidationError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_generate_embedding_plugin import (
            EmbeddingPlugin,
        )

        plugin = EmbeddingPlugin()
        graph_manager = MagicMock()

        with pytest.raises(ValidationError) as exc_info:
            plugin._extract_text_from_inputs({}, graph_manager)

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert err.field == "text"
        assert "must be provided" in err.message


# ---------------------------------------------------------------------------
# ai_extract_json_plugin.ExtractJsonPlugin.execute
# ---------------------------------------------------------------------------


class TestExtractJsonPluginExceptions:
    """Two sites in ai_extract_json_plugin.py."""

    @pytest.mark.asyncio
    async def test_missing_llm_service_raises_operation_error(self) -> None:
        """Line 184 — no llm_service → OperationError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_extract_json_plugin import (
            ExtractJsonPlugin,
        )

        plugin = ExtractJsonPlugin()
        context = _make_context(llm_service=None, settings=MagicMock())

        with pytest.raises(OperationError) as exc_info:
            await plugin.execute({"text": "some text", "json_schema": {}}, context)

        err = exc_info.value
        assert err.code == "OPERATION_ERROR"
        assert err.operation == "ai.extract_json"
        assert "LLM service" in err.message

    @pytest.mark.asyncio
    async def test_missing_settings_raises_validation_error(self) -> None:
        """Line 189 — llm_service present but no settings → ValidationError."""
        from chaoscypher_core.services.workflows.tools.plugins.ai_extract_json_plugin import (
            ExtractJsonPlugin,
        )

        plugin = ExtractJsonPlugin()
        context = _make_context(llm_service=AsyncMock(), settings=None)

        with pytest.raises(ValidationError) as exc_info:
            await plugin.execute({"text": "some text", "json_schema": {}}, context)

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert err.field == "settings"
        assert "settings" in err.message.lower()


# ---------------------------------------------------------------------------
# executor.execute_tool
# ---------------------------------------------------------------------------


class TestExecuteToolExceptions:
    """executor.py:327 — unknown tool_id raises NotFoundError."""

    @pytest.mark.asyncio
    async def test_unknown_tool_id_raises_not_found_error(self) -> None:
        from chaoscypher_core.services.workflows.tools.engine.executor import execute_tool

        with pytest.raises(NotFoundError) as exc_info:
            await execute_tool(
                tool_id="__completely_nonexistent_tool_id__",
                inputs={},
                graph_manager=MagicMock(),
            )

        err = exc_info.value
        assert err.code == "NOT_FOUND"
        assert err.resource_type == "Tool"
        assert "__completely_nonexistent_tool_id__" in err.identifier
