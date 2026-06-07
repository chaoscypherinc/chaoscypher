# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: FilteringMode must live at chaoscypher_core.ports.types.

Canonical location for cross-package boundary types (per API_DESIGN.md +
ports/types convention). CLI and Cortex import this Literal to validate
filtering-mode input at the boundary; importing from the engine internals
(``services.sources.engine.extraction.utils.filtering_config``) crosses a
layer it shouldn't.
"""

from __future__ import annotations

from typing import get_args

import pytest


@pytest.mark.unit
def test_filtering_mode_importable_from_ports_types() -> None:
    """FilteringMode must be re-exportable from chaoscypher_core.ports.types."""
    from chaoscypher_core.ports.types import FilteringMode

    modes = set(get_args(FilteringMode))
    assert modes == {
        "maximum",
        "strict",
        "balanced",
        "lenient",
        "minimal",
        "unfiltered",
    }


@pytest.mark.unit
def test_filtering_mode_matches_preset_overrides() -> None:
    """FilteringMode Literal must enumerate exactly the keys of _PRESET_OVERRIDES."""
    from chaoscypher_core.ports.types import FilteringMode
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        VALID_PRESETS,
    )

    assert set(get_args(FilteringMode)) == set(VALID_PRESETS)


@pytest.mark.unit
def test_filtering_mode_in_ports_types_all() -> None:
    """FilteringMode must be in chaoscypher_core.ports.types.__all__ if defined."""
    import chaoscypher_core.ports.types as ports_types

    if hasattr(ports_types, "__all__"):
        assert "FilteringMode" in ports_types.__all__
