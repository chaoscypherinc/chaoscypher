# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for snapshot layout selection and position computation."""

from chaoscypher_core.services.graph.snapshot.layout import (
    compute_source_positions,
    select_layout,
)


def test_select_layout_single_source_is_single_body():
    assert select_layout(1) == "single_body"


def test_select_layout_two_or_more_is_galaxy():
    assert select_layout(2) == "galaxy"
    assert select_layout(5) == "galaxy"
    assert select_layout(20) == "galaxy"


def test_select_layout_zero_sources_is_galaxy():
    # 0 is not == 1, so should not be single_body
    assert select_layout(0) == "galaxy"


def test_compute_positions_count_matches_input():
    for n in range(1, 21):
        positions = compute_source_positions("galaxy", n)
        assert len(positions) == n, f"Expected {n} positions for n={n}, got {len(positions)}"

    # The three hero slots must be exactly these coordinates for n=3
    hero_positions = compute_source_positions("galaxy", 3)
    assert hero_positions[0] == (0.0, 0.0)
    assert hero_positions[1] == (-210.0, 165.0)
    assert hero_positions[2] == (220.0, -150.0)


def test_compute_single_body_all_at_origin():
    positions = compute_source_positions("single_body", 5)
    assert len(positions) == 5
    for pos in positions:
        assert pos == (0.0, 0.0)
