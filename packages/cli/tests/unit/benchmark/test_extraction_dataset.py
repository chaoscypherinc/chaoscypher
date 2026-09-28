# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Structural tests for ExtractionDataset (no LLM)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.benchmark.dataset import BenchmarkDataset
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.scorers.v7 import V7ExtractionScorer


def test_extraction_dataset_satisfies_protocol(tmp_path: Path):
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("Pierre Bezukhov visited Moscow in 1812.")
    ds = ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
    )
    assert isinstance(ds, BenchmarkDataset)
    assert ds.kind == "extraction"
    assert isinstance(ds.scorer, V7ExtractionScorer)
    assert ds.fixture is None  # fixture used by chat in v2
    assert ds.source == "builtin"  # default


def test_extraction_dataset_loads_corpus(tmp_path: Path):
    corpus = tmp_path / "corpus.txt"
    text = "Pierre Bezukhov visited Moscow in 1812."
    corpus.write_text(text)
    ds = ExtractionDataset(id="t", version="1.0", corpus_path=corpus, domain="literary")
    assert ds.corpus_text == text


def test_extraction_dataset_missing_corpus_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ExtractionDataset(
            id="t",
            version="1.0",
            corpus_path=tmp_path / "missing.txt",
            domain="literary",
        )


def test_extraction_dataset_keep_db_defaults_to_false(tmp_path: Path):
    """Default behavior cleans up temp DBs."""
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("x")
    ds = ExtractionDataset(id="t", version="1.0", corpus_path=corpus, domain="literary")
    assert ds.keep_db is False


def test_extraction_dataset_keep_db_can_be_enabled(tmp_path: Path):
    """Opt-in path preserves temp DBs."""
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("x")
    ds = ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
        keep_db=True,
    )
    assert ds.keep_db is True


def test_extraction_dataset_source_set_user(tmp_path: Path):
    """Source is settable for user-overlay datasets."""
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("x")
    ds = ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
        source="user",
    )
    assert ds.source == "user"


@pytest.mark.asyncio
async def test_extraction_run_enables_normalization(tmp_path: Path):
    corpus = tmp_path / "c.txt"
    corpus.write_text("Some text about people and places.", encoding="utf-8")
    ds = ExtractionDataset(id="c", version="1.0", corpus_path=corpus, domain="literary")

    captured: dict = {}

    class _Result:
        success = True
        file_id = "f1"
        chunks_count = 1
        llm_total_input_tokens = 10
        llm_total_output_tokens = 20
        error = None

    def _fake_run(**kwargs):
        captured.update(kwargs)
        return _Result()

    fake_pipeline = MagicMock()
    fake_pipeline.run.side_effect = _fake_run
    fake_service = MagicMock()
    fake_service.__enter__.return_value = fake_service
    fake_service.__exit__.return_value = False

    with (
        patch("chaoscypher_cli.sources.SourcePipeline", return_value=fake_pipeline),
        patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=fake_service),
        patch.object(ExtractionDataset, "_build_temp_context") as build_ctx,
    ):
        ctx = MagicMock()
        ctx.storage_adapter.list_source_entities.return_value = [{"id": "e1"}]
        ctx.storage_adapter.list_source_relationships.return_value = []
        ctx.database_dir = tmp_path
        build_ctx.return_value = ctx
        await ds.run(ModelConfig(provider="ollama", model="m", label="M"))

    assert captured["enable_normalization"] is True


def _fake_pipeline_env(tmp_path: Path, result: object) -> tuple[MagicMock, MagicMock]:
    fake_pipeline = MagicMock()
    fake_pipeline.run.return_value = result
    fake_service = MagicMock()
    fake_service.__enter__.return_value = fake_service
    fake_service.__exit__.return_value = False
    return fake_pipeline, fake_service


@pytest.mark.asyncio
async def test_extraction_run_reports_truncation_on_pipeline_failure(tmp_path: Path):
    """A run the pipeline reports as failed still surfaces the truncation
    counters from its source row — a failure *caused* by truncation used to
    report chunks_truncated=0 (2026-09-24 llm audit).
    """
    corpus = tmp_path / "c.txt"
    corpus.write_text("Some text.", encoding="utf-8")
    ds = ExtractionDataset(id="c", version="1.0", corpus_path=corpus, domain="literary")

    class _Failed:
        success = False
        file_id = "f1"
        chunks_count = 1
        llm_total_input_tokens = 10
        llm_total_output_tokens = 20
        error = "extraction_failed"

    fake_pipeline, fake_service = _fake_pipeline_env(tmp_path, _Failed())
    with (
        patch("chaoscypher_cli.sources.SourcePipeline", return_value=fake_pipeline),
        patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=fake_service),
        patch.object(ExtractionDataset, "_build_temp_context") as build_ctx,
        patch.object(ExtractionDataset, "_integrity_counters", return_value=(3, 1)),
    ):
        ctx = MagicMock()
        ctx.database_dir = tmp_path
        build_ctx.return_value = ctx
        out = await ds.run(ModelConfig(provider="openai", model="m", label="M"))

    assert out.error == "extraction_failed"
    assert (out.chunks_truncated, out.chunks_aborted_by_loop) == (3, 1)


@pytest.mark.asyncio
async def test_thinking_probe_targets_configured_ollama(tmp_path: Path):
    """The probe goes to the Ollama the run extracts with, not to a hard-coded
    localhost (2026-09-24 llm audit).
    """
    corpus = tmp_path / "c.txt"
    corpus.write_text("Some text.", encoding="utf-8")
    ds = ExtractionDataset(id="c", version="1.0", corpus_path=corpus, domain="literary")

    class _Result:
        success = True
        file_id = "f1"
        chunks_count = 1
        llm_total_input_tokens = 1
        llm_total_output_tokens = 1
        error = None

    fake_pipeline, fake_service = _fake_pipeline_env(tmp_path, _Result())
    fake_settings = MagicMock()
    fake_settings.llm.primary_ollama_url = "http://ollama-box:11434"
    probe = AsyncMock(return_value={"thinks_when_asked": None, "obeys_think_false": None})
    with (
        patch("chaoscypher_cli.sources.SourcePipeline", return_value=fake_pipeline),
        patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=fake_service),
        patch("chaoscypher_cli.benchmark.thinking_probe.probe_thinking", probe),
        patch("chaoscypher_core.app_config.get_settings", return_value=fake_settings),
        patch.object(ExtractionDataset, "_build_temp_context") as build_ctx,
        patch.object(ExtractionDataset, "_integrity_counters", return_value=(0, 0)),
    ):
        ctx = MagicMock()
        ctx.storage_adapter.list_source_entities.return_value = [{"id": "e1"}]
        ctx.storage_adapter.list_source_relationships.return_value = []
        ctx.database_dir = tmp_path
        build_ctx.return_value = ctx
        await ds.run(ModelConfig(provider="ollama", model="m", label="M"))

    probe.assert_awaited_once_with("m", base_url="http://ollama-box:11434")
