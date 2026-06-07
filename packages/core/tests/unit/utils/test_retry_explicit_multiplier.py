# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Explicit-multiplier / class-default behavior for ``utils/retry._backoff_delay``.

Task C6 drops the direct ``app_config.get_settings()`` read from the generic
db-lock backoff helper. ``_backoff_delay`` gains an explicit
``exponential_multiplier`` parameter; ``None`` resolves to
``BackoffSettings().exponential_multiplier`` (the class default), not the app
singleton.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from chaoscypher_core.settings import BackoffSettings
from chaoscypher_core.utils.retry import _backoff_delay


def test_explicit_multiplier_honored() -> None:
    """A custom multiplier drives the exponential growth."""
    # base 2.0, multiplier 3.0, attempt 2 → 2.0 * 3**2 = 18.0, under the cap.
    assert _backoff_delay(2, 2.0, 100.0, exponential_multiplier=3.0) == 18.0


def test_explicit_multiplier_respects_cap() -> None:
    """Growth is still clamped by ``max_delay``."""
    assert _backoff_delay(10, 1.0, 5.0, exponential_multiplier=4.0) == 5.0


def test_none_uses_class_default_not_singleton() -> None:
    """``None`` reads ``BackoffSettings()`` class default, ignoring the singleton."""
    poisoned = MagicMock()
    poisoned.backoff.exponential_multiplier = 9.0

    with patch("chaoscypher_core.app_config.get_settings", return_value=poisoned):
        delay = _backoff_delay(1, 1.0, 1000.0)

    expected = 1.0 * (BackoffSettings().exponential_multiplier ** 1)
    assert delay == expected
    # Sanity: the class default is not the poisoned singleton value.
    assert BackoffSettings().exponential_multiplier != 9.0


def test_class_default_is_two() -> None:
    """Pin the BackoffSettings default so a schema change trips a test."""
    assert BackoffSettings().exponential_multiplier == 2.0
    # attempt 3, base 0.5, default multiplier 2.0 → 0.5 * 2**3 = 4.0
    assert _backoff_delay(3, 0.5, 100.0) == 4.0
