# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reference packs: manifest validation, listing, and the indexed copy's READY marker."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
import yaml

from chaoscypher_core.benchmark.reference import (
    READY_FILE,
    ReferencePackError,
    list_packs,
    load_pack,
    reference_root,
    write_manifest,
)
from chaoscypher_core.settings import EngineSettings


_QUERIES = """
version: "1.0"
queries:
  - id: q001
    band: factual_single_hop
    question: "Who commanded the Russian army?"
    gold_entities: ["Kutuzov"]
    answer_terms: ["Kutuzov"]
    gold_answer: Kutuzov.
"""


def _fields(name: str, **over: str) -> dict[str, str]:
    """A complete manifest for pack ``name``."""
    return {
        "name": name,
        "fixture_id": "war_and_peace_book1",
        "fixture_version": "1.0",
        "corpus_id": "war_and_peace_book1",
        "extractor": "ollama/gemma4:31b",
        "extractor_label": "Gemma 4 31B",
        "embedder": "ollama/qwen3-embedding:0.6b",
        "graph_cache_key": "1c1cdbcdfa49267e",  # pragma: allowlist secret
        "created": "2026-09-26T00:00:00+00:00",
        "snapshot_file": "app.db",
        "queries_file": "queries.yaml",
        **over,
    }


def _pack(data_dir: Path, name: str, *, db_name: str | None = None, **over: str) -> Path:
    """Write a pack whose snapshot has a sources row scoped to ``db_name``."""
    root = reference_root(data_dir) / name
    root.mkdir(parents=True)
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.execute("CREATE TABLE sources (id TEXT, database_name TEXT)")
        if db_name:
            conn.execute("INSERT INTO sources VALUES ('s1', ?)", (db_name,))
        conn.commit()
    (root / "queries.yaml").write_text(_QUERIES, encoding="utf-8")
    write_manifest(root, _fields(name, **over))
    return root


def test_a_written_manifest_loads_back(tmp_path: Path) -> None:
    """write_manifest + load_pack round-trip every field."""
    _pack(tmp_path, "book1")
    pack = load_pack(tmp_path, "book1")
    assert pack.name == "book1" and pack.extractor == "ollama/gemma4:31b"
    assert pack.embedder == "ollama/qwen3-embedding:0.6b"
    assert pack.graph_cache_key == "1c1cdbcdfa49267e"  # pragma: allowlist secret
    assert pack.manifest["fixture_id"] == "war_and_peace_book1"
    assert [q.id for q in pack.load_queries().queries] == ["q001"]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda m: m.pop("embedder"), "embedder"),
        (lambda m: m.update(fixture_version=1), "fixture_version"),
        (lambda m: m.update(name="other"), "must match the directory"),
        (lambda m: m.update(embedder="qwen3-embedding"), "provider/model"),
        (lambda m: m.update(snapshot_file="../app.db"), "plain file name"),
        (lambda m: m.update(queries_file="missing.yaml"), "missing from the pack"),
    ],
)
def test_a_bad_manifest_is_rejected(tmp_path: Path, edit: Any, message: str) -> None:
    """Missing or non-string fields, a wrong name, a bad model id or a missing file."""
    root = _pack(tmp_path, "book1")
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    edit(manifest)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ReferencePackError, match=message):
        load_pack(tmp_path, "book1")


def test_load_pack_refuses_unknown_and_malformed_names(tmp_path: Path) -> None:
    """A name is a directory name: no traversal, and it must exist."""
    with pytest.raises(ReferencePackError, match="no reference pack"):
        load_pack(tmp_path, "book1")
    with pytest.raises(ReferencePackError, match="not valid"):
        load_pack(tmp_path, "../book1")


def test_list_packs_is_sorted_and_skips_invalid_packs(tmp_path: Path) -> None:
    """Only packs that validate are listed."""
    assert list_packs(tmp_path) == []
    _pack(tmp_path, "zeta")
    _pack(tmp_path, "alpha")
    broken = reference_root(tmp_path) / "broken"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("name: broken\n", encoding="utf-8")
    assert [p.name for p in list_packs(tmp_path)] == ["alpha", "zeta"]


class _FakeEngine:
    """Stands in for the core Engine: records how it was opened and closed."""

    def __init__(self, data_dir: Path, settings: EngineSettings) -> None:
        """Record the database directory and the settings."""
        self.data_dir = data_dir
        self.settings = settings
        self.closed = False

    def close(self) -> None:
        """Mark closed."""
        self.closed = True


@pytest.mark.asyncio
async def test_the_first_use_indexes_a_copy_and_later_uses_skip_it(tmp_path: Path) -> None:
    """READY names the embedder and graph; a match skips the reindex, a change redoes it."""
    root = _pack(tmp_path, "book1", db_name="benchmark_book1_run")
    engines: list[_FakeEngine] = []
    reindexed: list[_FakeEngine] = []

    def factory(data_dir: Path, settings: EngineSettings) -> _FakeEngine:
        engine = _FakeEngine(data_dir, settings)
        engines.append(engine)
        return engine

    async def reindex(engine: Any) -> None:
        reindexed.append(engine)

    base = EngineSettings()
    base_embedder = (base.embedding.provider, base.embedding.model)
    pack = load_pack(tmp_path, "book1")
    assert not pack.index_is_ready()
    async with pack.indexed_engine(base, engine_factory=factory, reindex=reindex) as engine:
        # The copy sits under the snapshot's database name; the engine embeds
        # with the pack's embedder; the caller's settings are untouched.
        assert engine.data_dir == root / "index" / "databases" / "benchmark_book1_run"
        assert (engine.data_dir / "app.db").is_file()
        assert (engine.settings.embedding.provider, engine.settings.embedding.model) == (
            "ollama",
            "qwen3-embedding:0.6b",
        )
        assert (base.embedding.provider, base.embedding.model) == base_embedder
    assert engines[0].closed and reindexed == [engines[0]]
    assert pack.index_is_ready()
    assert "qwen3-embedding:0.6b" in (root / "index" / READY_FILE).read_text(encoding="utf-8")

    async with pack.indexed_engine(base, engine_factory=factory, reindex=reindex):
        pass
    assert len(engines) == 2 and len(reindexed) == 1

    # A pack re-exported with another embedder invalidates the copy.
    write_manifest(root, _fields("book1", embedder="ollama/nomic-embed-text"))
    repacked = load_pack(tmp_path, "book1")
    assert not repacked.index_is_ready()
    async with repacked.indexed_engine(base, engine_factory=factory, reindex=reindex):
        pass
    assert len(reindexed) == 2 and repacked.index_is_ready()


@pytest.mark.asyncio
async def test_a_failed_reindex_leaves_no_ready_marker(tmp_path: Path) -> None:
    """An interrupted index is rebuilt next time, never trusted."""
    _pack(tmp_path, "book1")
    pack = load_pack(tmp_path, "book1")

    async def failing(_engine: Any) -> None:
        raise RuntimeError("embedder down")

    with pytest.raises(RuntimeError, match="embedder down"):
        async with pack.indexed_engine(
            EngineSettings(), engine_factory=_FakeEngine, reindex=failing
        ):
            pass
    assert not pack.index_is_ready()
    assert (pack.index_dir / "databases" / "app" / "app.db").is_file()
