# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for callback creation in setup_chat_providers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.streaming.chat import setup_chat_providers


class TestSetupChatProvidersCallbacks:
    """Test the callbacks setup_chat_providers wires into ToolExecutorService.

    The embedding callback is deliberately local (a direct
    ``EmbeddingService.embed`` call, no queue hop); the LLM chat callback
    is the queue-based one. This pins the wiring, not just non-None-ness.
    """

    @pytest.mark.asyncio
    @patch("chaoscypher_core.streaming.chat.utils.get_llm_queue_service")
    @patch("chaoscypher_core.streaming.chat.utils.get_provider_factory")
    @patch("chaoscypher_core.repo_factories.get_embedding_service")
    @patch("chaoscypher_core.app_config.engine_factory.build_engine_settings")
    async def test_tool_executor_receives_working_embedding_callback(
        self,
        mock_build,
        mock_embed_fn,
        mock_factory_fn,
        mock_queue_fn,
    ):
        """The executor's embedding callback must delegate to EmbeddingService.embed."""
        # Setup mocks
        mock_provider = MagicMock()
        mock_factory = MagicMock()
        mock_factory.get_chat_provider.return_value = mock_provider
        mock_factory_fn.return_value = mock_factory

        mock_queue = MagicMock()
        mock_queue_fn.return_value = mock_queue

        mock_embedding_service = MagicMock()
        mock_embedding_service.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
        mock_embed_fn.return_value = mock_embedding_service

        mock_settings = MagicMock()
        mock_settings.priorities.interactive = 10
        mock_build.return_value = MagicMock()

        _provider, executor, _tools = setup_chat_providers(
            settings=mock_settings,
            graph_manager=MagicMock(),
            search_manager=MagicMock(),
            chat_id="test-chat",
        )

        callback = executor.node_handlers.embedding_callback
        assert callback is not None

        # Invoking the callback must hit the wired EmbeddingService and
        # propagate its result — a mis-wired or dropped callback cannot pass.
        result = await callback("hello world")

        mock_embedding_service.embed.assert_awaited_once_with("hello world")
        assert result == [0.1, 0.2, 0.3]
