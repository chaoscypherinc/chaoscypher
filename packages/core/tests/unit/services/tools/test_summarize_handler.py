# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the summarize handler end-to-end flow."""

import base64
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.summarize_handlers import (
    SummarizeToolHandlers,
)


def _make_chunk(index: int, source_id: str = "src1") -> dict:
    """Create a mock chunk dict."""
    emb = np.random.randn(8).astype(np.float32)
    encoded = base64.b64encode(emb.tobytes()).decode("utf-8")
    return {
        "id": f"chunk-{source_id}-{index}",
        "chunk_index": index,
        "content": f"Content about topic from chunk {index} of {source_id}.",
        "embedding": encoded,
        "source_id": source_id,
        "page_number": index + 1,
        "section": None,
        "chunk_metadata": {},
    }


def _make_settings(context_window: int = 65536):
    """Create mock settings large enough to stuff all test chunks."""
    settings = MagicMock()
    settings.llm.ai_context_window = context_window
    settings.llm.ai_max_tokens = 800
    settings.chat.tools_token_estimate = 2000
    settings.chunking.small_chunk_size = 900
    return settings


@pytest.fixture
def mock_indexing():
    """Mock indexing repository returning chunks by source."""
    repo = MagicMock()
    chunks = [_make_chunk(i) for i in range(10)]
    repo.get_chunks_by_source.return_value = (chunks, len(chunks))
    return repo


@pytest.fixture
def mock_search():
    """Mock search repository returning chunk IDs."""
    repo = MagicMock()
    # vector_search is sync (no AsyncMock); _retrieve_by_query awaits the
    # embedding callback then calls vector_search synchronously.
    repo.vector_search = MagicMock(
        return_value=[(f"chunk:chunk-src1-{i}", 0.9 - i * 0.05) for i in range(10)]
    )
    return repo


@pytest.fixture
def mock_llm_chat():
    """Mock LLM chat callback returning a summary."""
    return AsyncMock(return_value={"content": "This is a summary of the content."})


@pytest.fixture
def mock_embedding():
    """Mock embedding callback."""
    return AsyncMock(return_value={"embedding": [0.1] * 8})


class TestSummarizeHandler:
    """Test the summarize() method end-to-end."""

    @pytest.mark.asyncio
    async def test_summarize_by_source(
        self, mock_indexing, mock_search, mock_llm_chat, mock_embedding
    ):
        """Should retrieve chunks by source and return summary."""
        handlers = SummarizeToolHandlers(
            indexing_repository=mock_indexing,
            search_repository=mock_search,
            llm_chat_callback=mock_llm_chat,
            embedding_callback=mock_embedding,
            settings=_make_settings(),
        )
        result = await handlers.summarize(
            query="full document",
            source_ids=["src1"],
        )
        assert result["success"] is True
        assert "summary" in result
        assert result["chunks_analyzed"] == 10
        mock_indexing.get_chunks_by_source.assert_called()
        mock_llm_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_summarize_by_query(
        self, mock_indexing, mock_search, mock_llm_chat, mock_embedding
    ):
        """Should retrieve chunks by search when no source_ids."""
        mock_indexing.get_chunk_by_id.side_effect = lambda cid: _make_chunk(
            int(cid.split("-")[-1]), source_id="src1"
        )

        handlers = SummarizeToolHandlers(
            indexing_repository=mock_indexing,
            search_repository=mock_search,
            llm_chat_callback=mock_llm_chat,
            embedding_callback=mock_embedding,
            settings=_make_settings(),
        )
        result = await handlers.summarize(query="the character Anna")
        assert result["success"] is True
        assert "summary" in result
        mock_search.vector_search.assert_called()

    @pytest.mark.asyncio
    async def test_summarize_returns_metadata(
        self, mock_indexing, mock_search, mock_llm_chat, mock_embedding
    ):
        """Result should include strategy and chunk counts."""
        handlers = SummarizeToolHandlers(
            indexing_repository=mock_indexing,
            search_repository=mock_search,
            llm_chat_callback=mock_llm_chat,
            embedding_callback=mock_embedding,
            settings=_make_settings(),
        )
        result = await handlers.summarize(query="full document", source_ids=["src1"])
        assert "strategy" in result
        assert "chunks_selected" in result
        assert "sources_used" in result

    @pytest.mark.asyncio
    async def test_summarize_no_chunks_found(self, mock_search, mock_llm_chat, mock_embedding):
        """Should return error when no chunks found."""
        mock_indexing = MagicMock()
        mock_indexing.get_chunks_by_source.return_value = ([], 0)

        handlers = SummarizeToolHandlers(
            indexing_repository=mock_indexing,
            search_repository=mock_search,
            llm_chat_callback=mock_llm_chat,
            embedding_callback=mock_embedding,
            settings=_make_settings(),
        )
        result = await handlers.summarize(query="full document", source_ids=["src1"])
        assert result["success"] is False
        mock_llm_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_summarize_multi_source_comparison(
        self, mock_search, mock_llm_chat, mock_embedding
    ):
        """Should cluster per source for multi-doc comparison."""
        mock_indexing = MagicMock()

        def get_by_source(source_id, page=1, page_size=200, include_embeddings=True):
            chunks = [_make_chunk(i, source_id=source_id) for i in range(10)]
            return (chunks, len(chunks))

        mock_indexing.get_chunks_by_source.side_effect = get_by_source

        handlers = SummarizeToolHandlers(
            indexing_repository=mock_indexing,
            search_repository=mock_search,
            llm_chat_callback=mock_llm_chat,
            embedding_callback=mock_embedding,
            settings=_make_settings(context_window=8192),  # Force clustering
        )
        result = await handlers.summarize(
            query="compare themes",
            source_ids=["docA", "docB"],
        )
        assert result["success"] is True
        assert len(result["sources_used"]) == 2
