# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The semantic dedup threshold has one source per meaning (audit E5/E9).

The audit found four: the filtering preset's ``semantic_dedup_threshold``
(the effective value on every extraction path), a domain's
``extraction_limits.semantic_dedup_threshold`` (overrides the preset),
``extraction.semantic_dedup_threshold`` (the fallback for a caller that
resolves no preset), and ``source_processing.entity_deduplication_similarity_threshold``,
which nothing read and is gone.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    plan_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)
from chaoscypher_core.settings import EngineSettings, SourceProcessingSettings


_SLIDER = ("unfiltered", "minimal", "lenient", "balanced", "strict", "maximum")


def test_the_dead_source_processing_threshold_is_gone_but_old_files_still_parse() -> None:
    """No field; a settings file that still carries the key loads (the key is dropped)."""
    assert "entity_deduplication_similarity_threshold" not in SourceProcessingSettings.model_fields
    parsed = SourceProcessingSettings.model_validate(
        {"entity_deduplication_similarity_threshold": 0.9, "entity_deduplication_mode": "exact"}
    )
    assert parsed.entity_deduplication_mode == "exact"
    assert not hasattr(parsed, "entity_deduplication_similarity_threshold")


def test_the_preset_threshold_steps_with_the_slider() -> None:
    """Each preset has its own threshold, tightening as the slider loosens merging."""
    values = [resolve_filtering_config(mode).semantic_dedup_threshold for mode in _SLIDER]
    assert values == sorted(values, reverse=True)
    assert len(set(values)) == len(values)
    assert resolve_filtering_config("balanced").semantic_dedup_threshold == pytest.approx(0.90)


def test_a_domain_limit_overrides_the_preset() -> None:
    """``extraction_limits.semantic_dedup_threshold`` is an override, not a fourth default."""
    assert plan_filtering_config(
        "balanced", {"semantic_dedup_threshold": 0.8}
    ).semantic_dedup_threshold == pytest.approx(0.8)


def test_the_settings_fallback_is_not_any_preset_value() -> None:
    """The no-preset fallback differs from the balanced preset, so a path that lost its preset shows."""
    fallback = EngineSettings().extraction.semantic_dedup_threshold
    assert fallback != resolve_filtering_config("balanced").semantic_dedup_threshold
