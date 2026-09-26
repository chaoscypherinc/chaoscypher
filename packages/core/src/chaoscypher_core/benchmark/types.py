# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared benchmark value types: a dataset's raw output and a scorer's result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawOutput:
    """Raw output of a dataset's run against one model.

    Attributes:
        entities: Extracted entity dicts (extraction datasets only).
        relationships: Extracted relationship dicts (extraction datasets only).
        latency_ms: Total wall-clock for the run.
        input_tokens: Cumulative LLM input tokens.
        output_tokens: Cumulative LLM output tokens.
        error: Failure reason or None.
        per_chunk_latency_ms: Per-chunk latencies for percentile reporting.
            Empty when the dataset does not chunk.
        chunks_truncated: Chunks whose LLM call ended with ``finish_reason
            == "length"`` - the model ran out of generation budget mid-answer.
            A truncated extraction can outscore a complete one, so a score
            reported without this number is not interpretable.
        chunks_aborted_by_loop: Chunks the stream loop detector cut short on a
            degenerate pattern. Same reasoning as above.
        extras: Kind-specific payload. Embedding datasets pack per-query
            rank dicts; chat datasets pack per-query answers + judge scores.
            The runner ignores this field; the paired scorer reads it.
    """

    entities: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    latency_ms: int
    input_tokens: int
    output_tokens: int
    error: str | None
    per_chunk_latency_ms: list[int] = field(default_factory=list)
    chunks_truncated: int = 0
    chunks_aborted_by_loop: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreResult:
    """Output of a ``PackScorer.score()`` call.

    Attributes:
        headline_score: 0-100, comparable across pack kinds.
        metrics: Free-form per-kind detail (v7 breakdown for extraction;
            faithfulness etc. for chat in v2).
    """

    headline_score: float
    metrics: dict[str, Any] = field(default_factory=dict)


__all__ = ["RawOutput", "ScoreResult"]
