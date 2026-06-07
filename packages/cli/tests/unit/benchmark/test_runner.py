# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the benchmark runner."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_cli.benchmark.dataset import RawOutput
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.results import ScoreResult
from chaoscypher_cli.benchmark.runner import run_benchmark


class _FakeScorer:
    version: int = 7

    def score(self, output: RawOutput, fixture: Any) -> ScoreResult:
        return ScoreResult(
            headline_score=70.0 if output.error is None else 0.0,
            metrics={"fake": True},
        )


class _SuccessDataset:
    id = "success_dataset"
    kind = "extraction"
    version = "1.0"
    fixture = None
    source = "builtin"

    def __init__(self) -> None:
        self.scorer = _FakeScorer()

    async def run(self, model: ModelConfig) -> RawOutput:
        return RawOutput(
            entities=[{"name": "A"}],
            relationships=[],
            latency_ms=500,
            input_tokens=100,
            output_tokens=50,
            error=None,
            per_chunk_latency_ms=[500],
        )


class _FailDataset:
    id = "fail_dataset"
    kind = "extraction"
    version = "1.0"
    fixture = None
    source = "builtin"

    def __init__(self) -> None:
        self.scorer = _FakeScorer()

    async def run(self, model: ModelConfig) -> RawOutput:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_runner_emits_one_row_per_model_dataset_pair():
    models = [
        ModelConfig(provider="ollama", model="m1", label="M1"),
        ModelConfig(provider="ollama", model="m2", label="M2"),
    ]
    datasets = [_SuccessDataset(), _SuccessDataset()]
    rows = await run_benchmark(models=models, datasets=datasets)
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_runner_captures_dataset_run_exceptions_as_failed_rows():
    models = [ModelConfig(provider="ollama", model="m", label="M")]
    datasets = [_FailDataset()]
    rows = await run_benchmark(models=models, datasets=datasets)
    assert len(rows) == 1
    assert rows[0].success is False
    assert "RuntimeError" in (rows[0].error or "")
    assert rows[0].headline_score == 0.0


@pytest.mark.asyncio
async def test_runner_skips_models_whose_kinds_exclude_dataset_kind():
    models = [
        ModelConfig(provider="ollama", model="m", label="M", kinds=["chat"]),
    ]
    datasets = [_SuccessDataset()]
    rows = await run_benchmark(models=models, datasets=datasets)
    assert rows == []


@pytest.mark.asyncio
async def test_runner_pins_versions_and_seed_on_every_row():
    models = [ModelConfig(provider="ollama", model="m", label="M")]
    datasets = [_SuccessDataset()]
    rows = await run_benchmark(
        models=models,
        datasets=datasets,
        config_name="test_config",
        seed=99,
        temperature=0.0,
    )
    assert rows[0].seed == 99
    assert rows[0].temperature == 0.0
    assert rows[0].benchmark_version == "1.0"
    assert rows[0].dataset_version == "1.0"
    assert rows[0].dataset_source == "builtin"
    assert rows[0].config_name == "test_config"
    assert rows[0].scorer_version == 7
