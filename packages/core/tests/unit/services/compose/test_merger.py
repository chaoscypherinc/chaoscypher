# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for services/compose/merger.py (NamespaceMerger, CCX 3.0).

The merger imports each resolved package into a real runtime database
through ``CcxImporter``. These tests build genuine ``.ccx`` packages with
``CcxExporter`` from seeded SQLite adapters, merge them into ``tmp_path``,
and read the composed database back to assert what landed. The post-merge
embedding pass is stubbed (no model download in unit tests).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.services.compose.merger import (
    COMPOSITION_MANIFEST_FILENAME,
    MergerError,
    NamespaceMerger,
)
from chaoscypher_core.services.compose.models import (
    MergeStrategy,
    PackageSpec,
    ResolvedPackage,
)
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_core.services.package import extract_archive


# ---------------------------------------------------------------------------
# Helpers: build real packages
# ---------------------------------------------------------------------------


# Every SQLite file a test opens, evicted (engine disposed, pooled
# connections closed) after the test so nothing leaks into later tests —
# the loader suite has a ResourceWarning detector that would otherwise blame
# whichever test happens to run when the garbage collector gets to them.
_OPENED_DBS: list[Path] = []


def _adapter(tmp_path: Path, name: str) -> SqliteAdapter:
    db_dir = tmp_path / "src" / name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    _OPENED_DBS.append(db_path)
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


def _seed(adapter: SqliteAdapter, *, suffix: str, people: tuple[str, ...]) -> None:
    """Seed one Person template + ``people`` nodes + a chain of edges."""
    assert adapter.session is not None
    session = adapter.session
    session.add(
        GraphTemplate(
            id=f"tpl_person_{suffix}",
            database_name="default",
            name="Person",
            template_type="node",
            color="#ff0000",
        )
    )
    session.add(
        GraphTemplate(
            id=f"tpl_rel_{suffix}",
            database_name="default",
            name="WorksWith",
            template_type="edge",
            color=None,
        )
    )
    session.flush()
    for person in people:
        session.add(
            GraphNode(
                id=f"node_{person.lower()}_{suffix}",
                database_name="default",
                graph_name="knowledge",
                template_id=f"tpl_person_{suffix}",
                label=person,
            )
        )
    session.flush()
    for left, right in itertools.pairwise(people):
        session.add(
            GraphEdge(
                id=f"edge_{left.lower()}_{right.lower()}_{suffix}",
                database_name="default",
                graph_name="knowledge",
                template_id=f"tpl_rel_{suffix}",
                source_node_id=f"node_{left.lower()}_{suffix}",
                target_node_id=f"node_{right.lower()}_{suffix}",
                label="worksWith",
                properties={"since": 2020},
            )
        )
    session.commit()


def _export(adapter: SqliteAdapter) -> bytes:
    assert adapter.session is not None
    settings = build_engine_settings(get_settings())
    exporter = CcxExporter(
        graph_repository=GraphRepository(adapter.session, "default"),
        sources_repository=adapter,
        settings=settings,
    )
    return exporter.export(include_embeddings=False)


def _package(tmp_path: Path, name: str, people: tuple[str, ...]) -> ResolvedPackage:
    """Build a real .ccx on disk and wrap it as an archive-backed ResolvedPackage."""
    adapter = _adapter(tmp_path, name)
    try:
        _seed(adapter, suffix=name, people=people)
        data = _export(adapter)
    finally:
        adapter.disconnect()
    archive = tmp_path / f"{name}.ccx"
    archive.write_bytes(data)
    return ResolvedPackage(
        spec=PackageSpec.parse(f"{name}:1.0.0"),
        name=name,
        version="1.0.0",
        path=archive,
        archive_path=archive,
    )


def _count(output_dir: Path) -> tuple[int, int, int]:
    """(nodes, edges, templates) in the composed database."""
    db_path = output_dir / "databases" / "default" / "app.db"
    _OPENED_DBS.append(db_path)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        assert adapter.session is not None
        repo = GraphRepository(adapter.session, "default")
        return (
            repo.count_nodes(),
            repo.count_edges(),
            repo.count_templates(database_name="default"),
        )
    finally:
        adapter.disconnect()


@pytest.fixture(autouse=True)
def _no_embedding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep the post-merge indexing pass off the network and off the GPU."""
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path / "data"))
    from chaoscypher_core import app_config

    app_config.get_config_manager.cache_clear()
    app_config.get_settings.cache_clear()
    app_config._settings = None
    with patch(
        "chaoscypher_core.operations.importing.imported_source_handler.index_imported_package",
        new=AsyncMock(return_value={"sources_indexed": 0, "nodes_indexed": 0}),
    ):
        yield
    app_config.get_config_manager.cache_clear()
    app_config.get_settings.cache_clear()
    app_config._settings = None
    # The merger opens the composed database through Engine(); the test
    # helpers open source and composed databases directly. Dispose them all.
    for db in [*_OPENED_DBS, tmp_path / "out" / "databases" / "default" / "app.db"]:
        evict_engine(db)
    _OPENED_DBS.clear()


# ---------------------------------------------------------------------------
# MergerError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMergerError:
    def test_carries_package_in_details(self) -> None:
        err = MergerError("boom", package="pkg-a")
        assert err.package == "pkg-a"
        assert err.details["package"] == "pkg-a"
        assert err.code == "MERGER_ERROR"

    def test_without_package(self) -> None:
        err = MergerError("boom")
        assert err.package is None


# ---------------------------------------------------------------------------
# merge()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMerge:
    @pytest.mark.asyncio
    async def test_two_packages_land_in_one_real_database(self, tmp_path: Path) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        beta = _package(tmp_path, "beta", ("Carol", "Dave", "Erin"))
        merger = NamespaceMerger(output_dir=tmp_path / "out")

        result = await merger.merge([alpha, beta])

        assert result.success is True, result.errors
        assert result.packages_included == ["alpha:1.0.0", "beta:1.0.0"]
        assert result.total_entities == 5
        assert result.total_relationships == 3
        nodes, edges, templates = _count(tmp_path / "out")
        assert (nodes, edges) == (5, 3)
        # Templates are unified by name across packages: one Person, one
        # WorksWith, even though each package shipped its own copy.
        assert templates == 2

        manifest = json.loads(
            (tmp_path / "out" / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        assert manifest["strategy"] == "namespace"
        assert manifest["total_entities"] == 5
        assert [p["name"] for p in manifest["packages"]] == ["alpha", "beta"]
        assert manifest["packages"][1]["entities"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("strategy", list(MergeStrategy))
    async def test_every_strategy_builds_the_same_database_today(
        self, tmp_path: Path, strategy: MergeStrategy
    ) -> None:
        """The importer keys templates by name and entities by IRI under every
        strategy; the setting is honoured (recorded) but not yet observable.
        """
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        beta = _package(tmp_path, "beta", ("Carol",))
        merger = NamespaceMerger(output_dir=tmp_path / "out", strategy=strategy)

        result = await merger.merge([alpha, beta])

        assert result.success is True, result.errors
        assert _count(tmp_path / "out") == (3, 1, 2)
        manifest = json.loads(
            (tmp_path / "out" / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        assert manifest["strategy"] == strategy.value

    @pytest.mark.asyncio
    async def test_same_package_twice_is_stored_once(self, tmp_path: Path) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        merger = NamespaceMerger(output_dir=tmp_path / "out", strategy=MergeStrategy.REPLACE)

        result = await merger.merge([alpha, alpha])

        assert result.success is True, result.errors
        # Upsert by stable IRI: the totals come from the database, not the
        # per-package counters, so the duplicate does not double-count.
        assert result.total_entities == 2
        assert result.total_relationships == 1

    @pytest.mark.asyncio
    async def test_corrupt_package_fails_the_build(self, tmp_path: Path) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice",))
        bad_archive = tmp_path / "bad.ccx"
        bad_archive.write_bytes(b"PK\x03\x04 this is not a package")
        bad = ResolvedPackage(
            spec=PackageSpec.parse("bad:1.0.0"),
            name="bad",
            version="1.0.0",
            path=bad_archive,
            archive_path=bad_archive,
        )

        result = await NamespaceMerger(output_dir=tmp_path / "out").merge([alpha, bad])

        assert result.success is False
        assert result.packages_included == ["alpha:1.0.0"]
        assert any("Failed to merge bad" in e for e in result.errors)
        assert not (tmp_path / "out" / COMPOSITION_MANIFEST_FILENAME).exists()
        # No half-built database left for `up` to serve.
        assert not (tmp_path / "out" / "databases" / "default").exists()

    @pytest.mark.asyncio
    async def test_clean_true_removes_only_build_artefacts(self, tmp_path: Path) -> None:
        """A clean rebuild replaces the database and manifest — and nothing
        else in the output directory: the resolver cache it was just filled
        from, the runtime settings, the pid record and the server log stay.
        """
        alpha = _package(tmp_path, "alpha", ("Alice",))
        out = tmp_path / "out"
        (out / "databases" / "default").mkdir(parents=True)
        (out / "databases" / "default" / "stale.marker").write_text("x", encoding="utf-8")
        (out / COMPOSITION_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
        (out / "cache").mkdir()
        (out / "cache" / "pkg.ccx").write_bytes(b"cached")
        (out / "settings.yaml").write_text("setup_completed: true\n", encoding="utf-8")

        result = await NamespaceMerger(output_dir=out).merge([alpha], clean=True)

        assert result.success is True, result.errors
        assert not (out / "databases" / "default" / "stale.marker").exists()
        assert (out / "cache" / "pkg.ccx").read_bytes() == b"cached"
        assert (out / "settings.yaml").exists()
        assert json.loads((out / COMPOSITION_MANIFEST_FILENAME).read_text())["total_entities"] == 1

    @pytest.mark.asyncio
    async def test_clean_false_keeps_existing_output(self, tmp_path: Path) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice",))
        keep = tmp_path / "out" / "keep.txt"
        keep.parent.mkdir(parents=True)
        keep.write_text("keep", encoding="utf-8")

        result = await NamespaceMerger(output_dir=tmp_path / "out").merge([alpha], clean=False)

        assert result.success is True, result.errors
        assert keep.exists()


# ---------------------------------------------------------------------------
# _package_bytes: archives and extracted directories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPackageBytes:
    def test_archive_backed_package_reads_the_archive(self, tmp_path: Path) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice",))
        assert NamespaceMerger._package_bytes(alpha) == alpha.archive_path.read_bytes()

    @pytest.mark.asyncio
    async def test_extracted_directory_is_packed_back_into_a_valid_package(
        self, tmp_path: Path
    ) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        extracted = tmp_path / "alpha-extracted"
        extract_archive(alpha.archive_path, extracted)
        as_dir = ResolvedPackage(
            spec=PackageSpec.parse(str(extracted)),
            name="alpha",
            version="1.0.0",
            path=extracted,
        )

        import ccx

        packed = NamespaceMerger._package_bytes(as_dir)
        assert ccx.open_package(packed).validate().ok

        result = await NamespaceMerger(output_dir=tmp_path / "out").merge([as_dir])
        assert result.success is True, result.errors
        assert result.total_entities == 2

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        ghost = ResolvedPackage(
            spec=PackageSpec.parse("ghost:1.0.0"),
            name="ghost",
            version="1.0.0",
            path=tmp_path / "nope",
        )
        with pytest.raises(MergerError) as exc_info:
            NamespaceMerger._package_bytes(ghost)
        assert exc_info.value.package == "ghost"
