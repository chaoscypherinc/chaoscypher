# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for PrioritySettings bounds validation.

Queue priority values must be constrained to [0, 100] to prevent silent
misconfiguration that could drift into unexpected orderings.
"""

import pytest
from pydantic import ValidationError

from chaoscypher_core.app_config import PrioritySettings


def test_in_range_priorities_accepted() -> None:
    """Values 0-100 inclusive should be accepted."""
    config = PrioritySettings(interactive=10, background=50, default=0)
    assert config.interactive == 10
    assert config.background == 50
    assert config.default == 0


def test_negative_interactive_rejected() -> None:
    """Negative values should raise ValidationError."""
    with pytest.raises(ValidationError):
        PrioritySettings(interactive=-1)


def test_over_max_background_rejected() -> None:
    """Values over 100 should raise ValidationError."""
    with pytest.raises(ValidationError):
        PrioritySettings(background=101)


def test_boundary_values_accepted() -> None:
    """0 and 100 are inclusive bounds."""
    PrioritySettings(interactive=0, background=100, default=100)
