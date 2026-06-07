# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for BenchmarkDataset and DatasetScorer protocols."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_cli.benchmark.dataset import (
    BenchmarkDataset,
    DatasetScorer,
    RawOutput,
)
from chaoscypher_cli.benchmark.results import ScoreResult


class _FakeScorer:
    version: int = 7

    def score(self, output: RawOutput, fixture: Any) -> ScoreResult:
        return ScoreResult(headline_score=50.0, metrics={"fake": True})


class _FakeDataset:
    id: str = "fake_dataset"
    kind: str = "extraction"
    version: str = "1.0"
    fixture: Any = None
    source: str = "builtin"

    def __init__(self) -> None:
        self.scorer: DatasetScorer = _FakeScorer()

    async def run(self, model: Any) -> RawOutput:
        return RawOutput(
            entities=[],
            relationships=[],
            latency_ms=100,
            input_tokens=10,
            output_tokens=20,
            error=None,
        )


def test_fake_dataset_satisfies_protocol():
    d = _FakeDataset()
    assert isinstance(d, BenchmarkDataset)
    assert isinstance(d.scorer, DatasetScorer)


def test_raw_output_construction():
    out = RawOutput(
        entities=[{"name": "Pierre"}],
        relationships=[{"type": "knows", "source": 0, "target": 1}],
        latency_ms=1234,
        input_tokens=100,
        output_tokens=50,
        error=None,
    )
    assert out.latency_ms == 1234
    assert out.error is None
    assert out.per_chunk_latency_ms == []


def test_raw_output_failure_carries_error():
    out = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error="timeout",
    )
    assert out.error == "timeout"


@pytest.mark.asyncio
async def test_fake_dataset_run_returns_raw_output():
    d = _FakeDataset()
    out = await d.run(model=None)
    assert isinstance(out, RawOutput)
    assert out.latency_ms == 100


def test_raw_output_extras_default_empty_dict():
    r = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
    )
    assert r.extras == {}


def test_raw_output_extras_carries_payload():
    payload = {"per_query": [{"query_id": "q001", "ranks": {"X": 1}}]}
    r = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras=payload,
    )
    assert r.extras["per_query"][0]["query_id"] == "q001"
