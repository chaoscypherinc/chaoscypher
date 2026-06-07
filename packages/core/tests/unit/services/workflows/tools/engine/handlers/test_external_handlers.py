# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ExternalToolHandlers.

Covers callback availability checks, delegation to research
callbacks, default arguments, and error handling via the @tool_handler decorator.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.external_handlers import (
    ExternalToolHandlers,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def research_callback() -> AsyncMock:
    """Async callback simulating a research agent."""
    return AsyncMock(return_value={"success": True, "data": "research output"})


def _make_handler(
    research_callback: AsyncMock | None = None,
) -> ExternalToolHandlers:
    """Construct an ``ExternalToolHandlers`` instance from optional callbacks.

    Args:
        research_callback: Optional async research agent callback.

    Returns:
        Configured handler ready for testing.

    """
    return ExternalToolHandlers(
        research_agent_callback=research_callback,
    )


# ===========================================================================
# Extract Entities From Text Tests
# ===========================================================================


class TestExtractEntitiesFromText:
    """Tests for the extract_entities_from_text handler method."""

    @pytest.mark.asyncio
    async def test_no_callback_returns_error(self) -> None:
        """Missing research callback returns an error about no research agent."""
        handler = _make_handler()

        result = await handler.extract_entities_from_text("Some text about Alice and Bob.")

        assert result["success"] is False
        assert "no research agent" in result["error"]

    @pytest.mark.asyncio
    async def test_with_callback_delegates_correctly(
        self,
        research_callback: AsyncMock,
    ) -> None:
        """Callback is invoked with 'extract_entities' action and text kwarg."""
        handler = _make_handler(research_callback=research_callback)

        result = await handler.extract_entities_from_text("Alice knows Bob.")

        research_callback.assert_awaited_once_with("extract_entities", text="Alice knows Bob.")
        assert result == research_callback.return_value

    @pytest.mark.asyncio
    async def test_callback_exception_caught_by_decorator(self) -> None:
        """Exception in callback is caught by @tool_handler."""
        failing_callback = AsyncMock(side_effect=ValueError("parse error"))
        handler = _make_handler(research_callback=failing_callback)

        result = await handler.extract_entities_from_text("bad text")

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# Research Topic Tests
# ===========================================================================


class TestResearchTopic:
    """Tests for the research_topic handler method."""

    @pytest.mark.asyncio
    async def test_no_callback_returns_error(self) -> None:
        """Missing research callback returns an error about no research agent."""
        handler = _make_handler()

        result = await handler.research_topic("quantum computing")

        assert result["success"] is False
        assert "no research agent" in result["error"]

    @pytest.mark.asyncio
    async def test_with_callback_delegates_correctly(
        self,
        research_callback: AsyncMock,
    ) -> None:
        """Callback is invoked with topic and explicit depth."""
        handler = _make_handler(research_callback=research_callback)

        result = await handler.research_topic("quantum computing", depth="brief")

        research_callback.assert_awaited_once_with(
            "research_topic", topic="quantum computing", depth="brief"
        )
        assert result == research_callback.return_value

    @pytest.mark.asyncio
    async def test_default_depth_is_full(
        self,
        research_callback: AsyncMock,
    ) -> None:
        """Default depth parameter is 'full' when not specified."""
        handler = _make_handler(research_callback=research_callback)

        await handler.research_topic("machine learning")

        research_callback.assert_awaited_once_with(
            "research_topic", topic="machine learning", depth="full"
        )

    @pytest.mark.asyncio
    async def test_callback_exception_caught_by_decorator(self) -> None:
        """Exception in callback is caught by @tool_handler."""
        failing_callback = AsyncMock(side_effect=RuntimeError("agent unavailable"))
        handler = _make_handler(research_callback=failing_callback)

        result = await handler.research_topic("topic")

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# Build Topic Hierarchy Tests
# ===========================================================================


class TestBuildTopicHierarchy:
    """Tests for the build_topic_hierarchy handler method."""

    @pytest.mark.asyncio
    async def test_no_callback_returns_error(self) -> None:
        """Missing research callback returns an error about no research agent."""
        handler = _make_handler()

        result = await handler.build_topic_hierarchy("artificial intelligence")

        assert result["success"] is False
        assert "no research agent" in result["error"]

    @pytest.mark.asyncio
    async def test_with_callback_delegates_correctly(
        self,
        research_callback: AsyncMock,
    ) -> None:
        """Callback is invoked with 'build_hierarchy' action and root_topic kwarg."""
        handler = _make_handler(research_callback=research_callback)

        result = await handler.build_topic_hierarchy("artificial intelligence")

        research_callback.assert_awaited_once_with(
            "build_hierarchy", root_topic="artificial intelligence"
        )
        assert result == research_callback.return_value

    @pytest.mark.asyncio
    async def test_callback_exception_caught_by_decorator(self) -> None:
        """Exception in callback is caught by @tool_handler."""
        failing_callback = AsyncMock(side_effect=TypeError("bad argument"))
        handler = _make_handler(research_callback=failing_callback)

        result = await handler.build_topic_hierarchy("topic")

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# Identify Knowledge Gaps Tests
# ===========================================================================


class TestIdentifyKnowledgeGaps:
    """Tests for the identify_knowledge_gaps handler method."""

    @pytest.mark.asyncio
    async def test_no_callback_returns_error(self) -> None:
        """Missing research callback returns an error about no research agent."""
        handler = _make_handler()

        result = await handler.identify_knowledge_gaps()

        assert result["success"] is False
        assert "no research agent" in result["error"]

    @pytest.mark.asyncio
    async def test_with_callback_delegates_correctly(
        self,
        research_callback: AsyncMock,
    ) -> None:
        """Callback is invoked with 'identify_gaps' action and no extra kwargs."""
        handler = _make_handler(research_callback=research_callback)

        result = await handler.identify_knowledge_gaps()

        research_callback.assert_awaited_once_with("identify_gaps")
        assert result == research_callback.return_value

    @pytest.mark.asyncio
    async def test_callback_exception_caught_by_decorator(self) -> None:
        """Exception in callback is caught by @tool_handler."""
        failing_callback = AsyncMock(side_effect=ConnectionError("lost connection"))
        handler = _make_handler(research_callback=failing_callback)

        result = await handler.identify_knowledge_gaps()

        assert result["success"] is False
        assert result["error"] == "Operation failed"
