# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reciprocal Rank Fusion utility for merging ranked result lists.

RRF is a rank-based fusion method that combines results from multiple
retrieval systems without requiring score normalization. It uses only
the rank position of each result, making it robust across different
scoring scales.

Reference: Cormack, Clarke & Butt (2009) - "Reciprocal Rank Fusion
outperforms Condorcet and individual Rank Learning Methods"
"""

from collections import defaultdict


def reciprocal_rank_fusion(
    *result_lists: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    Args:
        *result_lists: Variable number of result lists. Each list contains
            (id, original_score) tuples ordered by rank (best first).
        k: Smoothing constant. Higher values reduce the impact of high
            rankings. Default 60 per the original paper.

    Returns:
        Merged list of (id, rrf_score) tuples, sorted by RRF score descending.
        RRF score = sum of 1/(k + rank) across all lists where the item appears.
    """
    rrf_scores: dict[str, float] = defaultdict(float)

    for result_list in result_lists:
        for rank, (result_id, _original_score) in enumerate(result_list, start=1):
            rrf_scores[result_id] += 1.0 / (k + rank)

    return sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
