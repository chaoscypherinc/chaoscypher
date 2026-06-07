# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ChaosCypher/CC namespace facade."""

from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.models import (
    EmbedResult,
    EngineSearchResult,
    ExtractionResult,
    LLMChatResponse,
    ProcessingResult,
)


@pytest.mark.unit
@pytest.mark.core
class TestChaosCypherNamespace:
    """Tests for ChaosCypher static namespace class."""

    @pytest.mark.asyncio
    async def test_extract_delegates(self):
        """ChaosCypher.extract() delegates to module-level extract()."""
        from chaoscypher_core import ChaosCypher

        mock_result = ExtractionResult(entities=[], relationships=[])
        with patch("chaoscypher_core.extract", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            result = await ChaosCypher.extract("paper.pdf")
            assert result is mock_result
            mock.assert_called_once_with("paper.pdf")

    @pytest.mark.asyncio
    async def test_chat_delegates(self):
        """ChaosCypher.chat() delegates to module-level chat()."""
        from chaoscypher_core import ChaosCypher

        mock_result = LLMChatResponse(content="Hello!", provider="ollama")
        with patch("chaoscypher_core.chat", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            result = await ChaosCypher.chat("Hello")
            assert result is mock_result

    @pytest.mark.asyncio
    async def test_embed_delegates(self):
        """ChaosCypher.embed() delegates to module-level embed()."""
        from chaoscypher_core import ChaosCypher

        mock_result = EmbedResult(embedding=[0.1, 0.2], provider="ollama")
        with patch("chaoscypher_core.embed", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            result = await ChaosCypher.embed("text")
            assert result is mock_result

    @pytest.mark.asyncio
    async def test_search_delegates(self):
        """ChaosCypher.search() delegates to module-level search()."""
        from chaoscypher_core import ChaosCypher

        mock_results = [EngineSearchResult(label="Test", score=0.9, result_type="node", id="n1")]
        with patch("chaoscypher_core.search", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            results = await ChaosCypher.search("query", database="demo")
            assert results == mock_results
            mock.assert_called_once_with("query", database="demo")

    @pytest.mark.asyncio
    async def test_add_document_delegates(self):
        """ChaosCypher.add_document() delegates to module-level add_document()."""
        from chaoscypher_core import ChaosCypher

        mock_result = ProcessingResult(source_id="s1", nodes=["n1"])
        with patch("chaoscypher_core.add_document", new_callable=AsyncMock) as mock:
            mock.return_value = mock_result
            result = await ChaosCypher.add_document("paper.pdf")
            assert result.source_id == "s1"

    def test_extract_sync_delegates(self):
        """ChaosCypher.extract_sync() delegates to module-level extract_sync()."""
        from chaoscypher_core import ChaosCypher

        mock_result = ExtractionResult(entities=[], relationships=[])
        with patch("chaoscypher_core.extract_sync") as mock:
            mock.return_value = mock_result
            result = ChaosCypher.extract_sync("text")
            assert result is mock_result

    def test_chat_sync_delegates(self):
        """ChaosCypher.chat_sync() delegates to module-level chat_sync()."""
        from chaoscypher_core import ChaosCypher

        mock_result = LLMChatResponse(content="Hi!", provider="ollama")
        with patch("chaoscypher_core.chat_sync") as mock:
            mock.return_value = mock_result
            result = ChaosCypher.chat_sync("Hello")
            assert result is mock_result

    def test_embed_sync_delegates(self):
        """ChaosCypher.embed_sync() delegates to module-level embed_sync()."""
        from chaoscypher_core import ChaosCypher

        mock_result = EmbedResult(embedding=[0.1], provider="ollama")
        with patch("chaoscypher_core.embed_sync") as mock:
            mock.return_value = mock_result
            result = ChaosCypher.embed_sync("text")
            assert result is mock_result

    def test_search_sync_delegates(self):
        """ChaosCypher.search_sync() delegates to module-level search_sync()."""
        from chaoscypher_core import ChaosCypher

        with patch("chaoscypher_core.search_sync") as mock:
            mock.return_value = []
            result = ChaosCypher.search_sync("query")
            assert result == []

    def test_add_document_sync_delegates(self):
        """ChaosCypher.add_document_sync() delegates to module-level add_document_sync()."""
        from chaoscypher_core import ChaosCypher

        mock_result = ProcessingResult(source_id="s1")
        with patch("chaoscypher_core.add_document_sync") as mock:
            mock.return_value = mock_result
            result = ChaosCypher.add_document_sync("paper.pdf")
            assert result.source_id == "s1"


@pytest.mark.unit
@pytest.mark.core
class TestCCAlias:
    """Tests for CC alias."""

    def test_cc_is_chaoscypher(self):
        """CC is an alias for ChaosCypher."""
        from chaoscypher_core import CC, ChaosCypher

        assert CC is ChaosCypher

    def test_cc_has_all_methods(self):
        """CC has all expected static methods."""
        from chaoscypher_core import CC

        assert hasattr(CC, "configure")
        assert hasattr(CC, "reset")
        assert hasattr(CC, "extract")
        assert hasattr(CC, "chat")
        assert hasattr(CC, "embed")
        assert hasattr(CC, "search")
        assert hasattr(CC, "add_document")
        assert hasattr(CC, "extract_sync")
        assert hasattr(CC, "chat_sync")
        assert hasattr(CC, "embed_sync")
        assert hasattr(CC, "search_sync")
        assert hasattr(CC, "add_document_sync")


@pytest.mark.unit
@pytest.mark.core
class TestChaosCypherConfigure:
    """Tests for ChaosCypher.configure() and reset()."""

    def setup_method(self):
        """Ensure clean state before each test."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.reset()

    def teardown_method(self):
        """Ensure clean state after each test."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.reset()

    def test_configure_stores_settings(self):
        """configure() caches settings for convenience functions."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.configure(provider="openai", api_key="sk-test")

        import chaoscypher_core.facade as _facade

        assert _facade._default_settings is not None
        assert _facade._default_settings.llm.chat_provider == "openai"
        assert _facade._default_settings.llm.openai_api_key.get_secret_value() == "sk-test"

    def test_configure_anthropic(self):
        """configure() correctly maps anthropic provider."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.configure(provider="anthropic", api_key="sk-ant-test")

        import chaoscypher_core.facade as _facade

        assert _facade._default_settings.llm.chat_provider == "anthropic"
        assert _facade._default_settings.llm.anthropic_api_key.get_secret_value() == "sk-ant-test"

    def test_configure_gemini(self):
        """configure() correctly maps gemini provider."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.configure(provider="gemini", api_key="gem-test")

        import chaoscypher_core.facade as _facade

        assert _facade._default_settings.llm.chat_provider == "gemini"
        assert _facade._default_settings.llm.gemini_api_key.get_secret_value() == "gem-test"

    def test_configure_with_engine_settings(self):
        """configure() accepts a full EngineSettings instance."""
        from chaoscypher_core import EngineSettings
        from chaoscypher_core.facade import ChaosCypher

        custom = EngineSettings(llm={"chat_provider": "openai", "openai_api_key": "sk-x"})
        ChaosCypher.configure(settings=custom)

        import chaoscypher_core.facade as _facade

        assert _facade._default_settings is custom

    def test_reset_clears_settings(self):
        """reset() removes cached settings."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.configure(provider="openai", api_key="sk-test")
        ChaosCypher.reset()

        import chaoscypher_core.facade as _facade

        assert _facade._default_settings is None


@pytest.mark.unit
@pytest.mark.core
class TestGetDefaultSettings:
    """Tests for _get_default_settings() helper."""

    def setup_method(self):
        """Ensure clean state before each test."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.reset()

    def teardown_method(self):
        """Ensure clean state after each test."""
        from chaoscypher_core.facade import ChaosCypher

        ChaosCypher.reset()

    def test_returns_cached_settings_when_configured(self):
        """_get_default_settings() returns cached settings after configure()."""
        from chaoscypher_core.facade import ChaosCypher, _get_default_settings

        ChaosCypher.configure(provider="openai", api_key="sk-test")
        settings = _get_default_settings()
        assert settings.llm.chat_provider == "openai"

    def test_returns_fresh_settings_when_not_configured(self):
        """_get_default_settings() returns EngineSettings() when not configured."""
        from chaoscypher_core.facade import _get_default_settings

        settings = _get_default_settings()
        # Default is ollama (unless CHAOSCYPHER_LLM_PROVIDER env var is set)
        assert settings is not None
