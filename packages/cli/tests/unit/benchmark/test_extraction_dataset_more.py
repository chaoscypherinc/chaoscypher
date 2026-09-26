# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral tests for ExtractionDataset.run / temp-context / cleanup paths.

Mocks chaoscypher_cli.sources.{SourcePipeline,CLISourceProcessingService},
chaoscypher_cli.context.CLIContext and the SQLite engine eviction so the
extraction run logic is exercised without real LLM/DB I/O.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cli.benchmark.extraction_dataset import (
    ExtractionDataset,
    _remove_temp_db_dir,
)
from chaoscypher_cli.benchmark.models import ModelConfig


def _ds(tmp_path: Path, *, keep_db: bool = False) -> ExtractionDataset:
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("Pierre visited Moscow in 1812.", encoding="utf-8")
    return ExtractionDataset(
        id="t",
        version="1.0",
        corpus_path=corpus,
        domain="literary",
        keep_db=keep_db,
    )


def _model() -> ModelConfig:
    return ModelConfig(provider="ollama", model="llama3.1:8b", label="L")


def _fake_ctx(tmp_path: Path, *, entities: list[Any], relationships: list[Any]) -> MagicMock:
    """A MagicMock CLIContext whose adapter returns the given graph rows."""
    ctx = MagicMock()
    ctx.database_name = "benchmark_db"
    db_dir = tmp_path / "dbdir"
    db_dir.mkdir(exist_ok=True)
    (db_dir / "app.db").write_bytes(b"sqlite")
    ctx.database_dir = db_dir
    ctx.storage_adapter.list_source_entities.return_value = entities
    ctx.storage_adapter.list_source_relationships.return_value = relationships
    return ctx


def _patch_pipeline(result: Any):
    """Patch sources.SourcePipeline + CLISourceProcessingService to a fake.

    The service is a context manager; the pipeline's .run returns ``result``.
    """
    service_cm = MagicMock()
    service_cm.__enter__ = lambda s: s
    service_cm.__exit__ = MagicMock(return_value=False)
    service_factory = MagicMock(return_value=service_cm)

    pipeline = MagicMock()
    pipeline.run = MagicMock(return_value=result)
    pipeline_factory = MagicMock(return_value=pipeline)

    return service_factory, pipeline_factory, pipeline


@pytest.mark.asyncio
async def test_run_success_returns_entities_and_relationships(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    ctx = _fake_ctx(
        tmp_path,
        entities=[{"id": "e1", "name": "Pierre"}],
        relationships=[{"id": "r1"}],
    )
    result = SimpleNamespace(
        success=True,
        error=None,
        file_id="file-123",
        llm_total_input_tokens=100,
        llm_total_output_tokens=50,
        chunks_count=2,
    )
    service_factory, pipeline_factory, pipeline = _patch_pipeline(result)

    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
    ):
        out = await ds.run(_model())

    assert out.error is None
    assert out.entities == [{"id": "e1", "name": "Pierre"}]
    assert out.relationships == [{"id": "r1"}]
    assert out.input_tokens == 100
    assert out.output_tokens == 50
    # 2 chunks -> per-chunk latency list has length 2
    assert len(out.per_chunk_latency_ms) == 2
    assert out.extras["snapshot_db_path"].endswith("app.db")
    # The pipeline was driven with the dataset's corpus + domain.
    _, kwargs = pipeline.run.call_args
    assert kwargs["file_path"] == ds.corpus_path
    assert kwargs["domain"] == "literary"
    assert kwargs["skip_commit"] is True
    # Cleanup ran (keep_db False) -> ctx.disconnect called.
    ctx.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_run_pipeline_failure_returns_error(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    ctx = _fake_ctx(tmp_path, entities=[], relationships=[])
    result = SimpleNamespace(
        success=False,
        error="extraction_blew_up",
        file_id=None,
        llm_total_input_tokens=7,
        llm_total_output_tokens=3,
        chunks_count=0,
    )
    service_factory, pipeline_factory, _ = _patch_pipeline(result)
    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
    ):
        out = await ds.run(_model())

    assert out.error == "extraction_blew_up"
    assert out.entities == []
    assert out.relationships == []
    assert out.input_tokens == 7
    assert out.output_tokens == 3


@pytest.mark.asyncio
async def test_run_pipeline_failure_without_error_uses_default(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    ctx = _fake_ctx(tmp_path, entities=[], relationships=[])
    result = SimpleNamespace(
        success=False,
        error=None,  # triggers the "pipeline_failed" fallback string
        file_id=None,
        llm_total_input_tokens=0,
        llm_total_output_tokens=0,
        chunks_count=0,
    )
    service_factory, pipeline_factory, _ = _patch_pipeline(result)
    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
    ):
        out = await ds.run(_model())

    assert out.error == "pipeline_failed"


@pytest.mark.asyncio
async def test_run_empty_extraction_returns_empty_extraction_error(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    ctx = _fake_ctx(tmp_path, entities=[], relationships=[])
    result = SimpleNamespace(
        success=True,
        error=None,
        file_id="file-1",
        llm_total_input_tokens=5,
        llm_total_output_tokens=2,
        chunks_count=1,
    )
    service_factory, pipeline_factory, _ = _patch_pipeline(result)
    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
    ):
        out = await ds.run(_model())

    assert out.error == "empty_extraction"
    assert out.input_tokens == 5
    assert out.output_tokens == 2


@pytest.mark.asyncio
async def test_run_exception_is_caught_and_reported(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    # _build_temp_context raises -> run's except branch fires.
    with patch.object(ds, "_build_temp_context", side_effect=RuntimeError("ctx boom")):
        out = await ds.run(_model())

    assert out.error is not None
    assert "RuntimeError" in out.error
    assert "ctx boom" in out.error
    assert out.entities == []
    assert out.input_tokens == 0


@pytest.mark.asyncio
async def test_run_keep_db_preserves_dir(tmp_path: Path) -> None:
    """keep_db=True -> finally block disconnects but does not remove the dir."""
    ds = _ds(tmp_path, keep_db=True)
    ctx = _fake_ctx(
        tmp_path,
        entities=[{"id": "e1", "name": "x"}],
        relationships=[],
    )
    db_dir = ctx.database_dir
    result = SimpleNamespace(
        success=True,
        error=None,
        file_id="f1",
        llm_total_input_tokens=0,
        llm_total_output_tokens=0,
        chunks_count=1,
    )
    service_factory, pipeline_factory, _ = _patch_pipeline(result)
    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
        patch("chaoscypher_cli.benchmark.extraction_dataset._remove_temp_db_dir") as remove,
    ):
        out = await ds.run(_model())

    assert out.error is None
    ctx.disconnect.assert_called_once()
    remove.assert_not_called()  # keep_db True => cleanup skipped
    assert db_dir.exists()


def test_estimate_per_chunk_latency_distributes_evenly() -> None:
    per = ExtractionDataset._estimate_per_chunk_latency(1000, 4)
    assert per == [250, 250, 250, 250]


def test_estimate_per_chunk_latency_floor_is_one() -> None:
    # total // chunks == 0 -> floored to 1 per chunk.
    per = ExtractionDataset._estimate_per_chunk_latency(2, 5)
    assert per == [1, 1, 1, 1, 1]


def test_estimate_per_chunk_latency_zero_chunks_empty() -> None:
    assert ExtractionDataset._estimate_per_chunk_latency(1000, 0) == []


def test_expected_snapshot_path_matches_naming(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    model = ModelConfig(provider="ollama", model="Llama-3.1:8B", label="L")
    with patch.dict(os.environ, {"CHAOSCYPHER_DATA_DIR": str(tmp_path / "data")}):
        path = ds.expected_snapshot_path(model)
    # safe_model derives from model_id (provider/model), lowercased with
    # non-alphanumerics collapsed to underscore.
    assert path.name == "app.db"
    assert "benchmark_t_ollama_llama_3_1_8b_" in path.parent.name
    assert str(path).startswith(str(tmp_path / "data"))


def test_build_temp_context_overrides_model_fields(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    model = ModelConfig(provider="openai", model="gpt-4o", label="GPT")

    llm_settings = SimpleNamespace(
        openai_chat_model="old",
        openai_extraction_model="old",
        extraction_temperature=0.7,
        ai_temperature=0.7,
        seed=1,
        thinking_for_extraction=True,
    )
    fake_ctx = MagicMock()
    fake_ctx.settings = SimpleNamespace(llm=llm_settings)

    ctx_factory = MagicMock(return_value=fake_ctx)
    fake_context_mod = SimpleNamespace(CLIContext=ctx_factory)

    with patch.dict("sys.modules", {"chaoscypher_cli.context": fake_context_mod}):
        out = ds._build_temp_context(model)

    assert out is fake_ctx
    fake_ctx.connect.assert_called_once()
    # Provider env var set before construction.
    assert os.environ["CHAOSCYPHER_LLM_PROVIDER"] == "openai"
    # Model fields overridden post-connect.
    assert llm_settings.openai_chat_model == "gpt-4o"
    assert llm_settings.openai_extraction_model == "gpt-4o"
    # Determinism pinned, on the field names LLMSettings actually has. The
    # earlier version asserted `llm_settings.temperature`, a field that does not
    # exist on LLMSettings, so it passed against a stub while the real code
    # pinned nothing.
    assert llm_settings.extraction_temperature == 0.0
    assert llm_settings.ai_temperature == 0.0
    assert llm_settings.seed == 42
    assert llm_settings.thinking_for_extraction is False
    # Provider cache reset.
    assert fake_ctx._llm_provider is None
    assert fake_ctx._llm_checked is False
    # DB name uses dataset id + safe model_id (provider/model) + pid.
    _, kwargs = ctx_factory.call_args
    assert kwargs["database_name"].startswith("benchmark_t_openai_gpt_4o_")


def test_build_temp_context_unknown_provider_skips_model_fields(tmp_path: Path) -> None:
    ds = _ds(tmp_path)
    model = ModelConfig(provider="mystery", model="m1", label="M")

    # Settings without the pinning fields must RAISE, not skip silently. This
    # test previously asserted the opposite, which is how the benchmark shipped
    # for months claiming temperature 0 and a fixed seed while applying neither:
    # the guards keyed on field names ("temperature", "seed") that LLMSettings
    # never had, so both assignments quietly no-opped.
    llm_settings = SimpleNamespace()
    fake_ctx = MagicMock()
    fake_ctx.settings = SimpleNamespace(llm=llm_settings)
    ctx_factory = MagicMock(return_value=fake_ctx)
    fake_context_mod = SimpleNamespace(CLIContext=ctx_factory)

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.context": fake_context_mod}),
        pytest.raises(AttributeError, match="extraction_temperature"),
    ):
        ds._build_temp_context(model)

    # Unknown provider -> no model fields set before the pin check raised.
    assert not hasattr(llm_settings, "mystery_chat_model")


def test_remove_temp_db_dir_evicts_and_removes(tmp_path: Path) -> None:
    db_dir = tmp_path / "benchmark_x"
    db_dir.mkdir()
    (db_dir / "app.db").write_bytes(b"db")

    fake_engine_mod = SimpleNamespace(evict_engine=MagicMock())
    with patch.dict(
        "sys.modules",
        {"chaoscypher_core.adapters.sqlite.engine": fake_engine_mod},
    ):
        _remove_temp_db_dir(db_dir, dataset_id="x")

    fake_engine_mod.evict_engine.assert_called_once_with(db_dir / "app.db")
    assert not db_dir.exists()


def test_remove_temp_db_dir_handles_evict_failure(tmp_path: Path) -> None:
    """An evict error is swallowed; rmtree still removes the directory."""
    db_dir = tmp_path / "benchmark_y"
    db_dir.mkdir()
    (db_dir / "app.db").write_bytes(b"db")

    fake_engine_mod = SimpleNamespace(evict_engine=MagicMock(side_effect=RuntimeError("no engine")))
    with patch.dict(
        "sys.modules",
        {"chaoscypher_core.adapters.sqlite.engine": fake_engine_mod},
    ):
        _remove_temp_db_dir(db_dir, dataset_id="y")

    assert not db_dir.exists()


def test_remove_temp_db_dir_retries_then_warns_on_persistent_failure(
    tmp_path: Path,
) -> None:
    """When rmtree keeps failing, the retry loop exhausts and logs a warning.

    We sleep through the retry delays by stubbing time.sleep, and make
    shutil.rmtree always raise so every attempt fails. The function must
    return without raising (best-effort cleanup contract).
    """
    db_dir = tmp_path / "benchmark_z"
    db_dir.mkdir()
    (db_dir / "app.db").write_bytes(b"db")

    fake_engine_mod = SimpleNamespace(evict_engine=MagicMock())
    with (
        patch.dict(
            "sys.modules",
            {"chaoscypher_core.adapters.sqlite.engine": fake_engine_mod},
        ),
        patch(
            "chaoscypher_cli.benchmark.extraction_dataset.shutil.rmtree",
            side_effect=OSError("WinError 32: file in use"),
        ),
        patch("chaoscypher_cli.benchmark.extraction_dataset.time.sleep"),
    ):
        # Must not raise even though every removal attempt fails.
        _remove_temp_db_dir(db_dir, dataset_id="z")

    # Directory still present because rmtree was stubbed to always fail.
    assert db_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_graph", [False, True])
async def test_commit_graph_does_not_change_extraction_latency(
    tmp_path: Path, commit_graph: bool
) -> None:
    """The timed call is extract-only either way; embed + commit run untimed after it.

    Before the split, ``commit_graph`` folded chunk embedding and the commit
    stage into the timed pipeline call, so the orchestrated extraction row's
    latency differed from the plain benchmark's for the same model.
    """
    from chaoscypher_cli.benchmark import extraction_dataset as mod

    ds = _ds(tmp_path)
    ds.commit_graph = commit_graph
    ctx = _fake_ctx(tmp_path, entities=[{"id": "e1", "name": "Pierre"}], relationships=[])
    chunks = [{"id": "c1", "content": "Pierre visited Moscow."}]
    ctx.storage_adapter.get_chunks_by_source.return_value = (chunks, 1)
    now = [0.0]

    def _clock() -> float:
        """Fake perf_counter driven by the pipeline fakes."""
        return now[0]

    extracted = SimpleNamespace(
        success=True,
        error=None,
        file_id="file-9",
        llm_total_input_tokens=10,
        llm_total_output_tokens=5,
        chunks_count=2,
    )
    committed = SimpleNamespace(success=True, error=None)

    def _run(**kwargs: Any) -> SimpleNamespace:
        """Extract takes 2s, the resumed commit 50s."""
        if kwargs.get("file_id") is None:
            now[0] += 2.0
            return extracted
        now[0] += 50.0
        return committed

    service_factory, pipeline_factory, pipeline = _patch_pipeline(extracted)
    pipeline.run = MagicMock(side_effect=_run)
    service = service_factory.return_value
    service._generate_embeddings = MagicMock(
        side_effect=lambda *_a: now.__setitem__(0, now[0] + 30)
    )
    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
        patch.object(mod, "time", SimpleNamespace(perf_counter=_clock)),
    ):
        out = await ds.run(ModelConfig(provider="openai", model="gpt-x", label="G"))

    assert out.error is None
    assert out.latency_ms == 2000
    assert out.per_chunk_latency_ms == [1000, 1000]
    first = pipeline.run.call_args_list[0].kwargs
    assert (first["skip_commit"], first["extract_only"], first["skip_embeddings"]) == (
        True,
        True,
        True,
    )
    if not commit_graph:
        assert pipeline.run.call_count == 1
        service._generate_embeddings.assert_not_called()
        return
    assert pipeline.run.call_count == 2
    service._generate_embeddings.assert_called_once_with("file-9", chunks)
    second = pipeline.run.call_args_list[1].kwargs
    assert second["file_id"] == "file-9"
    assert second["file_path"] is None
    assert (second["skip_index"], second["skip_extract"], second["skip_commit"]) == (
        True,
        True,
        False,
    )


@pytest.mark.asyncio
async def test_commit_graph_failure_is_reported_on_the_row(tmp_path: Path) -> None:
    """A failed resumed commit fails the row rather than caching an uncommitted graph."""
    ds = _ds(tmp_path)
    ds.commit_graph = True
    ctx = _fake_ctx(tmp_path, entities=[{"id": "e1", "name": "Pierre"}], relationships=[])
    ctx.storage_adapter.get_chunks_by_source.return_value = ([], 0)
    extracted = SimpleNamespace(
        success=True,
        error=None,
        file_id="file-9",
        llm_total_input_tokens=0,
        llm_total_output_tokens=0,
        chunks_count=1,
    )
    service_factory, pipeline_factory, pipeline = _patch_pipeline(extracted)
    pipeline.run = MagicMock(
        side_effect=[extracted, SimpleNamespace(success=False, error="graph locked")]
    )
    fake_sources = SimpleNamespace(
        CLISourceProcessingService=service_factory,
        SourcePipeline=pipeline_factory,
    )

    with (
        patch.dict("sys.modules", {"chaoscypher_cli.sources": fake_sources}),
        patch.object(ds, "_build_temp_context", return_value=ctx),
    ):
        out = await ds.run(ModelConfig(provider="openai", model="gpt-x", label="G"))

    assert out.error == "commit_failed: graph locked"
