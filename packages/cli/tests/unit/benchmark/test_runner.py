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
    seed = 42
    temperature = 0.0
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
    seed = 42
    temperature = 0.0
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
    assert rows[0].benchmark_version == "2.0"
    assert rows[0].dataset_version == "1.0"
    assert rows[0].dataset_source == "builtin"
    assert rows[0].config_name == "test_config"
    assert rows[0].scorer_version == 7


class _HangingDataset:
    seed = 42
    temperature = 0.0
    id = "hanging_dataset"
    kind = "extraction"
    version = "1.0"
    fixture = None
    source = "builtin"

    def __init__(self) -> None:
        self.scorer = _FakeScorer()

    async def run(self, model: ModelConfig) -> RawOutput:
        import asyncio

        await asyncio.sleep(5)
        raise AssertionError("unreachable: the timeout must cancel this run")


@pytest.mark.asyncio
async def test_model_timeout_records_a_failed_row_and_moves_on() -> None:
    """A hung model becomes one failed row instead of stalling the sweep.

    2026-09-23: olmo-3.1:32b sat 75 minutes at 100% GPU with no output and
    the 16-model sweep behind it never ran.
    """
    seen: list = []
    rows = await run_benchmark(
        [ModelConfig(provider="ollama", model="m1", label="M1")],
        [_HangingDataset(), _SuccessDataset()],
        model_timeout=0.05,
        on_row=seen.append,
    )
    assert [r.success for r in rows] == [False, True]
    assert rows[0].error is not None and rows[0].error.startswith("TimeoutError")
    assert seen == rows  # every row handed to the caller as it landed


@pytest.mark.asyncio
async def test_every_row_says_whether_its_seed_reached_the_model():
    """Audit wave 3: only the Ollama adapter sends a seed; a cloud row's seed is a label."""
    models = [
        ModelConfig(provider="ollama", model="m", label="M"),
        ModelConfig(provider="anthropic", model="c", label="C"),
    ]
    rows = await run_benchmark(models=models, datasets=[_SuccessDataset(), _FailDataset()])
    by_label = {(r.model_label, r.success): r.seed_honoured for r in rows}
    assert by_label == {
        ("M", True): True,
        ("C", True): False,
        ("M", False): True,
        ("C", False): False,
    }


def test_the_seed_honouring_providers_are_the_adapters_that_send_one():
    """Read from the code: a built-in provider is in the set exactly when its adapter sends ``seed``."""
    import importlib
    import inspect

    from chaoscypher_core.adapters.llm.providers import ProviderRegistry
    from chaoscypher_core.utils.decoding import SEED_HONOURING_PROVIDERS

    sends = set()
    for name, target in ProviderRegistry._BUILTIN_PROVIDERS.items():
        module = importlib.import_module(target.partition(":")[0])
        if '"seed"' in inspect.getsource(module):
            sends.add(name)
    assert sends == set(SEED_HONOURING_PROVIDERS)


# ---------------------------------------------------------------------------
# The model build every row ran on (audit wave 4, 2026-10-06)
# ---------------------------------------------------------------------------


_BUILD = {
    "digest": "500a1f067a9f",
    "server_version": "0.12.3",
    "parameters": {"repeat_penalty": 1, "top_k": 20},
    "num_batch": 512,
    "num_thread": None,
    "num_parallel": 2,
}


@pytest.mark.asyncio
async def test_every_row_records_the_model_build_scored_and_failed_alike():
    """An ``ollama pull`` between two runs must show on the rows, so every row records it."""
    asked: list[str] = []

    async def fake_build(model: ModelConfig) -> dict[str, Any] | None:
        asked.append(model.model)
        return dict(_BUILD)

    models = [ModelConfig(provider="ollama", model="qwen3:8b", label="Q")]
    rows = await run_benchmark(
        models=models, datasets=[_SuccessDataset(), _FailDataset()], model_build=fake_build
    )
    assert [r.success for r in rows] == [True, False]
    assert all(r.extras["model_build"] == _BUILD for r in rows)
    # per row, before it runs and after (audit wave 6); the same build: nothing more recorded
    assert asked == ["qwen3:8b"] * 4
    assert all("model_build_changed" not in r.extras for r in rows)


@pytest.mark.asyncio
async def test_a_build_that_moves_while_a_row_runs_is_recorded_and_flagged():
    """Audit wave 6: an ``ollama pull`` mid-row went unseen; the build is asked again after."""
    from chaoscypher_core.benchmark.results import MODEL_BUILD_CHANGED, comparability_findings

    digests = iter(["sha256:before", "sha256:after", "sha256:after", "sha256:after"])

    async def pulled_mid_row(model: ModelConfig) -> dict[str, Any] | None:
        return {**_BUILD, "digest": next(digests)}

    models = [ModelConfig(provider="ollama", model="qwen3:8b", label="Q")]
    rows = await run_benchmark(
        models=models, datasets=[_SuccessDataset(), _SuccessDataset()], model_build=pulled_mid_row
    )
    assert rows[0].extras["model_build"]["digest"] == "sha256:before"
    assert rows[0].extras[MODEL_BUILD_CHANGED]["digest"] == "sha256:after"
    assert MODEL_BUILD_CHANGED not in rows[1].extras
    findings = comparability_findings({"base": [rows[0]], "variant": [rows[1]]})
    assert any("changed while a row ran (base)" in f for f in findings)


@pytest.mark.asyncio
async def test_a_judged_row_records_the_judges_build_too():
    """Audit wave 5: the judge's weights move every judged score; its build is on the row."""
    asked: list[str] = []

    async def fake_build(model: ModelConfig) -> dict[str, Any] | None:
        asked.append(model.model)
        return {**_BUILD, "digest": f"sha256:{model.model}"}

    judged = _SuccessDataset()
    judged.judge = ModelConfig(provider="ollama", model="judge:7b", label="J")  # type: ignore[attr-defined]
    models = [ModelConfig(provider="ollama", model="qwen3:8b", label="Q")]
    rows = await run_benchmark(
        models=models, datasets=[judged, _SuccessDataset()], model_build=fake_build
    )
    assert rows[0].extras["judge_model_build"]["digest"] == "sha256:judge:7b"
    assert "judge_model_build" not in rows[1].extras
    assert asked == ["qwen3:8b", "qwen3:8b", "judge:7b", "qwen3:8b", "qwen3:8b"]


class _HttpxTimeoutDataset(_FailDataset):
    async def run(self, model: ModelConfig) -> RawOutput:
        import httpx

        raise httpx.ReadTimeout("the server stopped answering")


@pytest.mark.asyncio
async def test_an_httpx_timeout_is_flagged_timed_out():
    """Audit wave 5: the flag asked isinstance(exc, TimeoutError), which httpx's timeouts are not."""
    rows = await run_benchmark(
        models=[ModelConfig(provider="ollama", model="m", label="M")],
        datasets=[_HttpxTimeoutDataset()],
    )
    assert rows[0].extras["timed_out"] is True


@pytest.mark.asyncio
async def test_the_default_fetch_asks_the_configured_ollama_with_the_sent_options(monkeypatch):
    """Ollama models ask the configured server with the per-card options; cloud models record None."""
    from types import SimpleNamespace

    from chaoscypher_cli.benchmark import runner
    from chaoscypher_core.settings import CacheSettings

    monkeypatch.undo()  # the suite's autouse fake; this test fakes one layer lower
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_ollama_model_build(base_url: str, model: str, *, options: Any) -> dict:
        calls.append((base_url, model, dict(options)))
        return {**_BUILD, **options}

    llm = SimpleNamespace(
        primary_ollama_url="http://gpu-5090:11434",
        ollama_num_batch=1024,
        ollama_num_thread=None,
        ollama_num_parallel=1,
    )
    monkeypatch.setattr(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ollama_model_build",
        fake_ollama_model_build,
    )
    monkeypatch.setattr(
        # the row records the run's caches too (informational)
        "chaoscypher_core.app_config.get_settings",
        lambda: SimpleNamespace(llm=llm, cache=CacheSettings()),
    )
    rows = await run_benchmark(
        models=[
            ModelConfig(provider="ollama", model="qwen3:8b", label="Q"),
            ModelConfig(provider="anthropic", model="claude", label="C"),
        ],
        datasets=[_SuccessDataset()],
    )
    # before the row and after it (audit wave 6); the per-call repeat penalty unset (wave 6)
    assert (
        calls
        == [
            (
                "http://gpu-5090:11434",
                "qwen3:8b",
                {"num_batch": 1024, "num_thread": None, "num_parallel": 1, "repeat_penalty": None},
            )
        ]
        * 2
    )
    assert rows[0].extras["model_build"]["num_batch"] == 1024
    # audit wave 5: requested is not sent; ChatOllama drops num_batch and num_parallel
    assert rows[0].extras["model_build"]["options_sent"] == {
        "num_batch": False,
        "num_thread": False,  # unset
        "num_parallel": False,
        "repeat_penalty": False,  # sent only under an override (the retry ladder's rung 2)
    }
    assert rows[1].extras["model_build"] is None
    assert runner.fetch_model_build.__name__ == "fetch_model_build"
