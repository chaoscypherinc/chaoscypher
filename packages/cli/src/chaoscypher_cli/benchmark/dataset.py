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

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from chaoscypher_core.benchmark.types import RawOutput


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_core.benchmark.types import ScoreResult


# Where a dataset was discovered. Affects override semantics (user wins on
# id collision) and surfaces in the leaderboard so reviewers can tell what's
# reproducible from the package alone vs what requires a user setup.
DatasetSource = Literal["builtin", "user"]


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
