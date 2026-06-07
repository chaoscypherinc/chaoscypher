# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for summarize handler registration in ToolExecutorService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.llm.ai_context_window = 16384
    settings.llm.ai_max_tokens = 800
    settings.chat.tools_token_estimate = 2000
    settings.chunking.small_chunk_size = 900
    return settings


class TestExecutorSummarize:
    """Test summarize tool registration in ToolExecutorService."""

    def test_summarize_in_tool_handlers(self):
        """Executor should have 'summarize' in _tool_handlers."""
        executor = ToolExecutorService(
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            indexing_repository=MagicMock(),
            llm_chat_callback=AsyncMock(),
            embedding_callback=AsyncMock(),
            search_settings=MagicMock(),
        )
        assert "summarize" in executor._tool_handlers

    def test_summarize_in_source_scoped_tools(self):
        """'summarize' should be in _SOURCE_SCOPED_TOOLS."""
        assert "summarize" in ToolExecutorService._SOURCE_SCOPED_TOOLS

    @pytest.mark.asyncio
    async def test_execute_summarize_tool(self):
        """execute_tool('summarize', ...) should dispatch to summarize handler."""
        mock_llm = AsyncMock(return_value={"content": "A summary."})
        mock_indexing = MagicMock()
        mock_indexing.get_chunks_by_source.return_value = ([], 0)

        executor = ToolExecutorService(
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            indexing_repository=mock_indexing,
            llm_chat_callback=mock_llm,
            embedding_callback=AsyncMock(),
            search_settings=MagicMock(),
        )
        result = await executor.execute_tool("summarize", {"query": "test"})
        assert isinstance(result, dict)
