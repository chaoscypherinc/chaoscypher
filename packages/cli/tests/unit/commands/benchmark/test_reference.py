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

from chaoscypher_cli.benchmark.config import DEFAULT_SEED, DEFAULT_TEMPERATURE
from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
from chaoscypher_cli.benchmark.graph_cache import (
    cache_key,
    dataset_corpus_identity,
    extraction_recipe_for,
    resolution_for,
)
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.commands.benchmark.reference import reference_group
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.benchmark.reference import load_pack


DATASET = "war_and_peace_book1"
EXTRACTOR = "ollama/gemma4:31b"
EMBEDDER = "ollama/qwen3-embedding:0.6b"
DEDUP = "local/Qwen/Qwen3-Embedding-0.6B"


def _cached_graph(workspace: Path) -> str:
    """Put a snapshot in the workspace's cache slot for DATASET + EXTRACTOR; return the key."""
    bundle = load_dataset_bundle(DATASET)
    domain, corpus_sha256 = dataset_corpus_identity(DATASET)
    key = cache_key(
        corpus_id=bundle.id,
        corpus_version=bundle.version,
        extractor=ModelConfig(provider="ollama", model="gemma4:31b", label="x"),
        # `reference export` defaults to the same pins `benchmark run` does.
        seed=DEFAULT_SEED,
        temperature=DEFAULT_TEMPERATURE,
        dedup_embedder=DEDUP,
        resolution=resolution_for(get_settings()),  # the resolver is on by default
        # every GraphCache keys with the settings' extraction recipe (audit H7) under the
        # corpus's extraction domain (audit wave 4, M3)
        extraction_recipe=extraction_recipe_for(get_settings(), domain=domain),
        # and the extraction domain and the corpus bytes (audit wave 2)
        domain=domain,
        corpus_sha256=corpus_sha256,
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
            "--dedup-embedder",
            DEDUP,
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
    assert pack.resolution == resolution_for(get_settings())  # recorded for install.py's slot
    assert pack.extraction_recipe == extraction_recipe_for(
        get_settings(), domain="literary"
    )  # the recipe the key proves
    # the domain and corpus the graph cache keys on (audit W2-4): install.py seeds the same slot
    assert pack.extraction_domain == "literary"
    assert pack.corpus_sha256 is not None and len(pack.corpus_sha256) == 64
    assert pack.dedup_embedder == DEDUP
    assert pack.fixture_version == load_dataset_bundle(DATASET).version
    with closing(sqlite3.connect(pack.snapshot_path)) as conn:
        assert conn.execute("SELECT database_name FROM sources").fetchone() == (
            "benchmark_book1_run",
        )
    assert pack.load_queries().queries
    assert not pack.index_is_ready()


def test_export_under_a_floor_overlay_records_the_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v5 floor arm: a user overlay with ``packs: []`` exports ``extraction_domain: generic``.

    The built-in manifest keeps ``domain: literary``; the overlay (a copy of
    the dataset directory, the user's version winning) names no pack, and the
    pack records the floor's label and the floor's recipe, the slot the sweep
    computed.
    """
    import shutil

    from chaoscypher_cli.benchmark.discovery import builtin_dataset_root, user_dataset_root

    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path / "home"))
    overlay = user_dataset_root() / DATASET
    shutil.copytree(builtin_dataset_root() / DATASET, overlay)
    manifest = overlay / "manifest.yaml"
    text = manifest.read_text(encoding="utf-8")
    assert "domain: literary\n" in text
    manifest.write_text(text.replace("domain: literary\n", "packs: []\n"), encoding="utf-8")

    key = _cached_graph(tmp_path / "ws")
    result = _export(tmp_path / "ws", tmp_path / "data")
    assert result.exit_code == 0, result.output
    pack = load_pack(tmp_path / "data", DATASET)
    assert pack.graph_cache_key == key
    assert pack.extraction_domain == "generic"
    assert pack.extraction_recipe == extraction_recipe_for(get_settings(), domain="generic")


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
            "--dedup-embedder",
            DEDUP,
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


def test_export_refuses_a_slot_install_adopted(tmp_path: Path) -> None:
    """Audit wave 3: an adopted slot holds an older pack's graph under today's key, not a new pack."""
    import json

    from chaoscypher_cli.benchmark.graph_cache import SLOT_RECORD

    key = _cached_graph(tmp_path / "ws")
    record = tmp_path / "ws" / "graph_cache" / key / SLOT_RECORD
    record.write_text(json.dumps({"adopted_from": "war_and_peace_book1-v3"}), encoding="utf-8")
    result = _export(tmp_path / "ws", tmp_path / "data")
    assert result.exit_code != 0
    assert "seeded from pack 'war_and_peace_book1-v3'" in result.output
    assert not (tmp_path / "data" / "benchmark" / "reference" / DATASET).exists()


def test_run_export_and_install_share_one_default_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit wave 3: install.py seeded <paths.data_dir>/benchmark/workspace, the CLI read another."""
    import importlib
    import inspect

    from chaoscypher_cli.benchmark import graph_cache
    from chaoscypher_cli.benchmark.discovery import user_benchmark_root

    assert graph_cache.default_workspace() == user_benchmark_root() / "workspace"
    # export without --workspace reads the shared default
    _cached_graph(tmp_path / "shared")
    monkeypatch.setattr(graph_cache, "default_workspace", lambda: tmp_path / "shared")
    result = CliRunner().invoke(
        reference_group,
        [
            "export",
            "--dataset",
            DATASET,
            "--extractor",
            EXTRACTOR,
            "--embedder",
            EMBEDDER,
            "--dedup-embedder",
            DEDUP,
            "--data-dir",
            str(tmp_path / "data"),
            "--no-index",
        ],
    )
    assert result.exit_code == 0, result.output
    run_module = importlib.import_module("chaoscypher_cli.commands.benchmark.run")
    assert "workspace = default_workspace()" in inspect.getsource(run_module)


# ------------------------------------------------------------------ LLM store


_BUILD = "ollama:gemma4:31b:abc123@0.12.1"


def _record(*builds: str) -> Path:
    """Fill the run's completion store (the autouse fixture's tmp file) with one entry per build."""
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.cache import wiring
    from chaoscypher_core.cache.chat import COMPLETIONS_NAMESPACE
    from chaoscypher_core.cache.store import CacheEntry, encode_value

    store = wiring.open_store(build_engine_settings(get_settings()), "completions")
    for i, build in enumerate(builds):
        value = encode_value({"content": f"answer {i}", "finish_reason": "stop"})
        store.put(
            COMPLETIONS_NAMESPACE,
            f"key{i}",
            CacheEntry(
                value=value,
                model_build=build,
                call_site="extraction",
                seeded=True,
                latency_ms=10,
                bytes=len(value),
            ),
        )
    return store.path


def test_export_ships_the_extractors_completions_and_names_them_in_the_manifest(
    tmp_path: Path,
) -> None:
    """Only the extractor's build: a chat model's answers in the same store stay behind."""
    from chaoscypher_core.cache.export import file_sha256
    from chaoscypher_core.cache.sqlite_store import read_entries

    _cached_graph(tmp_path / "ws")
    _record(_BUILD, _BUILD, "ollama:qwen3:14b:fff000@0.12.1", "ollama:gemma4:31b-it:aaa@0.12.1")
    result = _export(tmp_path / "ws", tmp_path / "data")
    assert result.exit_code == 0, result.output

    pack = load_pack(tmp_path / "data", DATASET)
    assert pack.llm_store == "llm_store.db" and pack.model_build == _BUILD
    assert pack.llm_store_path is not None
    assert pack.llm_store_sha256 == file_sha256(pack.llm_store_path)
    rows = list(read_entries(pack.llm_store_path))
    assert [(ns, key) for ns, key, _ in rows] == [("completions", "key0"), ("completions", "key1")]
    assert {entry.model_build for _, _, entry in rows} == {_BUILD}
    assert pack.verify_llm_store() == pack.llm_store_path


def test_export_asks_which_build_when_the_store_holds_two(tmp_path: Path) -> None:
    _cached_graph(tmp_path / "ws")
    older = "ollama:gemma4:31b:0ld000@0.11.0"
    _record(_BUILD, older)
    refused = _export(tmp_path / "ws", tmp_path / "data")
    assert refused.exit_code != 0 and "--model-build" in " ".join(refused.output.split())
    chosen = _export(tmp_path / "ws", tmp_path / "data", "--model-build", older)
    assert chosen.exit_code == 0, chosen.output
    assert load_pack(tmp_path / "data", DATASET).model_build == older
    unknown = _export(
        tmp_path / "ws", tmp_path / "data", "--force", "--model-build", "ollama:gemma4:31b:nope"
    )
    assert unknown.exit_code != 0 and "has no completions" in " ".join(unknown.output.split())


def test_export_without_recorded_completions_ships_no_store(tmp_path: Path) -> None:
    _cached_graph(tmp_path / "ws")
    result = _export(tmp_path / "ws", tmp_path / "data")
    assert result.exit_code == 0, result.output
    assert "ships no LLM store" in " ".join(result.output.split())  # rich wraps lines
    pack = load_pack(tmp_path / "data", DATASET)
    assert pack.llm_store is None and not (pack.root / "llm_store.db").exists()
    _record(_BUILD)
    skipped = _export(tmp_path / "ws", tmp_path / "data", "--force", "--no-llm-store")
    assert skipped.exit_code == 0, skipped.output
    assert load_pack(tmp_path / "data", DATASET).llm_store is None
