# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ToolExecutorService.execute_tool routing and _apply_scope injection.

Covers:
- execute_tool dispatches to correct handler via _tool_handlers dict
- Unknown tool returns error dict
- TypeError from handler returns invalid-parameters error
- General exceptions return tool-execution-failed error
- _apply_scope injects source_ids for scoped tools
- _apply_scope leaves unscoped tools unchanged
- _apply_scope is a no-op when scope has no source_ids
- _apply_scope overrides existing source_ids in params
- _SOURCE_SCOPED_TOOLS contains expected entries
- All registered handlers are callable
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService


@pytest.fixture
def mock_graph_repo():
    """Create mock graph repository."""
    return MagicMock()


@pytest.fixture
def mock_search_repo():
    """Create mock search repository."""
    return MagicMock()


@pytest.fixture
def executor(mock_graph_repo, mock_search_repo):
    """Create a ToolExecutorService with no scope."""
    return ToolExecutorService(
        graph_repository=mock_graph_repo,
        search_repository=mock_search_repo,
    )


@pytest.fixture
def scoped_executor(mock_graph_repo, mock_search_repo):
    """Create a ToolExecutorService with source_ids scope."""
    return ToolExecutorService(
        graph_repository=mock_graph_repo,
        search_repository=mock_search_repo,
        scope={"source_ids": ["src1", "src2"]},
    )


class TestExecuteToolRouting:
    """Test execute_tool dispatches to correct handlers."""

    @pytest.mark.asyncio
    async def test_dispatches_to_correct_handler(self, executor):
        """execute_tool should call the handler mapped to the tool name."""
        mock_handler = AsyncMock(return_value={"nodes": []})
        executor._tool_handlers["search_nodes"] = mock_handler

        result = await executor.execute_tool("search_nodes", {"query": "test"})

        mock_handler.assert_called_once_with(query="test")
        assert result == {"nodes": []}

    @pytest.mark.asyncio
    async def test_returns_handler_result(self, executor):
        """execute_tool should return the dict from the handler."""
        expected = {"id": "node-123", "label": "Alice"}
        executor._tool_handlers["get_node"] = AsyncMock(return_value=expected)

        result = await executor.execute_tool("get_node", {"node_id": "node-123"})

        assert result == expected

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        """execute_tool should return error dict for unregistered tool names."""
        result = await executor.execute_tool("nonexistent_tool", {"foo": "bar"})

        assert result == {"error": "Unknown tool: nonexistent_tool"}

    @pytest.mark.asyncio
    async def test_type_error_returns_invalid_parameters(self, executor):
        """TypeError from handler should produce invalid-parameters error."""
        handler = AsyncMock(side_effect=TypeError("unexpected keyword argument 'bad'"))
        executor._tool_handlers["search_nodes"] = handler

        result = await executor.execute_tool("search_nodes", {"bad": "param"})

        assert result == {"error": "Invalid parameters for search_nodes"}

    @pytest.mark.asyncio
    async def test_general_exception_returns_execution_failed(self, executor):
        """Non-TypeError exceptions should produce generic failure error."""
        handler = AsyncMock(side_effect=RuntimeError("database connection lost"))
        executor._tool_handlers["search_nodes"] = handler

        result = await executor.execute_tool("search_nodes", {"query": "test"})

        assert result == {"error": "Tool execution failed"}

    @pytest.mark.asyncio
    async def test_value_error_returns_execution_failed(self, executor):
        """ValueError from handler should produce generic failure error."""
        handler = AsyncMock(side_effect=ValueError("invalid node id"))
        executor._tool_handlers["get_node"] = handler

        result = await executor.execute_tool("get_node", {"node_id": "bad"})

        assert result == {"error": "Tool execution failed"}

    @pytest.mark.asyncio
    async def test_passes_multiple_parameters(self, executor):
        """execute_tool should unpack all parameters as kwargs to the handler."""
        mock_handler = AsyncMock(return_value={"created": True})
        executor._tool_handlers["create_edge"] = mock_handler

        params = {"source_id": "n1", "target_id": "n2", "label": "KNOWS"}
        await executor.execute_tool("create_edge", params)

        mock_handler.assert_called_once_with(source_id="n1", target_id="n2", label="KNOWS")

    @pytest.mark.asyncio
    async def test_empty_parameters(self, executor):
        """execute_tool should work with empty parameters dict."""
        mock_handler = AsyncMock(return_value={"templates": []})
        executor._tool_handlers["list_templates"] = mock_handler

        result = await executor.execute_tool("list_templates", {})

        mock_handler.assert_called_once_with()
        assert result == {"templates": []}


class TestApplyScope:
    """Test _apply_scope injects source_ids into tool parameters."""

    def test_no_scope_returns_params_unchanged(self, executor):
        """When scope has no source_ids, parameters should be returned as-is."""
        params = {"query": "test"}
        result = executor._apply_scope("search_nodes", params)

        assert result is params

    def test_scoped_tool_gets_source_ids_injected(self, scoped_executor):
        """Source-scoped tool should have source_ids added to parameters."""
        params = {"query": "test"}
        result = scoped_executor._apply_scope("search_nodes", params)

        assert result == {"query": "test", "source_ids": ["src1", "src2"]}

    def test_unscoped_tool_returns_params_unchanged(self, scoped_executor):
        """Non-source-scoped tool should not have source_ids injected."""
        params = {"query": "test"}
        result = scoped_executor._apply_scope("list_templates", params)

        assert result == {"query": "test"}
        assert "source_ids" not in result

    def test_scope_overrides_existing_source_ids(self, scoped_executor):
        """Scope source_ids should override any existing source_ids in params."""
        params = {"query": "test", "source_ids": ["original"]}
        result = scoped_executor._apply_scope("search_nodes", params)

        assert result["source_ids"] == ["src1", "src2"]

    def test_preserves_other_params_when_injecting(self, scoped_executor):
        """All original parameters should be preserved alongside injected source_ids."""
        params = {"query": "graph theory", "limit": 10, "offset": 0}
        result = scoped_executor._apply_scope("search_nodes", params)

        assert result["query"] == "graph theory"
        assert result["limit"] == 10
        assert result["offset"] == 0
        assert result["source_ids"] == ["src1", "src2"]

    def test_empty_source_ids_treated_as_no_scope(self):
        """Empty source_ids list in scope should not inject (falsy check)."""
        executor = ToolExecutorService(
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            scope={"source_ids": []},
        )
        params = {"query": "test"}
        result = executor._apply_scope("search_nodes", params)

        assert result is params
        assert "source_ids" not in result

    def test_scope_without_source_ids_key(self):
        """Scope dict without source_ids key should not inject."""
        executor = ToolExecutorService(
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            scope={"database_name": "mydb"},
        )
        params = {"query": "test"}
        result = executor._apply_scope("search_nodes", params)

        assert result is params

    def test_all_source_scoped_tools_get_injection(self, scoped_executor):
        """Every tool in _SOURCE_SCOPED_TOOLS should receive source_ids injection."""
        for tool_name in ToolExecutorService._SOURCE_SCOPED_TOOLS:
            params = {"query": "test"}
            result = scoped_executor._apply_scope(tool_name, params)
            assert "source_ids" in result, f"{tool_name} did not receive source_ids"
            assert result["source_ids"] == ["src1", "src2"]


class TestApplyScopeIntegration:
    """Test that execute_tool applies scope before calling handler."""

    @pytest.mark.asyncio
    async def test_scoped_tool_receives_source_ids(self, scoped_executor):
        """Handler for scoped tool should receive source_ids in kwargs."""
        mock_handler = AsyncMock(return_value={"results": []})
        scoped_executor._tool_handlers["search_nodes"] = mock_handler

        await scoped_executor.execute_tool("search_nodes", {"query": "test"})

        mock_handler.assert_called_once_with(query="test", source_ids=["src1", "src2"])

    @pytest.mark.asyncio
    async def test_unscoped_tool_does_not_receive_source_ids(self, scoped_executor):
        """Handler for non-scoped tool should not receive source_ids."""
        mock_handler = AsyncMock(return_value={"templates": []})
        scoped_executor._tool_handlers["list_templates"] = mock_handler

        await scoped_executor.execute_tool("list_templates", {"query": "test"})

        mock_handler.assert_called_once_with(query="test")

    @pytest.mark.asyncio
    async def test_no_scope_skips_apply(self, executor):
        """When executor has no scope, _apply_scope should not be called."""
        mock_handler = AsyncMock(return_value={"nodes": []})
        executor._tool_handlers["search_nodes"] = mock_handler

        await executor.execute_tool("search_nodes", {"query": "hello"})

        # Should be called with original params, no source_ids
        mock_handler.assert_called_once_with(query="hello")


class TestSourceScopedTools:
    """Test _SOURCE_SCOPED_TOOLS class variable contains expected entries."""

    EXPECTED_SCOPED_TOOLS = {
        "graphrag_search",
        "search_chunks",
        "search_nodes",
        "get_node",
        "get_node_context",
        "get_node_edges",
        "traverse_path",
        "resolve_node",
        "create_node",
        "update_node",
        "create_edge",
        "delete_node",
        "list_edges",
        "analyze_graph_structure",
        "find_shortest_path",
        "find_similar_nodes",
        "summarize",
        "get_summary_context",
    }

    def test_contains_all_expected_tools(self):
        """_SOURCE_SCOPED_TOOLS should contain all 18 expected tool names."""
        assert ToolExecutorService._SOURCE_SCOPED_TOOLS == self.EXPECTED_SCOPED_TOOLS

    def test_count(self):
        """_SOURCE_SCOPED_TOOLS should have exactly 18 entries."""
        assert len(ToolExecutorService._SOURCE_SCOPED_TOOLS) == 18

    def test_is_a_set(self):
        """_SOURCE_SCOPED_TOOLS should be a set for O(1) lookup."""
        assert isinstance(ToolExecutorService._SOURCE_SCOPED_TOOLS, set)


class TestToolHandlerWiring:
    """Test all expected tools are registered in _tool_handlers."""

    EXPECTED_HANDLERS = {
        # GraphRAG
        "graphrag_search",
        # Node operations
        "search_nodes",
        "search_chunks",
        "get_node",
        "get_node_context",
        "resolve_node",
        "create_node",
        "update_node",
        "delete_node",
        # Edge operations
        "create_edge",
        "list_edges",
        "get_node_edges",
        # Template operations
        "list_templates",
        "search_templates",
        "create_template",
        "delete_template",
        # Graph analytics
        "analyze_graph_structure",
        "find_shortest_path",
        "find_similar_nodes",
        "traverse_path",
        # Summarization
        "summarize",
        "get_summary_context",
        # Research tools
        "extract_entities_from_text",
        "research_topic",
        "build_topic_hierarchy",
        "identify_knowledge_gaps",
    }

    def test_all_expected_tools_registered(self, executor):
        """All 26 expected tools should be in _tool_handlers."""
        registered = set(executor._tool_handlers.keys())
        missing = self.EXPECTED_HANDLERS - registered
        assert not missing, f"Missing handlers: {missing}"

    def test_no_unexpected_tools_registered(self, executor):
        """No extra tools should be registered beyond the expected set."""
        registered = set(executor._tool_handlers.keys())
        extra = registered - self.EXPECTED_HANDLERS
        assert not extra, f"Unexpected handlers: {extra}"

    def test_handler_count(self, executor):
        """Should have exactly 26 registered tool handlers."""
        assert len(executor._tool_handlers) == 26

    def test_all_handlers_are_callable(self, executor):
        """Every registered handler should be callable."""
        for name, handler in executor._tool_handlers.items():
            assert callable(handler), f"Handler for '{name}' is not callable"
