# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCPToolBridge."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.mcp.bridge import MCPToolBridge
from chaoscypher_core.utils.tool_failure import TOOL_FAILURES


@pytest.fixture
def mock_executor():
    """Create a mock ToolExecutorService."""
    executor = MagicMock()
    executor.execute_tool = AsyncMock()
    return executor


@pytest.fixture
def bridge(mock_executor):
    """Create an MCPToolBridge with mock executor."""
    return MCPToolBridge(tool_executor=mock_executor)


class TestBridgeExecute:
    """MCPToolBridge.execute() translation."""

    @pytest.mark.asyncio
    async def test_success_result(self, bridge, mock_executor):
        mock_executor.execute_tool.return_value = {
            "success": True,
            "count": 5,
            "nodes": [],
        }
        result = await bridge.execute("search_nodes", {"query": "test"})
        assert result.is_error is False
        assert '"success": true' in result.text

    @pytest.mark.asyncio
    async def test_error_result(self, bridge, mock_executor):
        mock_executor.execute_tool.return_value = {
            "success": False,
            "error": "Node not found",
        }
        result = await bridge.execute("get_node", {"node_id": "bad"})
        assert result.is_error is True
        assert "Node not found" in result.text

    @pytest.mark.asyncio
    async def test_executor_exception(self, bridge, mock_executor):
        mock_executor.execute_tool.side_effect = Exception("kaboom")
        result = await bridge.execute("get_node", {"node_id": "x"})
        assert result.is_error is True
        assert TOOL_FAILURES["OPERATION_FAILED"] in result.text

    @pytest.mark.asyncio
    async def test_passes_params_to_executor(self, bridge, mock_executor):
        mock_executor.execute_tool.return_value = {"success": True}
        await bridge.execute("search_nodes", {"query": "alice", "limit": 5})
        mock_executor.execute_tool.assert_called_once_with(
            "search_nodes", {"query": "alice", "limit": 5}
        )

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, bridge, mock_executor):
        """The real executor answers ``{"error": ...}`` with no ``success`` key.

        The old check (``not result.get("success", True)``) read that as a
        success; the old test mocked a KeyError the executor never raises.
        """
        mock_executor.execute_tool.return_value = {"error": "Unknown tool: not_a_tool"}
        result = await bridge.execute("not_a_tool", {})
        assert result.is_error is True
        assert "Unknown tool" in result.text

    @pytest.mark.asyncio
    async def test_invalid_parameters_shape_is_an_error(self, bridge, mock_executor):
        mock_executor.execute_tool.return_value = {"error": "Invalid parameters for search_nodes"}
        result = await bridge.execute("search_nodes", {})
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_payload_with_error_key_but_success_true_is_not_an_error(
        self, bridge, mock_executor
    ):
        mock_executor.execute_tool.return_value = {"success": True, "error": None}
        result = await bridge.execute("search_nodes", {"query": "x"})
        assert result.is_error is False


class TestGetSummaryContext:
    """get_summary_context returns clustered chunks without LLM call."""

    @pytest.mark.asyncio
    async def test_returns_chunks_without_llm(self, bridge, mock_executor):
        mock_executor.execute_tool.return_value = {
            "success": True,
            "strategy": "stuff",
            "chunks_analyzed": 3,
            "chunks_selected": 3,
            "sources_used": ["test.pdf"],
            "chunks": [{"chunk_id": "c1", "content": "text"}],
        }
        result = await bridge.execute("get_summary_context", {"query": "test"})
        assert result.is_error is False
        assert '"strategy"' in result.text
        assert '"chunks"' in result.text
