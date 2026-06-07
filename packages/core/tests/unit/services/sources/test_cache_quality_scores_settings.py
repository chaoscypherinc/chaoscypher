# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: cache_quality_scores forwards the caller's settings to get_domain_registry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_cache_quality_scores_forwards_settings() -> None:
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        cache_quality_scores,
    )

    custom_settings = MagicMock(name="custom_settings")
    fake_registry = MagicMock()
    fake_domain = MagicMock()
    fake_domain.get_quality_scoring.return_value = {"weights": {"a": 1.0}}
    fake_registry.get_domain.return_value = fake_domain

    fake_scorer = MagicMock()
    fake_scorer.get_cacheable_scores.return_value = {
        "cached_quality_grade": "A",
        "cached_quality_label": "Excellent",
        "cached_scores_version": "1",
    }

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=fake_registry,
        ) as registry_factory,
        patch(
            "chaoscypher_core.services.quality.QualityScorer",
            return_value=fake_scorer,
        ),
    ):
        cache_quality_scores(
            adapter=MagicMock(),
            source_id="src_1",
            domain_name="literature",
            entities=[{"name": "Alice", "source_chunks": [0, 1]}],
            relationships=[],
            database_name="default",
            settings=custom_settings,
        )

    # Forwarding contract: caller's settings reach the registry factory.
    registry_factory.assert_called_once()
    call_kwargs = registry_factory.call_args.kwargs
    assert call_kwargs.get("database_name") == "default"
    assert call_kwargs.get("settings") is custom_settings
