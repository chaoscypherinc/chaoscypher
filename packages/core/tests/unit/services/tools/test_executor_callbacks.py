# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ToolExecutorService callback injection."""

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
def mock_embedding_callback():
    """Create mock embedding callback."""
    return AsyncMock(return_value={"embedding": [0.1, 0.2, 0.3]})


@pytest.fixture
def mock_llm_chat_callback():
    """Create mock LLM chat callback."""
    return AsyncMock(return_value={"content": "Summary text"})


class TestToolExecutorServiceCallbacks:
    """Test that ToolExecutorService accepts and routes callbacks correctly."""

    def test_init_accepts_callbacks(
        self, mock_graph_repo, mock_search_repo, mock_embedding_callback, mock_llm_chat_callback
    ):
        """ToolExecutorService should accept embedding_callback and llm_chat_callback."""
        executor = ToolExecutorService(
            graph_repository=mock_graph_repo,
            search_repository=mock_search_repo,
            embedding_callback=mock_embedding_callback,
            llm_chat_callback=mock_llm_chat_callback,
        )
        assert executor is not None

    def test_node_handlers_receive_embedding_callback(
        self, mock_graph_repo, mock_search_repo, mock_embedding_callback
    ):
        """NodeToolHandlers should receive the embedding_callback."""
        executor = ToolExecutorService(
            graph_repository=mock_graph_repo,
            search_repository=mock_search_repo,
            embedding_callback=mock_embedding_callback,
        )
        assert executor.node_handlers.embedding_callback is mock_embedding_callback

    def test_backwards_compatible_without_callbacks(self, mock_graph_repo, mock_search_repo):
        """ToolExecutorService should work without callbacks (None defaults)."""
        executor = ToolExecutorService(
            graph_repository=mock_graph_repo,
            search_repository=mock_search_repo,
        )
        assert executor.node_handlers.embedding_callback is None
