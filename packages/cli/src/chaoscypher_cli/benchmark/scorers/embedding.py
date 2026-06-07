# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""EmbeddingRetrievalScorer — MRR + Recall@1 + Recall@3 over (query, gold) pairs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chaoscypher_cli.benchmark.results import ScoreResult


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import RawOutput
    from chaoscypher_cli.benchmark.queries import LabeledQuerySet


@dataclass
class EmbeddingRetrievalScorer:
    """Computes MRR / R@1 / R@3 from per-query rank dicts in extras."""

    version: int = 1

    def score(self, output: RawOutput, fixture: LabeledQuerySet) -> ScoreResult:
        """Score one RawOutput against the query fixture.

        Args:
            output: Raw embedding run output; ``extras["per_query"]`` must be a
                list of dicts with keys ``query_id``, ``band``, ``ranks``, and
                ``skipped``.
            fixture: The ``LabeledQuerySet`` used to produce ``output``; used
                only to compute ``queries_total`` (non-out_of_scope count).

        Returns:
            A :class:`~chaoscypher_cli.benchmark.results.ScoreResult` whose
            ``headline_score`` is MRR * 100 and whose ``metrics`` dict carries
            the full per-band breakdown.
        """
        per_query: list[dict[str, Any]] = output.extras.get("per_query", [])
        rr_sum = 0.0
        r1_hits = 0
        r3_hits = 0
        pairs = 0
        scored = 0
        unresolved = 0
        total = sum(1 for q in fixture.queries if q.band != "out_of_scope")
        per_band_pairs: dict[str, list[float]] = defaultdict(list)
        per_band_r1: dict[str, list[int]] = defaultdict(list)
        per_band_r3: dict[str, list[int]] = defaultdict(list)

        for entry in per_query:
            if entry.get("skipped"):
                unresolved += 1
                continue
            scored += 1
            band = entry.get("band", "unknown")
            for rank in entry.get("ranks", {}).values():
                rr = 1.0 / rank if rank and rank > 0 else 0.0
                rr_sum += rr
                hit_at_1 = 1 if rank == 1 else 0
                hit_at_3 = 1 if rank <= 3 else 0
                r1_hits += hit_at_1
                r3_hits += hit_at_3
                pairs += 1
                per_band_pairs[band].append(rr)
                per_band_r1[band].append(hit_at_1)
                per_band_r3[band].append(hit_at_3)

        mrr = rr_sum / pairs if pairs else 0.0
        recall_at_1 = r1_hits / pairs if pairs else 0.0
        recall_at_3 = r3_hits / pairs if pairs else 0.0

        by_band: dict[str, dict[str, float]] = {}
        for band, rrs in per_band_pairs.items():
            n = len(rrs) or 1
            by_band[band] = {
                "mrr": sum(rrs) / n,
                "recall_at_1": sum(per_band_r1[band]) / n,
                "recall_at_3": sum(per_band_r3[band]) / n,
                "pairs": len(rrs),
            }

        return ScoreResult(
            headline_score=mrr * 100.0,
            metrics={
                "mrr": mrr,
                "recall_at_1": recall_at_1,
                "recall_at_3": recall_at_3,
                "pairs_scored": pairs,
                "queries_total": total,
                "queries_scored": scored,
                "queries_unresolved": unresolved,
                "top_k": output.extras.get("top_k"),
                "by_band": by_band,
            },
        )


__all__ = ["EmbeddingRetrievalScorer"]
