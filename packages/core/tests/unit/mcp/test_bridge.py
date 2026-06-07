# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCPToolBridge."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.mcp.bridge import MCPToolBridge


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
        assert "Tool execution failed" in result.text

    @pytest.mark.asyncio
    async def test_passes_params_to_executor(self, bridge, mock_executor):
        mock_executor.execute_tool.return_value = {"success": True}
        await bridge.execute("search_nodes", {"query": "alice", "limit": 5})
        mock_executor.execute_tool.assert_called_once_with(
            "search_nodes", {"query": "alice", "limit": 5}
        )

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, bridge, mock_executor):
        mock_executor.execute_tool.side_effect = KeyError("not_a_tool")
        result = await bridge.execute("not_a_tool", {})
        assert result.is_error is True


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
