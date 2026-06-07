# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for summarize tool budget calculation."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from chaoscypher_core.services.workflows.tools.engine.handlers.summarize_handlers import (
    SummarizeToolHandlers,
)


def _make_settings(
    context_window: int = 16384,
    chunk_size: int = 900,
    max_tokens: int = 800,
):
    """Create settings with explicit attributes (no MagicMock auto-create).

    Uses SimpleNamespace for sub-objects so accessing undefined attributes
    raises AttributeError instead of silently returning a new Mock.
    """
    return SimpleNamespace(
        llm=SimpleNamespace(ai_context_window=context_window, ai_max_tokens=max_tokens),
        chat=SimpleNamespace(tools_token_estimate=2000),
        chunking=SimpleNamespace(small_chunk_size=chunk_size),
    )


def _make_handlers(
    context_window: int = 16384,
    chunk_size: int = 900,
    max_tokens: int = 800,
):
    """Create handlers with constrained settings."""
    return SummarizeToolHandlers(
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        llm_chat_callback=MagicMock(),
        embedding_callback=MagicMock(),
        settings=_make_settings(
            context_window=context_window,
            chunk_size=chunk_size,
            max_tokens=max_tokens,
        ),
    )


class TestBudgetCalculation:
    """Test auto-scaling budget computation from settings."""

    def test_small_context_window(self):
        """8k context should allow ~26 chunks."""
        handlers = _make_handlers(context_window=8192)
        budget = handlers._compute_budget(prompt_text="Summarize the following:")
        # 8192 - prompt_tokens - 800 (output) - 2000 (tools) = ~5300 usable
        # 5300 / (900/4) = ~23 chunks
        assert 15 <= budget.max_chunks <= 30

    def test_large_context_window(self):
        """64k context should allow 200+ chunks."""
        handlers = _make_handlers(context_window=65536)
        budget = handlers._compute_budget(prompt_text="Summarize the following:")
        assert budget.max_chunks > 200

    def test_strategy_stuff_when_fits(self):
        """Should choose STUFF when chunks fit in budget."""
        handlers = _make_handlers(context_window=65536)
        budget = handlers._compute_budget(prompt_text="Summarize the following:")
        strategy, k = handlers._select_strategy(num_chunks=50, budget=budget)
        assert strategy == "stuff"
        assert k == 50

    def test_strategy_cluster_when_exceeds(self):
        """Should choose CLUSTER when chunks exceed budget."""
        handlers = _make_handlers(context_window=8192)
        budget = handlers._compute_budget(prompt_text="Summarize the following:")
        strategy, k = handlers._select_strategy(num_chunks=200, budget=budget)
        assert strategy == "cluster"
        assert k == budget.max_chunks

    def test_max_tokens_exceeds_context_window(self):
        """Output reserve should be capped so budget stays positive."""
        # Real-world scenario: ai_max_tokens=65536, context_window=16384
        handlers = _make_handlers(context_window=16384, max_tokens=65536)
        budget = handlers._compute_budget(prompt_text="Summarize the following:")
        # Output reserve capped at 25% of 16384 = 4096
        # 16384 - ~5 prompt - 4096 - 2000 = ~10283 usable
        # 10283 / 225 = ~45 chunks
        assert budget.max_chunks > 0
        assert budget.usable_tokens > 0

    def test_strategy_at_least_one_chunk(self):
        """Strategy should always allow at least 1 chunk even with tiny budget."""
        handlers = _make_handlers(context_window=4096, max_tokens=65536)
        budget = handlers._compute_budget(prompt_text="Summarize the following:")
        _strategy, k = handlers._select_strategy(num_chunks=100, budget=budget)
        assert k >= 1

    def test_missing_attribute_raises(self):
        """Verify SimpleNamespace raises on undefined attributes."""
        settings = _make_settings()
        # Accessing a non-existent attribute should fail
        try:
            _ = settings.chat.nonexistent_field
            raise AssertionError("Should have raised AttributeError")
        except AttributeError:
            pass
