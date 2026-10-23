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

from chaoscypher_core.benchmark.pins import file_sha256
from chaoscypher_core.benchmark.reference import (
    READY_FILE,
    ReferencePackError,
    embedder_dimensions,
    list_packs,
    load_pack,
    pack_domain,
    reference_root,
    snapshot_domain,
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
        "dedup_embedder": "local/Qwen/Qwen3-Embedding-0.6B",
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


def test_the_optional_resolution_field_round_trips_and_is_none_when_absent(tmp_path: Path) -> None:
    _pack(tmp_path, "plain")
    assert load_pack(tmp_path, "plain").resolution is None
    _pack(tmp_path, "resolved", resolution="0123456789ab")
    pack = load_pack(tmp_path, "resolved")
    assert pack.resolution == "0123456789ab" and pack.manifest["resolution"] == "0123456789ab"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda m: m.pop("embedder"), "embedder"),
        (lambda m: m.update(fixture_version=1), "fixture_version"),
        (lambda m: m.update(name="other"), "must match the directory"),
        (lambda m: m.update(embedder="qwen3-embedding"), "provider/model"),
        (lambda m: m.pop("dedup_embedder"), "dedup_embedder"),
        (lambda m: m.update(dedup_embedder="qwen3-embedding"), "provider/model"),
        (lambda m: m.update(snapshot_file="../app.db"), "plain file name"),
        (lambda m: m.update(queries_file="missing.yaml"), "missing from the pack"),
        (lambda m: m.update(resolution=""), "resolution"),
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
        # indexed and queried at the embedder's native size, whatever the base says
        assert engine.settings.search.vector_dimensions == 1024
        assert (base.embedding.provider, base.embedding.model) == base_embedder
    assert engines[0].closed and reindexed == [engines[0]]
    assert pack.index_is_ready()
    assert "qwen3-embedding:0.6b" in (root / "index" / READY_FILE).read_text(encoding="utf-8")

    async with pack.indexed_engine(base, engine_factory=factory, reindex=reindex):
        pass
    assert len(engines) == 2 and len(reindexed) == 1

    # A pack re-exported with another embedder invalidates the copy.
    write_manifest(root, _fields("book1", embedder="ollama/qwen3-embedding:4b"))
    repacked = load_pack(tmp_path, "book1")
    assert not repacked.index_is_ready()
    async with repacked.indexed_engine(base, engine_factory=factory, reindex=reindex) as engine:
        assert engine.settings.search.vector_dimensions == 2560
    assert len(reindexed) == 2 and repacked.index_is_ready()


@pytest.mark.asyncio
async def test_a_graph_rebuild_keeps_the_naive_index_beside_it(tmp_path: Path) -> None:
    """index/naive-<embedder> has its own READY; rebuilding the graph copy must not delete it."""
    root = _pack(tmp_path, "book1")
    pack = load_pack(tmp_path, "book1")
    naive = root / "index" / "naive-ollama_qwen3-embedding:0.6b"
    naive.mkdir(parents=True)
    (naive / READY_FILE).write_text("{}", encoding="utf-8")
    stale = root / "index" / "databases" / "old" / "app.db"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"")

    async def reindex(_engine: Any) -> None:
        return None

    async with pack.indexed_engine(EngineSettings(), engine_factory=_FakeEngine, reindex=reindex):
        pass
    assert (naive / READY_FILE).is_file()
    assert not stale.exists()
    assert pack.index_is_ready()


def test_an_index_built_under_an_older_reindex_recipe_is_not_ready(tmp_path: Path) -> None:
    """A READY written before the recipe was recorded (node labels alone) asks for a rebuild."""
    import json

    from chaoscypher_core.benchmark.reindex import reindex_recipe

    root = _pack(tmp_path, "book1", db_name="benchmark_book1_run")
    pack = load_pack(tmp_path, "book1")
    pack.index_db_path.parent.mkdir(parents=True)
    pack.index_db_path.write_bytes(b"")
    marker: dict[str, Any] = {
        "embedder": pack.embedder,
        "vector_dimensions": 1024,
        "graph_cache_key": pack.graph_cache_key,
        "snapshot_sha256": file_sha256(pack.snapshot_path),
    }
    (root / "index" / READY_FILE).write_text(json.dumps(marker), encoding="utf-8")
    assert not pack.index_is_ready()
    marker["reindex_recipe"] = 2  # the hand-bumped integer READY carried on 2026-10-03
    (root / "index" / READY_FILE).write_text(json.dumps(marker), encoding="utf-8")
    assert not pack.index_is_ready()
    marker["reindex_recipe"] = reindex_recipe()
    (root / "index" / READY_FILE).write_text(json.dumps(marker), encoding="utf-8")
    assert pack.index_is_ready()


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


def _ready_marker_of(pack: Any) -> dict[str, Any]:
    """The READY marker a freshly built index of ``pack`` records."""
    import json

    return dict(json.loads((pack.index_dir / READY_FILE).read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_ready_records_dimensions_and_snapshot_and_either_changing_invalidates(
    tmp_path: Path,
) -> None:
    """A READY written under other dimensions or for another snapshot is not ready (audit R8)."""
    import json

    root = _pack(tmp_path, "book1")
    pack = load_pack(tmp_path, "book1")

    async def reindex(_engine: Any) -> None:
        return None

    async with pack.indexed_engine(EngineSettings(), engine_factory=_FakeEngine, reindex=reindex):
        pass
    marker = _ready_marker_of(pack)
    assert marker["vector_dimensions"] == embedder_dimensions(pack.embedder) == 1024
    assert marker["snapshot_sha256"] == file_sha256(pack.snapshot_path)
    assert pack.index_is_ready()

    (root / "index" / READY_FILE).write_text(
        json.dumps({**marker, "vector_dimensions": 512}), encoding="utf-8"
    )
    assert not pack.index_is_ready()

    (root / "index" / READY_FILE).write_text(json.dumps(marker), encoding="utf-8")
    assert pack.index_is_ready()
    with closing(sqlite3.connect(root / "app.db")) as conn:  # the snapshot is replaced
        conn.execute("INSERT INTO sources VALUES ('s2', 'other')")
        conn.commit()
    assert not pack.index_is_ready()


def test_an_embedder_of_unknown_native_size_is_refused() -> None:
    """Indexing at a guessed size is how R1 happened: an unknown model raises instead."""
    assert embedder_dimensions("ollama/qwen3-embedding:4b") == 2560
    with pytest.raises(ReferencePackError, match="native vector size"):
        embedder_dimensions("ollama/some-unlisted-embedder")


def _with_sources(root: Path, *rows: tuple[str | None, str | None]) -> None:
    """Give the pack's snapshot a ``sources`` table of ``(forced_domain, extraction_domain)`` rows."""
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.execute("DROP TABLE sources")
        conn.execute("CREATE TABLE sources (forced_domain TEXT, extraction_domain TEXT)")
        conn.executemany("INSERT INTO sources VALUES (?, ?)", rows)
        conn.commit()


def test_the_pack_domain_is_the_manifests_then_the_snapshots(tmp_path: Path) -> None:
    """``extraction_domain``, else the manifest's ``domain``, else the snapshot's source rows (wave 4).

    A v3 pack records neither in its manifest: the MCP suite read ``domain``
    alone and resolved its profile under no domain while the CLI used the
    dataset's.
    """
    data = tmp_path / "data"
    v3 = _pack(data, "v3")
    _with_sources(v3, ("literary", None))
    assert load_pack(data, "v3").domain == "literary"
    assert snapshot_domain(v3 / "app.db") == "literary"

    detected = _pack(data, "detected")
    _with_sources(detected, (None, "historical"), (None, "historical"))
    assert pack_domain(load_pack(data, "detected")) == "historical"

    exported = _pack(data, "exported", extraction_domain="literary")
    _with_sources(exported, ("literary", None))
    assert load_pack(data, "exported").domain == "literary"

    hand = _pack(data, "hand")
    manifest = yaml.safe_load((hand / "manifest.yaml").read_text(encoding="utf-8"))
    (hand / "manifest.yaml").write_text(
        yaml.safe_dump({**manifest, "domain": "medical"}), encoding="utf-8"
    )
    assert load_pack(data, "hand").domain == "medical"

    # sources that chose different packs read as their union, sorted and joined (2026-10-07)
    mixed = _pack(data, "mixed")
    _with_sources(mixed, ("literary", None), (None, "legal"))
    assert load_pack(data, "mixed").domain == "legal+literary"
    # the floor adds no pack; a floor-only snapshot is the floor's name
    floored = _pack(data, "floored")
    _with_sources(floored, ("generic", None), (None, "literary"))
    assert snapshot_domain(floored / "app.db") == "literary"
    floor_only = _pack(data, "floor_only")
    _with_sources(floor_only, ("generic", None), (None, None))
    assert snapshot_domain(floor_only / "app.db") == "generic"
    # a manifest's label is read with its packs sorted, so it matches the snapshot's
    labelled = _pack(data, "labelled", extraction_domain="literary+legal")
    _with_sources(labelled, ("literary", None), (None, "legal"))
    assert load_pack(data, "labelled").domain == "legal+literary"
    # a snapshot whose sources table has no domain columns names none
    _pack(data, "old")
    assert load_pack(data, "old").domain is None


@pytest.mark.parametrize("key", ["extraction_domain", "domain"])
def test_a_manifest_domain_the_snapshot_contradicts_is_refused(tmp_path: Path, key: str) -> None:
    """The manifest says legal, the source rows literary: refused, naming both (wave 5).

    Until wave 5 the manifest won silently, so every stage read profiles and
    names under a domain the graph was not extracted under.
    """
    data = tmp_path / "data"
    root = _pack(data, "split")
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    (root / "manifest.yaml").write_text(yaml.safe_dump({**manifest, key: "legal"}), "utf-8")
    _with_sources(root, ("literary", None))
    with pytest.raises(ReferencePackError, match=r"'legal'.*'literary'"):
        pack_domain(load_pack(data, "split"))
    with pytest.raises(ReferencePackError, match=f"manifest's {key}"):
        _ = load_pack(data, "split").domain
