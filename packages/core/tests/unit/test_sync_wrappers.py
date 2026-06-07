# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for synchronous wrapper functions."""

from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.models import EmbedResult, ExtractionResult, LLMChatResponse


@pytest.mark.unit
@pytest.mark.core
class TestExtractSync:
    """Tests for extract_sync()."""

    def test_extract_sync_delegates_to_extract(self):
        """extract_sync() calls extract() and returns the result."""
        from chaoscypher_core import extract_sync

        mock_result = ExtractionResult(entities=[], relationships=[])
        with patch("chaoscypher_core.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = mock_result
            result = extract_sync("some text")
            assert result is mock_result
            mock_extract.assert_called_once_with("some text")

    def test_extract_sync_forwards_kwargs(self):
        """extract_sync() passes keyword arguments through."""
        from chaoscypher_core import extract_sync

        mock_result = ExtractionResult(entities=[], relationships=[])
        with patch("chaoscypher_core.extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = mock_result
            extract_sync("text", analysis_depth="quick")
            mock_extract.assert_called_once_with("text", analysis_depth="quick")


@pytest.mark.unit
@pytest.mark.core
class TestChatSync:
    """Tests for chat_sync()."""

    def test_chat_sync_delegates_to_chat(self):
        """chat_sync() calls chat() and returns the result."""
        from chaoscypher_core import chat_sync

        mock_result = LLMChatResponse(content="Hello!", provider="ollama")
        with patch("chaoscypher_core.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = mock_result
            result = chat_sync("Hello")
            assert result is mock_result
            mock_chat.assert_called_once_with("Hello")

    def test_chat_sync_forwards_kwargs(self):
        """chat_sync() passes keyword arguments through."""
        from chaoscypher_core import chat_sync

        mock_result = LLMChatResponse(content="Hi!", provider="ollama")
        with patch("chaoscypher_core.chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = mock_result
            chat_sync("Hi", stream=True, temperature=0.5)
            mock_chat.assert_called_once_with("Hi", stream=True, temperature=0.5)


@pytest.mark.unit
@pytest.mark.core
class TestEmbedSync:
    """Tests for embed_sync()."""

    def test_embed_sync_delegates_to_embed(self):
        """embed_sync() calls embed() and returns the result."""
        from chaoscypher_core import embed_sync

        mock_result = EmbedResult(embedding=[0.1, 0.2], provider="ollama")
        with patch("chaoscypher_core.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = mock_result
            result = embed_sync("text")
            assert result is mock_result
            mock_embed.assert_called_once_with("text")

    def test_embed_sync_forwards_kwargs(self):
        """embed_sync() passes keyword arguments through."""
        from chaoscypher_core import embed_sync

        mock_result = EmbedResult(embedding=[0.1], provider="ollama")
        with patch("chaoscypher_core.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = mock_result
            embed_sync(["a", "b"], batch_size=10)
            mock_embed.assert_called_once_with(["a", "b"], batch_size=10)
