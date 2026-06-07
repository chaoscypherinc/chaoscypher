# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Dataset and Scorer protocols for the benchmark.

These are the small abstractions that allow v2 to add chat without touching
the runner, result schema, or leaderboard renderer.

Vocabulary:
    dataset - the test unit (corpus + metadata + how to evaluate it).
    corpus - the body of text inside a dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.benchmark.results import ScoreResult


# Where a dataset was discovered. Affects override semantics (user wins on
# id collision) and surfaces in the leaderboard so reviewers can tell what's
# reproducible from the package alone vs what requires a user setup.
DatasetSource = Literal["builtin", "user"]


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
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DatasetScorer(Protocol):
    """Scores a dataset's RawOutput, producing a normalized 0-100 headline."""

    version: int

    def score(self, output: RawOutput, fixture: Any) -> ScoreResult:
        """Score one RawOutput against the optional fixture."""
        ...


@runtime_checkable
class BenchmarkDataset(Protocol):
    """A benchmark dataset: corpus + scorer + run logic."""

    id: str
    kind: str
    version: str
    scorer: DatasetScorer
    fixture: Any
    source: DatasetSource

    async def run(self, model: ModelConfig) -> RawOutput:
        """Execute the dataset against ``model`` and return its RawOutput."""
        ...


__all__ = ["BenchmarkDataset", "DatasetScorer", "DatasetSource", "RawOutput"]
