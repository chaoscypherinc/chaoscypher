# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAGChatScorer — judge-LLM faithfulness/correctness/refusal aggregation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chaoscypher_cli.benchmark.results import ScoreResult


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import RawOutput
    from chaoscypher_cli.benchmark.queries import LabeledQuerySet


CHAT_SCORER_VERSION = 1
_WEIGHTS = {"faithfulness": 0.4, "correctness": 0.4, "refusal": 0.2}


@dataclass
class GraphRAGChatScorer:
    """Aggregates judge verdicts into a single 0-100 headline."""

    version: int = CHAT_SCORER_VERSION

    def score(self, output: RawOutput, fixture: LabeledQuerySet) -> ScoreResult:
        """Score one RawOutput against the query fixture.

        Args:
            output: Raw run output whose ``extras["per_query"]`` contains
                per-query judge score dicts.
            fixture: The labeled query set used for ``queries_total``.

        Returns:
            A :class:`~chaoscypher_cli.benchmark.results.ScoreResult` with a
            weighted headline and per-dimension breakdowns.
        """
        per_query: list[dict[str, Any]] = output.extras.get("per_query", [])
        f_vals: list[float] = []
        c_vals: list[float] = []
        r_vals: list[float] = []
        per_band: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: {"f": [], "c": [], "r": []}
        )
        head_terms: list[float] = []

        for entry in per_query:
            band = entry.get("band", "unknown")
            judge = entry.get("judge_scores") or {}
            f = judge.get("faithfulness")
            c = judge.get("correctness")
            r = judge.get("refusal_correct")
            weight_total = 0.0
            weighted = 0.0
            if isinstance(f, (int, float)):
                f_vals.append(float(f))
                per_band[band]["f"].append(float(f))
                weighted += (float(f) / 5.0) * _WEIGHTS["faithfulness"]
                weight_total += _WEIGHTS["faithfulness"]
            if isinstance(c, (int, float)):
                c_vals.append(float(c))
                per_band[band]["c"].append(float(c))
                weighted += (float(c) / 5.0) * _WEIGHTS["correctness"]
                weight_total += _WEIGHTS["correctness"]
            if isinstance(r, bool):
                r_num = 1.0 if r else 0.0
                r_vals.append(r_num)
                per_band[band]["r"].append(r_num)
                weighted += r_num * _WEIGHTS["refusal"]
                weight_total += _WEIGHTS["refusal"]
            if weight_total > 0:
                head_terms.append(weighted / weight_total)

        headline = (sum(head_terms) / len(head_terms) * 100.0) if head_terms else 0.0

        def _avg(xs: list[float]) -> float | None:
            return sum(xs) / len(xs) if xs else None

        by_band_out: dict[str, dict[str, float | int | None]] = {}
        for band, vals in per_band.items():
            by_band_out[band] = {
                "faithfulness_avg": _avg(vals["f"]),
                "correctness_avg": _avg(vals["c"]),
                "refusal_correct_rate": _avg(vals["r"]),
                "queries": max(len(vals["f"]), len(vals["c"]), len(vals["r"])),
            }

        return ScoreResult(
            headline_score=headline,
            metrics={
                "faithfulness_avg": _avg(f_vals),
                "correctness_avg": _avg(c_vals),
                "refusal_correct_rate": _avg(r_vals),
                "queries_total": len(fixture.queries),
                "queries_scored": len(per_query),
                "judge_provider": output.extras.get("judge_provider"),
                "judge_model": output.extras.get("judge_model"),
                "by_band": by_band_out,
            },
        )


__all__ = ["CHAT_SCORER_VERSION", "GraphRAGChatScorer"]
