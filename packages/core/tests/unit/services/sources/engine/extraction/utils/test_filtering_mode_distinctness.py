# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""All six filtering modes produce distinct results on the gold corpus."""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        ("unfiltered", "minimal"),
        ("minimal", "lenient"),
        ("lenient", "balanced"),
        ("balanced", "strict"),
        ("strict", "maximum"),
    ],
)
def test_adjacent_modes_differ_on_at_least_three_fields(lower: str, upper: str) -> None:
    """Slider must feel distinct: at least 3 effective flags change between adjacent levels."""
    cfg_lower = resolve_filtering_config(lower)
    cfg_upper = resolve_filtering_config(upper)

    differing = sum(
        1
        for field in cfg_lower.__dataclass_fields__
        if getattr(cfg_lower, field) != getattr(cfg_upper, field)
    )
    assert differing >= 3, (
        f"{lower} -> {upper} differ in only {differing} fields; "
        "user won't perceive the slider change"
    )


def test_loop_max_entity_count_differs_across_modes() -> None:
    """The dead field 'loop_max_entity_count' must be wired into each preset."""
    minimal = resolve_filtering_config("minimal").loop_max_entity_count
    maximum = resolve_filtering_config("maximum").loop_max_entity_count
    assert minimal != maximum, (
        "loop_max_entity_count must differentiate at least minimal vs maximum"
    )


def test_semantic_dedup_threshold_differs_across_modes() -> None:
    """The dead field 'semantic_dedup_threshold' must be wired into each preset."""
    minimal = resolve_filtering_config("minimal").semantic_dedup_threshold
    maximum = resolve_filtering_config("maximum").semantic_dedup_threshold
    assert minimal != maximum


def test_minimum_alias_length_differs_across_modes() -> None:
    """The dead field 'minimum_alias_length' must be wired into each preset."""
    minimal = resolve_filtering_config("minimal").minimum_alias_length
    maximum = resolve_filtering_config("maximum").minimum_alias_length
    assert minimal != maximum
