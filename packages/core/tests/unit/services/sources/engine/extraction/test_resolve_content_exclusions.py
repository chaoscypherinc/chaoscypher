# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for orchestration.resolve_content_exclusions error isolation."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    resolve_content_exclusions,
)


class FakeDomain:
    """Minimal duck-typed stand-in for ConfigurableDomain."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    def get_content_exclusions(self) -> dict[str, Any]:
        return self._config


class TestResolveContentExclusionsIsolation:
    """An invalid category name must not halt extraction."""

    def test_unknown_category_returns_empty_and_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """Unknown category logs WARNING and returns []; extraction can continue."""
        domain = FakeDomain({"categories": ["toc", "not_a_real_category"], "custom_patterns": []})
        with caplog.at_level(logging.WARNING):
            result = resolve_content_exclusions(domain)
        assert result == []
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "unknown_content_category" in combined or "not_a_real_category" in combined

    def test_unknown_category_does_not_raise(self) -> None:
        """The resolver catches KeyError — no exception escapes."""
        domain = FakeDomain({"categories": ["completely_made_up"], "custom_patterns": []})
        # If this raises, the test fails.
        result = resolve_content_exclusions(domain)
        assert result == []

    def test_valid_categories_still_resolve(self) -> None:
        """Known categories resolve normally."""
        domain = FakeDomain({"categories": ["toc", "legal"], "custom_patterns": []})
        result = resolve_content_exclusions(domain)
        assert len(result) == 2

    def test_none_domain_returns_empty(self) -> None:
        """A None domain short-circuits to []."""
        assert resolve_content_exclusions(None) == []
