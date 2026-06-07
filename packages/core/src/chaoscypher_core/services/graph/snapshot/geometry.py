# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Render geometry utilities for graph snapshots.

Ported from the original snapshot mockup script — pure functions, no I/O.
"""

import math
import random
from collections.abc import Iterator


def disc_positions(
    n: int,
    spacing: float,
    rotation: float = 0.0,
) -> Iterator[tuple[float, float, float]]:
    """Phyllotactic packing projected onto a hemisphere.

    Yields (x, y, depth) where depth is in [0, 1]: 0 ≈ silhouette edge,
    1 ≈ facing the viewer. Lets the renderer modulate dot size/opacity
    so clusters read as 3D spheres, not flat discs.
    """
    golden = math.pi * (3 - math.sqrt(5))
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    max_r = spacing * math.sqrt(max(n, 1) + 0.5)
    for i in range(n):
        r = spacing * math.sqrt(i + 0.5)
        theta = i * golden
        jitter_amp = spacing * 0.4
        px = r * math.cos(theta) + random.uniform(-jitter_amp, jitter_amp)  # noqa: S311
        py = r * math.sin(theta) + random.uniform(-jitter_amp, jitter_amp)  # noqa: S311
        fx = px * cos_r - py * sin_r
        fy = px * sin_r + py * cos_r
        # Simulated depth — hemisphere projection. Dots near cluster
        # center are "in front," edge dots are near the silhouette.
        dist = math.sqrt(fx * fx + fy * fy)
        norm = min(dist / max_r, 1.0)
        depth_base = math.sqrt(max(0.0, 1.0 - norm * norm))
        # Shuffle a bit so edge isn't a hard boundary
        depth = max(
            0.0,
            min(
                1.0,
                depth_base * random.uniform(0.7, 1.0)  # noqa: S311
                + random.uniform(-0.12, 0.12),  # noqa: S311
            ),
        )
        yield (fx, fy, depth)


def disc_radius(count: int, spacing: float) -> float:
    """Cluster radius for a template with ``count`` entities at a given ``spacing``."""
    return spacing * math.sqrt(max(count, 1) + 0.5)


def dot_variance(spacing: float) -> tuple[float, float]:
    """Procedural star/dust distribution. Returns ``(radius, opacity)`` from a 10/60/30 mixture.

    - 10% bright stars: radius = 2.1 * base, opacity = 1.0
    - 60% standard: radius = 1.5 * base, opacity = 0.92
    - 30% dust: radius = 1.0 * base, opacity = 0.55

    ``base = spacing / 3.2``.
    """
    roll = random.random()  # noqa: S311
    base = spacing / 3.2  # scale factor relative to spacing=3.2
    if roll < 0.10:
        # Bright stars — slightly larger, nearly opaque
        return (2.1 * base, 1.0)
    if roll < 0.70:
        # Standard
        return (1.5 * base, 0.92)
    # Dust — small, translucent
    return (1.0 * base, 0.55)


def pack_circles(radii: list[float], iterations: int = 140) -> list[tuple[float, float]]:
    """Force-directed circle packing near origin with ~8% overlap tolerance.

    Returns ``(x, y)`` tuples in the same order as ``radii``. Empty input
    returns []. Single input returns [(0.0, 0.0)].
    """
    n = len(radii)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]

    # Bounding radius for the whole source (so circles don't fly off)
    source_r = math.sqrt(sum(r * r for r in radii)) * 1.25

    # Initial: biggest at center, smaller ones ringed by a Fibonacci spiral
    positions = [[0.0, 0.0]]
    for i, r in enumerate(radii[1:], 1):
        angle = i * 2.399  # golden angle seed
        d = radii[0] + r * 1.4
        positions.append([d * math.cos(angle), d * math.sin(angle)])

    for _ in range(iterations):
        # Pairwise collision resolve (allow ~8% overlap so glows merge)
        for i in range(n):
            for j in range(i + 1, n):
                dx = positions[j][0] - positions[i][0]
                dy = positions[j][1] - positions[i][1]
                d = math.sqrt(dx * dx + dy * dy)
                if d < 0.001:
                    # perfect overlap — nudge apart
                    positions[j][0] += 0.5
                    positions[j][1] += 0.5
                    continue
                min_d = (radii[i] + radii[j]) * 0.92
                if d < min_d:
                    push = (min_d - d) / 2
                    ux, uy = dx / d, dy / d
                    positions[i][0] -= ux * push
                    positions[i][1] -= uy * push
                    positions[j][0] += ux * push
                    positions[j][1] += uy * push

        # Soft pull toward center (so the whole arrangement stays compact)
        for i in range(n):
            positions[i][0] *= 0.995
            positions[i][1] *= 0.995

        # Clamp each circle inside source_r
        for i in range(n):
            d = math.sqrt(positions[i][0] ** 2 + positions[i][1] ** 2)
            max_d = source_r - radii[i]
            if d > max_d and d > 0.001:
                scale = max_d / d
                positions[i][0] *= scale
                positions[i][1] *= scale

    return [(p[0], p[1]) for p in positions]
