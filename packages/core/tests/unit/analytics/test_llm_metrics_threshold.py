# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Outlier-threshold resolution for ``compute_metrics_summary`` (Task C6).

The std-dev outlier threshold no longer reads the app singleton. It resolves
to an explicit ``outlier_std_dev_threshold`` parameter when supplied, else to
``QualitySettings().llm_outlier_std_dev_threshold`` (the class default).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from chaoscypher_core.analytics.llm_metrics import compute_metrics_summary
from chaoscypher_core.settings import QualitySettings


def _attempt(duration_ms: int) -> dict[str, object]:
    return {
        "success": True,
        "was_retry": False,
        "input_tokens": 1,
        "output_tokens": 1,
        "duration_ms": duration_ms,
    }


def _summary(threshold: float | None, durations: list[int]) -> dict[str, object]:
    return compute_metrics_summary(
        attempts=[_attempt(d) for d in durations],
        provider="ollama",
        model="test",
        outlier_std_dev_threshold=threshold,
    )


# durations chosen so exactly one point is a clear outlier under a tight
# threshold but not under a loose one.
_DURATIONS = [100, 100, 100, 100, 100, 100, 100, 100, 100, 1000]


def test_tight_threshold_flags_outlier() -> None:
    """A low explicit threshold flags the spike as an outlier."""
    summary = _summary(0.5, _DURATIONS)
    assert summary["outlier_count"] == 1


def test_loose_threshold_flags_nothing() -> None:
    """A very high explicit threshold flags nothing."""
    summary = _summary(50.0, _DURATIONS)
    assert summary["outlier_count"] == 0


def test_none_uses_class_default_not_singleton() -> None:
    """``None`` uses the class default, ignoring a poisoned singleton."""
    poisoned = MagicMock()
    # A singleton threshold of 0.01 would flag everything; the class default
    # (2.0) must win instead.
    poisoned.quality.llm_outlier_std_dev_threshold = 0.01

    with patch("chaoscypher_core.app_config.get_settings", return_value=poisoned):
        summary = _summary(None, _DURATIONS)

    # Recompute the expected count using the class default threshold.
    default = _summary(QualitySettings().llm_outlier_std_dev_threshold, _DURATIONS)
    assert summary["outlier_count"] == default["outlier_count"]
    assert QualitySettings().llm_outlier_std_dev_threshold == 2.0


def test_explicit_threshold_overrides_singleton() -> None:
    """An explicit threshold wins even when a singleton is present."""
    poisoned = MagicMock()
    poisoned.quality.llm_outlier_std_dev_threshold = 0.01
    with patch("chaoscypher_core.app_config.get_settings", return_value=poisoned):
        summary = _summary(50.0, _DURATIONS)
    assert summary["outlier_count"] == 0
