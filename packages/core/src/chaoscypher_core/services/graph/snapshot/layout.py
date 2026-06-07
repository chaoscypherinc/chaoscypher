# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Layout position tables for graph snapshot rendering.

Ported from the original snapshot mockup script — pure data, no I/O.
"""

from typing import Literal


Layout = Literal["galaxy", "single_body"]


# Galaxy layout: 3 heroes clearly separated + up to 17 smaller sources orbiting them.
# Copied from the original mockup script's galaxy_layout() — hand-tuned hero slots
# ensure visual weight balances across 1-20 sources.
_GALAXY_HERO_POSITIONS: tuple[tuple[float, float], ...] = (
    (0, 0),
    (-210, 165),
    (220, -150),
)

_GALAXY_OUTER_POSITIONS: tuple[tuple[float, float], ...] = (
    (-10, -270),
    (160, -240),
    (300, -55),
    (330, 110),
    (250, 230),
    (80, 320),
    (-90, 320),
    (-270, 280),
    (-350, 85),
    (-360, -75),
    (-280, -230),
    (-80, -215),
    (90, -185),
    (-140, 25),
    (360, 225),
    (-50, 230),
    (185, -20),
)

_GALAXY_POSITIONS = _GALAXY_HERO_POSITIONS + _GALAXY_OUTER_POSITIONS  # 20 total


def select_layout(n_sources: int) -> Layout:
    """Single body when there's exactly one source, otherwise galaxy."""
    return "single_body" if n_sources == 1 else "galaxy"


def compute_source_positions(layout: Layout, n_sources: int) -> list[tuple[float, float]]:
    """Return ``n_sources`` ``(x, y)`` positions for the chosen layout.

    Galaxy: hero slots first, then outer ring; up to 20 sources total
    (truncates silently beyond that — callers should already limit to
    the top 20 by entity count).
    Single body: all at origin (stacked; renderer applies scale).
    """
    if n_sources <= 0:
        return []
    if layout == "single_body":
        return [(0.0, 0.0)] * n_sources
    return [(float(x), float(y)) for x, y in _GALAXY_POSITIONS[:n_sources]]
