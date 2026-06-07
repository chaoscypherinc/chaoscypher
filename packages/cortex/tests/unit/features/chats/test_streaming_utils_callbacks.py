# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for queue callback creation in setup_chat_providers."""

from unittest.mock import MagicMock, patch

from chaoscypher_core.streaming.chat import setup_chat_providers


class TestSetupChatProvidersCallbacks:
    """Test that setup_chat_providers creates queue-based callbacks."""

    @patch("chaoscypher_core.streaming.chat.utils.get_llm_queue_service")
    @patch("chaoscypher_core.streaming.chat.utils.get_provider_factory")
    @patch("chaoscypher_core.repo_factories.get_embedding_service")
    @patch("chaoscypher_core.app_config.engine_factory.build_engine_settings")
    def test_tool_executor_receives_embedding_callback(
        self,
        mock_build,
        _mock_embed_fn,  # noqa: PT019
        mock_factory_fn,
        mock_queue_fn,
    ):
        """ToolExecutorService should receive a queue-based embedding callback."""
        # Setup mocks
        mock_provider = MagicMock()
        mock_factory = MagicMock()
        mock_factory.get_chat_provider.return_value = mock_provider
        mock_factory_fn.return_value = mock_factory

        mock_queue = MagicMock()
        mock_queue_fn.return_value = mock_queue

        mock_settings = MagicMock()
        mock_settings.priorities.interactive = 10
        mock_build.return_value = MagicMock()

        _provider, executor, _tools = setup_chat_providers(
            settings=mock_settings,
            graph_manager=MagicMock(),
            search_manager=MagicMock(),
            chat_id="test-chat",
        )

        # Executor should have embedding callback (not None)
        assert executor.node_handlers.embedding_callback is not None
