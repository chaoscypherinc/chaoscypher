# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for `chaoscypher benchmark reference export` and `... list`."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
from chaoscypher_cli.benchmark.graph_cache import cache_key
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.commands.benchmark.reference import reference_group
from chaoscypher_core.benchmark.reference import load_pack


DATASET = "war_and_peace_book1"
EXTRACTOR = "ollama/gemma4:31b"
EMBEDDER = "ollama/qwen3-embedding:0.6b"


def _cached_graph(workspace: Path) -> str:
    """Put a snapshot in the workspace's cache slot for DATASET + EXTRACTOR; return the key."""
    bundle = load_dataset_bundle(DATASET)
    key = cache_key(
        corpus_id=bundle.id,
        corpus_version=bundle.version,
        extractor=ModelConfig(provider="ollama", model="gemma4:31b", label="x"),
    )
    slot = workspace / "graph_cache" / key
    slot.mkdir(parents=True)
    with closing(sqlite3.connect(slot / "app.db")) as conn:
        conn.execute("CREATE TABLE sources (id TEXT, database_name TEXT)")
        conn.execute("INSERT INTO sources VALUES ('s1', 'benchmark_book1_run')")
        conn.commit()
    return key


def _export(workspace: Path, data_dir: Path, *extra: str) -> Any:
    """Invoke export with the test's workspace and data dir, without indexing."""
    return CliRunner().invoke(
        reference_group,
        [
            "export",
            "--workspace",
            str(workspace),
            "--dataset",
            DATASET,
            "--extractor",
            EXTRACTOR,
            "--embedder",
            EMBEDDER,
            "--data-dir",
            str(data_dir),
            "--no-index",
            *extra,
        ],
    )


def test_export_writes_a_pack_the_bridge_can_load(tmp_path: Path) -> None:
    """Snapshot, fixture and manifest land under <data_dir>/benchmark/reference/<dataset>."""
    key = _cached_graph(tmp_path / "ws")
    result = _export(tmp_path / "ws", tmp_path / "data")
    assert result.exit_code == 0, result.output

    pack = load_pack(tmp_path / "data", DATASET)
    assert pack.fixture_id == DATASET and pack.corpus_id == DATASET
    assert pack.extractor == EXTRACTOR and pack.embedder == EMBEDDER
    assert pack.extractor_label == "Gemma 4 31B"
    assert pack.graph_cache_key == key
    assert pack.fixture_version == load_dataset_bundle(DATASET).version
    with closing(sqlite3.connect(pack.snapshot_path)) as conn:
        assert conn.execute("SELECT database_name FROM sources").fetchone() == (
            "benchmark_book1_run",
        )
    assert pack.load_queries().queries
    assert not pack.index_is_ready()


def test_export_refuses_a_missing_graph_and_an_existing_pack(tmp_path: Path) -> None:
    """No cached graph is an error; an existing pack needs --force."""
    result = _export(tmp_path / "ws", tmp_path / "data")
    assert result.exit_code != 0 and "no cached graph" in result.output

    _cached_graph(tmp_path / "ws")
    assert _export(tmp_path / "ws", tmp_path / "data", "--name", "book").exit_code == 0
    again = _export(tmp_path / "ws", tmp_path / "data", "--name", "book")
    assert again.exit_code != 0 and "--force" in again.output
    forced = _export(tmp_path / "ws", tmp_path / "data", "--name", "book", "--force")
    assert forced.exit_code == 0, forced.output
    assert load_pack(tmp_path / "data", "book").name == "book"


def test_export_indexes_the_pack_unless_told_not_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--index (the default) builds the indexed copy right away."""
    import chaoscypher_cli.commands.benchmark.reference as mod

    indexed: list[str] = []

    async def _fake_index(pack: Any) -> None:
        indexed.append(pack.name)

    monkeypatch.setattr(mod, "_build_index", _fake_index)
    _cached_graph(tmp_path / "ws")
    result = CliRunner().invoke(
        reference_group,
        [
            "export",
            "--workspace",
            str(tmp_path / "ws"),
            "--dataset",
            DATASET,
            "--extractor",
            EXTRACTOR,
            "--embedder",
            EMBEDDER,
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert indexed == [DATASET]


def test_list_shows_packs(tmp_path: Path) -> None:
    """List prints each pack with its extractor and embedder."""
    empty = CliRunner().invoke(reference_group, ["list", "--data-dir", str(tmp_path / "data")])
    assert empty.exit_code == 0 and "No reference packs" in empty.output
    _cached_graph(tmp_path / "ws")
    _export(tmp_path / "ws", tmp_path / "data")
    result = CliRunner().invoke(
        reference_group, ["list", "--data-dir", str(tmp_path / "data")], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.output
    assert DATASET in result.output and EMBEDDER in result.output
