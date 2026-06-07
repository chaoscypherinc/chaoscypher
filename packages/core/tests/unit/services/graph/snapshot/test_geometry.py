# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for snapshot geometry utilities."""

import random

from chaoscypher_core.services.graph.snapshot.geometry import (
    disc_positions,
    disc_radius,
    dot_variance,
    pack_circles,
)


def test_disc_radius_scales_with_count():
    random.seed(42)
    # More entities → larger radius
    assert disc_radius(10, 3.2) > disc_radius(1, 3.2)
    # count=0 should use max(count, 1) — same result as count=1
    assert disc_radius(0, 3.2) == disc_radius(1, 3.2)


def test_disc_positions_yields_count_points():
    random.seed(42)
    pts = list(disc_positions(15, 3.2))
    assert len(pts) == 15
    for pt in pts:
        assert len(pt) == 3
        x, y, depth = pt
        assert isinstance(x, float)
        assert isinstance(y, float)
        assert isinstance(depth, float)
        assert 0.0 <= depth <= 1.0


def test_disc_positions_depth_higher_near_center():
    # Statistical test: points closer to the cluster centre have higher mean
    # depth than points near the edge. Random jitter means this is not
    # guaranteed per-point, only true in aggregate across many points.
    random.seed(0)
    n = 300
    pts = list(disc_positions(n, 3.2))
    # Sort by squared radial distance from origin
    pts_with_r2 = [(x * x + y * y, depth) for x, y, depth in pts]
    pts_with_r2.sort(key=lambda t: t[0])
    # Split into inner quartile and outer quartile
    quartile = n // 4
    inner = pts_with_r2[:quartile]
    outer = pts_with_r2[-quartile:]
    mean_inner_depth = sum(d for _, d in inner) / quartile
    mean_outer_depth = sum(d for _, d in outer) / quartile
    assert mean_inner_depth > mean_outer_depth


def test_pack_circles_no_major_overlap():
    random.seed(42)
    # Edge cases
    assert pack_circles([]) == []
    assert pack_circles([5.0]) == [(0.0, 0.0)]

    radii = [10.0, 8.0, 6.0, 5.0, 4.0, 3.0]
    positions = pack_circles(radii)
    assert len(positions) == len(radii)

    epsilon = 0.02
    for i in range(len(radii)):
        for j in range(i + 1, len(radii)):
            xi, yi = positions[i]
            xj, yj = positions[j]
            dist = ((xj - xi) ** 2 + (yj - yi) ** 2) ** 0.5
            min_allowed = 0.92 * (radii[i] + radii[j]) * (1 - epsilon)
            assert dist >= min_allowed, (
                f"circles {i} and {j} overlap too much: dist={dist:.3f}, "
                f"min_allowed={min_allowed:.3f}"
            )


def test_dot_variance_returns_tiered_sizes():
    random.seed(123)
    base = 3.2 / 3.2  # spacing=3.2, base = spacing / 3.2 = 1.0
    bright_r = round(2.1 * base, 10)
    standard_r = round(1.5 * base, 10)
    dust_r = round(1.0 * base, 10)

    results = [dot_variance(3.2) for _ in range(1000)]
    radii_returned = [round(r, 10) for r, _ in results]

    distinct = set(radii_returned)
    assert distinct == {bright_r, standard_r, dust_r}, (
        f"Expected exactly 3 distinct radii, got: {distinct}"
    )

    counts = {v: radii_returned.count(v) for v in distinct}
    total = len(radii_returned)

    # 10% bright stars ± 5%
    assert abs(counts[bright_r] / total - 0.10) < 0.05, (
        f"Bright star frequency off: {counts[bright_r] / total:.3f}"
    )
    # 60% standard ± 5%
    assert abs(counts[standard_r] / total - 0.60) < 0.05, (
        f"Standard frequency off: {counts[standard_r] / total:.3f}"
    )
    # 30% dust ± 5%
    assert abs(counts[dust_r] / total - 0.30) < 0.05, (
        f"Dust frequency off: {counts[dust_r] / total:.3f}"
    )
