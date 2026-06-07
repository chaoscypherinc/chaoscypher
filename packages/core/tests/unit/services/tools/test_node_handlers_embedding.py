# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for NodeToolHandlers embedding callback migration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers import (
    NodeToolHandlers,
)


@pytest.fixture
def mock_graph_repo():
    """Create mock graph repository."""
    return MagicMock()


@pytest.fixture
def mock_search_repo():
    """Create mock search repository."""
    repo = MagicMock()
    repo.hybrid_search = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_embedding_callback():
    """Create mock embedding callback."""
    return AsyncMock(return_value={"embedding": [0.1, 0.2, 0.3]})


class TestNodeHandlersEmbeddingCallback:
    """Test that NodeToolHandlers uses embedding_callback instead of direct llm_provider."""

    def test_init_accepts_embedding_callback(
        self, mock_graph_repo, mock_search_repo, mock_embedding_callback
    ):
        """NodeToolHandlers should accept embedding_callback parameter."""
        handlers = NodeToolHandlers(
            graph_repository=mock_graph_repo,
            search_repository=mock_search_repo,
            embedding_callback=mock_embedding_callback,
        )
        assert handlers is not None

    def test_make_embedding_callback_returns_injected_callback(
        self, mock_graph_repo, mock_search_repo, mock_embedding_callback
    ):
        """_make_embedding_callback should return the injected callback."""
        handlers = NodeToolHandlers(
            graph_repository=mock_graph_repo,
            search_repository=mock_search_repo,
            embedding_callback=mock_embedding_callback,
        )
        result = handlers._make_embedding_callback()
        assert result is mock_embedding_callback

    def test_make_embedding_callback_returns_none_when_no_callback(
        self, mock_graph_repo, mock_search_repo
    ):
        """_make_embedding_callback should return None when no callback provided."""
        handlers = NodeToolHandlers(
            graph_repository=mock_graph_repo,
            search_repository=mock_search_repo,
        )
        result = handlers._make_embedding_callback()
        assert result is None
