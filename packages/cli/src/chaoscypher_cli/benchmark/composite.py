# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Composite "Overall" scoring for the LLM (extractor) leaderboard.

Pure functions only — no I/O, no LLM calls. The three quality dimensions
(extraction, retrieval, chat) already arrive on a 0-100 scale from their
scorers; speed and cost are normalized to 0-100 against fixed anchors so a
model's Overall does not move when the field changes (absolute, not relative).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.results import BenchmarkResult


COMPOSITE_VERSION = 1

# Fixed normalization anchors. Calibrated in PR4 against a canonical run.
FAST_MS = 500  # ms/chunk -> speed 100
SLOW_MS = 30_000  # ms/chunk -> speed 0
MAX_USD = 1.0  # run cost -> cost 0 (free -> 100)


@dataclass(frozen=True)
class CompositeWeights:
    """Per-dimension weights for the Overall score (need not sum to 1)."""

    extraction: float = 0.40
    retrieval: float = 0.20
    chat: float = 0.20
    speed: float = 0.10
    cost: float = 0.10


@dataclass(frozen=True)
class DimensionScores:
    """0-100 per-dimension scores; None means the dimension was not run."""

    extraction: float | None
    retrieval: float | None
    chat: float | None
    speed: float | None
    cost: float | None


@dataclass(frozen=True)
class ExtractorComposite:
    """One row in the unified LLM leaderboard."""

    model_id: str
    model_label: str
    overall: float
    dims: DimensionScores
    basis: list[str]


def _clamp(x: float, lo: float, hi: float) -> float:
    """Clamp ``x`` to the inclusive ``[lo, hi]`` range."""
    return max(lo, min(hi, x))


def normalize_speed(latency_ms_per_chunk: int) -> float | None:
    """Map per-chunk latency to 0-100 (lower latency = higher score)."""
    if latency_ms_per_chunk <= 0:
        return None
    frac = (SLOW_MS - latency_ms_per_chunk) / (SLOW_MS - FAST_MS)
    return _clamp(frac, 0.0, 1.0) * 100.0


def normalize_cost(cost_usd: float) -> float:
    """Map run cost to 0-100 (cheaper = higher score; free = 100)."""
    frac = (MAX_USD - cost_usd) / MAX_USD
    return _clamp(frac, 0.0, 1.0) * 100.0


def weighted_overall(dims: DimensionScores, weights: CompositeWeights) -> tuple[float, list[str]]:
    """Weighted sum over present dimensions, weights renormalized to those present.

    Returns ``(overall, basis)`` where ``basis`` lists the contributing
    dimensions in canonical order. Returns ``(0.0, [])`` if none are present.
    """
    order = [
        ("extraction", dims.extraction, weights.extraction),
        ("retrieval", dims.retrieval, weights.retrieval),
        ("chat", dims.chat, weights.chat),
        ("speed", dims.speed, weights.speed),
        ("cost", dims.cost, weights.cost),
    ]
    present = [(name, score, w) for name, score, w in order if score is not None and w > 0]
    total_w = sum(w for _, _, w in present)
    if total_w <= 0:
        return 0.0, []
    overall = sum(score * w for _, score, w in present) / total_w
    return overall, [name for name, _, _ in present]


def compute_extractor_composites(
    rows: list[BenchmarkResult],
    *,
    default_embedder: str | None,
    default_chat: str | None,
    weights: CompositeWeights | None = None,
) -> list[ExtractorComposite]:
    """Build one composite per extractor, sorted by Overall descending.

    - Extraction/speed/cost come from the extraction rows (model_id == extractor).
    - Retrieval is the embedding row for (this extractor, default_embedder).
    - Chat is the chat row for (this extractor, default_embedder, default_chat).
    Dimensions with no matching successful row are None and drop out of Overall.

    Partial-config joins: when ``default_embedder`` is None, the retrieval/chat
    joins skip the embedder filter (averaging across all embedders for that
    extractor); when ``default_chat`` is None, the chat join skips the
    chat-model filter.
    """
    weights = weights or CompositeWeights()

    ext_rows = [r for r in rows if r.dataset_kind == "extraction" and r.success]
    emb_rows = [r for r in rows if r.dataset_kind == "embedding" and r.success]
    chat_rows = [r for r in rows if r.dataset_kind == "chat" and r.success]

    extractor_ids: list[str] = []
    seen: set[str] = set()
    for r in ext_rows:
        if r.model_id not in seen:
            seen.add(r.model_id)
            extractor_ids.append(r.model_id)

    out: list[ExtractorComposite] = []
    for ext_id in extractor_ids:
        my_ext = [r for r in ext_rows if r.model_id == ext_id]
        label = my_ext[0].model_label

        extraction = statistics.mean(r.headline_score for r in my_ext)
        speed_vals = [
            v
            for v in (normalize_speed(r.latency_ms_per_chunk_p50) for r in my_ext)
            if v is not None
        ]
        speed = statistics.mean(speed_vals) if speed_vals else None
        # Cost is the TOTAL run cost across datasets (sum, not mean), normalized
        # against the MAX_USD run-cost anchor — intentionally asymmetric with the
        # mean-based quality/speed dims (MAX_USD is calibrated against a full run in PR4).
        cost = normalize_cost(sum(r.cost_usd for r in my_ext))

        retrieval = _mean_or_none(
            r.headline_score
            for r in emb_rows
            if r.metrics.get("extractor_id") == ext_id
            and (default_embedder is None or r.model_id == default_embedder)
        )
        chat = _mean_or_none(
            r.headline_score
            for r in chat_rows
            if r.metrics.get("extractor_id") == ext_id
            and (default_embedder is None or r.metrics.get("embedder_id") == default_embedder)
            and (default_chat is None or r.model_id == default_chat)
        )

        dims = DimensionScores(
            extraction=extraction,
            retrieval=retrieval,
            chat=chat,
            speed=speed,
            cost=cost,
        )
        overall, basis = weighted_overall(dims, weights)
        out.append(
            ExtractorComposite(
                model_id=ext_id,
                model_label=label,
                overall=overall,
                dims=dims,
                basis=basis,
            )
        )

    out.sort(key=lambda c: -c.overall)
    return out


def _mean_or_none(values: Iterable[float]) -> float | None:
    """Return the arithmetic mean of ``values``, or ``None`` when empty."""
    vals = list(values)
    return statistics.mean(vals) if vals else None


__all__ = [
    "COMPOSITE_VERSION",
    "CompositeWeights",
    "DimensionScores",
    "ExtractorComposite",
    "compute_extractor_composites",
    "normalize_cost",
    "normalize_speed",
    "weighted_overall",
]
