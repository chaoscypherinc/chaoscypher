# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction quality analysis utilities.

Standalone functions for analyzing the quality and density of entity
extraction results.  Used by the AIEntityExtractor to produce statistics
included in extraction output.

Functions:
- calculate_density_stats: Per-chunk entity density statistics
"""

from __future__ import annotations

from typing import Any


def calculate_density_stats(
    chunks: list[str],
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Calculate extraction density statistics across chunks.

    Counts the number of entities extracted per chunk and derives
    min, max, average, and variance ratio metrics.

    Args:
        chunks: List of text chunks.
        entities: All extracted entities (each with a ``chunk_index`` key).

    Returns:
        Dictionary with min, max, avg, variance_ratio, per_chunk counts,
        or None if *chunks* is empty.

    """
    if not chunks:
        return None

    chunk_counts: dict[int, int] = dict.fromkeys(range(len(chunks)), 0)
    for entity in entities:
        chunk_idx = entity.get("chunk_index", 0)
        if chunk_idx in chunk_counts:
            chunk_counts[chunk_idx] += 1

    counts = list(chunk_counts.values())
    if not counts:
        return None

    min_count = min(counts)
    max_count = max(counts)
    avg_count = round(sum(counts) / len(counts), 1)
    variance_ratio = round(max_count / min_count, 1) if min_count > 0 else float(max_count)

    return {
        "min": min_count,
        "max": max_count,
        "avg": avg_count,
        "variance_ratio": variance_ratio,
        "per_chunk": counts,
    }
