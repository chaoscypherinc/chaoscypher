# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for services/compose/merger.py (NamespaceMerger).

Exercises namespacing helpers, the inclusion strategy table, single-package
merge counting, the full ``merge()`` orchestration (success + malformed-JSON
failure + clean rmtree), and the auxiliary data-copy path.

Everything is driven off ``tmp_path`` JSON fixtures — no database is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chaoscypher_core.services.compose.merger import MergerError, NamespaceMerger
from chaoscypher_core.services.compose.models import (
    CompositionResult,
    MergeStrategy,
    PackageSpec,
    ResolvedPackage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_package(
    tmp_path: Path,
    *,
    name: str = "pkg",
    version: str = "1.0",
    entities: dict | None = None,
    relationships: list | None = None,
    sources: dict | None = None,
    write_data: bool = True,
    malformed: bool = False,
) -> ResolvedPackage:
    """Create a ResolvedPackage on disk with optional data/*.json files."""
    pkg_root = tmp_path / name
    pkg_root.mkdir(parents=True, exist_ok=True)

    if write_data:
        data_dir = pkg_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        if malformed:
            (data_dir / "entities.json").write_text("{ this is not valid json ")
        else:
            (data_dir / "entities.json").write_text(json.dumps(entities or {}))
            (data_dir / "relationships.json").write_text(json.dumps(relationships or []))
            (data_dir / "sources.json").write_text(json.dumps(sources or {}))

    return ResolvedPackage(
        spec=PackageSpec.parse(f"{name}:{version}"),
        name=name,
        version=version,
        path=pkg_root,
    )


def _merger(tmp_path: Path, strategy: MergeStrategy = MergeStrategy.NAMESPACE) -> NamespaceMerger:
    return NamespaceMerger(output_dir=tmp_path / "out", strategy=strategy)


# ---------------------------------------------------------------------------
# MergerError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMergerError:
    def test_carries_package_in_details(self) -> None:
        err = MergerError("boom", package="pkg-x")
        assert err.code == "MERGER_ERROR"
        assert err.package == "pkg-x"
        assert err.details.get("package") == "pkg-x"

    def test_without_package(self) -> None:
        err = MergerError("boom")
        assert err.package is None
        assert err.details == {}


# ---------------------------------------------------------------------------
# Namespacing helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNamespacingHelpers:
    def test_namespace_id_namespace_strategy(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.NAMESPACE)
        pkg = _make_package(tmp_path, name="my-pkg")
        # namespace replaces "-" with "_"
        assert merger._namespace_id("e1", pkg) == "my_pkg:e1"

    def test_namespace_id_non_namespace_strategy_passthrough(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.MERGE)
        pkg = _make_package(tmp_path, name="my-pkg")
        assert merger._namespace_id("e1", pkg) == "e1"

    def test_namespace_entity_adds_metadata_and_rewrites_refs(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="pkg")
        entity = {"label": "Alpha", "relationships": ["e2", "e3"]}
        out = merger._namespace_entity(entity, pkg)
        assert out["_source_package"] == "pkg"
        assert out["_source_version"] == "1.0"
        assert out["_namespace"] == "pkg"
        assert out["relationships"] == ["pkg:e2", "pkg:e3"]
        # original not mutated
        assert entity["relationships"] == ["e2", "e3"]

    def test_namespace_entity_passthrough_other_strategy(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.REPLACE)
        pkg = _make_package(tmp_path, name="pkg")
        entity = {"label": "Alpha"}
        out = merger._namespace_entity(entity, pkg)
        assert "_source_package" not in out

    def test_namespace_relationship_rewrites_source_target(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="pkg")
        rel = {"source": "a", "target": "b", "type": "rel"}
        out = merger._namespace_relationship(rel, pkg)
        assert out["source"] == "pkg:a"
        assert out["target"] == "pkg:b"
        assert out["_source_package"] == "pkg"
        assert out["_namespace"] == "pkg"

    def test_namespace_relationship_passthrough_other_strategy(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.MERGE)
        pkg = _make_package(tmp_path, name="pkg")
        rel = {"source": "a", "target": "b"}
        out = merger._namespace_relationship(rel, pkg)
        assert out["source"] == "a"
        assert "_source_package" not in out

    def test_namespace_source_adds_metadata(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="pkg")
        src = {"filename": "doc.pdf"}
        out = merger._namespace_source(src, pkg)
        assert out["_source_package"] == "pkg"
        assert out["_namespace"] == "pkg"

    def test_namespace_source_passthrough_other_strategy(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.MERGE)
        pkg = _make_package(tmp_path, name="pkg")
        out = merger._namespace_source({"filename": "doc.pdf"}, pkg)
        assert "_source_package" not in out


# ---------------------------------------------------------------------------
# _should_include
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShouldInclude:
    def test_not_present_always_included(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.MERGE)
        assert merger._should_include("e1", {}) is True

    def test_present_namespace_strategy_included(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.NAMESPACE)
        assert merger._should_include("e1", {"e1": {}}) is True

    def test_present_replace_strategy_included(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.REPLACE)
        assert merger._should_include("e1", {"e1": {}}) is True

    def test_present_merge_strategy_skipped(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path, MergeStrategy.MERGE)
        assert merger._should_include("e1", {"e1": {}}) is False


# ---------------------------------------------------------------------------
# _merge_package
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMergePackage:
    @pytest.mark.asyncio
    async def test_counts_entities_and_relationships(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(
            tmp_path,
            name="pkg",
            entities={"e1": {"label": "A"}, "e2": {"label": "B"}},
            relationships=[{"source": "e1", "target": "e2"}],
            sources={"s1": {"filename": "doc.pdf"}},
        )
        merged_entities: dict = {}
        merged_relationships: list = []
        merged_sources: dict = {}

        stats = await merger._merge_package(
            pkg, merged_entities, merged_relationships, merged_sources
        )
        assert stats["entities"] == 2
        assert stats["relationships"] == 1
        assert set(merged_entities.keys()) == {"pkg:e1", "pkg:e2"}
        assert merged_relationships[0]["source"] == "pkg:e1"
        assert "pkg:s1" in merged_sources

    @pytest.mark.asyncio
    async def test_no_data_dir_warns_and_returns_zero(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="empty", write_data=False)
        stats = await merger._merge_package(pkg, {}, [], {})
        assert stats == {"entities": 0, "relationships": 0}


# ---------------------------------------------------------------------------
# merge() orchestration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMerge:
    @pytest.mark.asyncio
    async def test_full_merge_writes_four_json_files(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(
            tmp_path,
            name="pkg",
            entities={"e1": {"label": "A"}},
            relationships=[{"source": "e1", "target": "e1"}],
            sources={"s1": {"filename": "doc.pdf"}},
        )
        result = await merger.merge([pkg])
        assert isinstance(result, CompositionResult)
        assert result.success is True
        assert result.total_entities == 1
        assert result.total_relationships == 1
        assert result.packages_included == ["pkg:1.0"]

        db_dir = merger.output_dir / "databases" / "default"
        for fname in ("entities.json", "relationships.json", "sources.json", "composition.json"):
            assert (db_dir / fname).exists(), f"missing {fname}"

        written = json.loads((db_dir / "entities.json").read_text())
        assert "pkg:e1" in written

    @pytest.mark.asyncio
    async def test_malformed_json_package_fails_without_db_write(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="bad", malformed=True)
        result = await merger.merge([pkg])
        assert result.success is False
        assert result.errors
        assert any("bad" in e for e in result.errors)
        # No entities.json written because errors short-circuit the DB write
        db_dir = merger.output_dir / "databases" / "default"
        assert not (db_dir / "entities.json").exists()

    @pytest.mark.asyncio
    async def test_clean_true_removes_existing_output(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        # Seed a stale file inside output_dir
        merger.output_dir.mkdir(parents=True, exist_ok=True)
        stale = merger.output_dir / "stale.txt"
        stale.write_text("old")

        pkg = _make_package(tmp_path, name="pkg", entities={"e1": {"label": "A"}})
        result = await merger.merge([pkg], clean=True)
        assert result.success is True
        # rmtree wiped the stale file before recreating output_dir
        assert not stale.exists()

    @pytest.mark.asyncio
    async def test_clean_false_keeps_existing_output(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        merger.output_dir.mkdir(parents=True, exist_ok=True)
        keep = merger.output_dir / "keep.txt"
        keep.write_text("preserved")

        pkg = _make_package(tmp_path, name="pkg", entities={"e1": {"label": "A"}})
        result = await merger.merge([pkg], clean=False)
        assert result.success is True
        assert keep.exists()


# ---------------------------------------------------------------------------
# _copy_package_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCopyPackageData:
    @pytest.mark.asyncio
    async def test_copies_graphs_and_embeddings(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="pkg")
        data_dir = pkg.path / "data"

        graphs_dir = data_dir / "graphs"
        graphs_dir.mkdir(parents=True, exist_ok=True)
        (graphs_dir / "g.ttl").write_text("@prefix x: <urn:x> .")
        (graphs_dir / "g.json").write_text("{}")

        emb_dir = data_dir / "embeddings"
        emb_dir.mkdir(parents=True, exist_ok=True)
        (emb_dir / "vectors.bin").write_bytes(b"\x00\x01")

        # output_dir must exist for the destination subdirs
        merger.output_dir.mkdir(parents=True, exist_ok=True)
        await merger._copy_package_data(pkg, data_dir)

        ns_graphs = merger.output_dir / "databases" / "default" / "graphs" / pkg.namespace
        ns_emb = merger.output_dir / "databases" / "default" / "embeddings" / pkg.namespace
        assert (ns_graphs / "g.ttl").exists()
        assert (ns_graphs / "g.json").exists()
        assert (ns_emb / "vectors.bin").exists()

    @pytest.mark.asyncio
    async def test_no_aux_dirs_is_noop(self, tmp_path: Path) -> None:
        merger = _merger(tmp_path)
        pkg = _make_package(tmp_path, name="pkg")
        merger.output_dir.mkdir(parents=True, exist_ok=True)
        # data dir exists but no graphs/embeddings subdirs → should not raise
        await merger._copy_package_data(pkg, pkg.path / "data")
        # nothing created under databases/default for this package
        assert not (merger.output_dir / "databases" / "default" / "graphs" / pkg.namespace).exists()
