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

import asyncio
import errno
import itertools
import json
import shutil
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
from chaoscypher_core.services.compose import merger as merger_module
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
from chaoscypher_core.templates.default_templates import SHIPPED_TEMPLATE_IDS


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
    """(nodes, edges, templates) in the composed database; the shipped templates not counted."""
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
            sum(
                t.id not in SHIPPED_TEMPLATE_IDS
                for t in repo.list_templates(include_disabled_sources=True)
            ),
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
        # WorksWith, even though each package shipped its own copy. The
        # database has the shipped templates too (``prepare_database``), and
        # the packages' Person is unified with the shipped one by name, so one
        # template of the packages' own remains.
        assert templates == 1

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
        assert _count(tmp_path / "out") == (3, 1, 1)  # Person is the shipped one
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
    async def test_failed_upsert_build_restores_the_existing_composition(
        self, tmp_path: Path
    ) -> None:
        """`compose build --no-clean` that hits one bad package keeps what was there.

        The upsert merges into the composition the user already has (and
        anything `compose mcp --mode write` added since); the failure path
        used to rmtree that whole database. It now puts the pre-build copy
        back, partial merges of this run included.
        """
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        first = await NamespaceMerger(output_dir=out).merge([alpha], clean=True)
        assert first.success is True, first.errors
        manifest_before = (out / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8")

        beta = _package(tmp_path, "beta", ("Carol",))
        bad_archive = tmp_path / "bad.ccx"
        bad_archive.write_bytes(b"PK\x03\x04 this is not a package")
        bad = ResolvedPackage(
            spec=PackageSpec.parse("bad:1.0.0"),
            name="bad",
            version="1.0.0",
            path=bad_archive,
            archive_path=bad_archive,
        )

        second = await NamespaceMerger(output_dir=out).merge([beta, bad], clean=False)

        # The restore swaps the database file under this process's cached
        # engine (the CLI exits here; `compose up` opens it fresh) — read it
        # through a fresh one, as the server would.
        evict_engine(out / "databases" / "default" / "app.db")
        assert second.success is False
        # Alice, Bob and their one edge, as the first build left them. One
        # template of the package's own: since the Engine seeds the shipped
        # templates into every database it opens (``prepare_database``), the
        # package's Person is unified with the shipped Person by name and only
        # WorksWith is the package's own — ``_count`` leaves the shipped ones out.
        assert _count(out) == (2, 1, 1)
        assert (out / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8") == manifest_before
        assert [p.name for p in out.iterdir() if p.name.endswith(".pre-build")] == []

    @pytest.mark.asyncio
    async def test_a_failed_snapshot_copy_leaves_the_composition_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `--no-clean` build that cannot copy the composition aside changes nothing.

        The copy ran inside the build's error handler with the snapshot
        still None, so a full disk (ENOSPC halfway through the copy) went
        down the failure path with nothing to restore: the user's
        composition was deleted and the half-written copy left behind.
        """
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")
        manifest_before = (out / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8")

        def _disk_full(src: Path, dst: Path, *args: object, **kwargs: object) -> None:
            Path(dst).mkdir(parents=True)
            (Path(dst) / "app.db").write_bytes(b"half a copy")
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(merger_module.shutil, "copytree", _disk_full)
        beta = _package(tmp_path, "beta", ("Carol",))

        result = await NamespaceMerger(output_dir=out).merge([beta], clean=False)

        assert result.success is False
        assert any("No space left on device" in e for e in result.errors), result.errors
        assert _count(out) == (2, 1, 1)
        assert (out / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8") == manifest_before
        assert [p.name for p in out.iterdir() if "pre-build" in p.name] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("interruption", [KeyboardInterrupt, asyncio.CancelledError])
    async def test_an_interrupted_upsert_build_restores_the_existing_composition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        interruption: type[BaseException],
    ) -> None:
        """Ctrl+C or a cancelled task mid-build puts the composition back, then re-raises.

        The failure handler caught ``Exception`` only, so an interruption
        skipped the restore: the partial database stayed live and the next
        `--no-clean` build dropped the good copy.
        """
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")
        manifest_before = (out / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8")

        real_merge_package = NamespaceMerger._merge_package
        calls = 0

        async def _interrupt_on_second(
            self: NamespaceMerger, engine: object, pkg: object
        ) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise interruption
            return await real_merge_package(self, engine, pkg)

        monkeypatch.setattr(NamespaceMerger, "_merge_package", _interrupt_on_second)
        beta = _package(tmp_path, "beta", ("Carol",))
        gamma = _package(tmp_path, "gamma", ("Dave",))

        with pytest.raises(interruption):
            await NamespaceMerger(output_dir=out).merge([beta, gamma], clean=False)

        evict_engine(out / "databases" / "default" / "app.db")
        assert _count(out) == (2, 1, 1)  # beta's Carol, merged before the interruption, is gone
        assert (out / COMPOSITION_MANIFEST_FILENAME).read_text(encoding="utf-8") == manifest_before
        assert [p.name for p in out.iterdir() if "pre-build" in p.name] == []

    @pytest.mark.asyncio
    async def test_an_interrupted_snapshot_copy_leaves_the_composition_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C while the composition is being copied aside re-raises and leaves no half copy."""
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")

        def _interrupted(src: Path, dst: Path, *args: object, **kwargs: object) -> None:
            Path(dst).mkdir(parents=True)
            (Path(dst) / "app.db").write_bytes(b"half a copy")
            raise KeyboardInterrupt

        monkeypatch.setattr(merger_module.shutil, "copytree", _interrupted)
        beta = _package(tmp_path, "beta", ("Carol",))

        with pytest.raises(KeyboardInterrupt):
            await NamespaceMerger(output_dir=out).merge([beta], clean=False)

        assert _count(out) == (2, 1, 1)
        assert [p.name for p in out.iterdir() if "pre-build" in p.name] == []

    @pytest.mark.asyncio
    async def test_a_snapshot_left_by_an_interrupted_build_is_restored_not_dropped(
        self, tmp_path: Path
    ) -> None:
        """A pre-build copy found at the start of a build is the last good composition.

        A build killed outright (SIGKILL, power loss) leaves the partial
        database live and the pre-build copy beside it. The next upsert
        build used to delete that copy first and snapshot the partial
        database instead, losing the good one for good. It now restores the
        copy, says so, and builds on it.
        """
        out = tmp_path / "out"
        good = tmp_path / "good"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        beta = _package(tmp_path, "beta", ("Carol",))
        assert (await NamespaceMerger(output_dir=good).merge([alpha], clean=True)).success
        assert (await NamespaceMerger(output_dir=out).merge([alpha, beta], clean=True)).success
        evict_engine(good / "databases" / "default" / "app.db")
        evict_engine(out / "databases" / "default" / "app.db")
        # What the killed build left: its partial database (Alice, Bob and
        # Carol) live, the good composition (Alice and Bob) copied aside.
        snapshot = out / ".default.pre-build"
        shutil.copytree(good / "databases" / "default", snapshot / "database")
        shutil.copy2(good / COMPOSITION_MANIFEST_FILENAME, snapshot / COMPOSITION_MANIFEST_FILENAME)

        gamma = _package(tmp_path, "gamma", ("Dave",))
        result = await NamespaceMerger(output_dir=out).merge([gamma], clean=False)

        assert result.success is True, result.errors
        evict_engine(out / "databases" / "default" / "app.db")
        assert _count(out)[0] == 3  # Alice, Bob, Dave: not the killed build's Carol
        assert any("interrupted" in w for w in result.warnings), result.warnings
        assert [p.name for p in out.iterdir() if "pre-build" in p.name] == []

    @pytest.mark.asyncio
    async def test_a_restore_killed_after_putting_the_database_back_is_finished(
        self, tmp_path: Path
    ) -> None:
        """A pre-build copy holding only the manifest: the database is already back.

        Finishing that restore must not delete the database it put back.
        """
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")
        snapshot = out / ".default.pre-build"
        snapshot.mkdir()
        (out / COMPOSITION_MANIFEST_FILENAME).rename(snapshot / COMPOSITION_MANIFEST_FILENAME)

        beta = _package(tmp_path, "beta", ("Carol",))
        result = await NamespaceMerger(output_dir=out).merge([beta], clean=False)

        assert result.success is True, result.errors
        evict_engine(out / "databases" / "default" / "app.db")
        assert _count(out)[0] == 3
        assert [p.name for p in out.iterdir() if "pre-build" in p.name] == []

    @pytest.mark.asyncio
    async def test_successful_upsert_build_leaves_no_snapshot(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice",))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success

        beta = _package(tmp_path, "beta", ("Carol",))
        result = await NamespaceMerger(output_dir=out).merge([beta], clean=False)

        assert result.success is True, result.errors
        assert _count(out)[0] == 2
        assert [p.name for p in out.iterdir() if p.name.endswith(".pre-build")] == []

    @pytest.mark.asyncio
    async def test_clean_false_keeps_existing_output(self, tmp_path: Path) -> None:
        alpha = _package(tmp_path, "alpha", ("Alice",))
        keep = tmp_path / "out" / "keep.txt"
        keep.parent.mkdir(parents=True)
        keep.write_text("keep", encoding="utf-8")

        result = await NamespaceMerger(output_dir=tmp_path / "out").merge([alpha], clean=False)

        assert result.success is True, result.errors
        assert keep.exists()

    @pytest.mark.asyncio
    async def test_rebuild_drops_a_package_removed_from_the_composition(
        self, tmp_path: Path
    ) -> None:
        """A clean rebuild stops serving a package you removed.

        The importer behind the merger is upsert-by-IRI with no delete
        path, so a `clean=False` rebuild adds the current package set on
        top of whatever the previous build left: the removed package's
        entities keep being served while `composition.json` reports it
        gone. `clean=True` — what `compose build` now passes by default —
        is the only thing that reconciles the two.
        """
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice",))
        beta = _package(tmp_path, "beta", ("Bob",))

        first = await NamespaceMerger(output_dir=out).merge([alpha, beta], clean=True)
        assert first.success is True, first.errors
        assert _count(out)[0] == 2
        # The merger opens the composed database through Engine(); a second
        # build in the same process must not reuse an engine bound to the
        # directory clean=True is about to remove.
        evict_engine(out / "databases" / "default" / "app.db")

        second = await NamespaceMerger(output_dir=out).merge([alpha], clean=True)

        assert second.success is True, second.errors
        assert second.packages_included == ["alpha:1.0.0"]
        # Beta's entity is gone, not merely unlisted in the manifest.
        assert _count(out)[0] == 1


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


@pytest.mark.asyncio
async def test_the_build_opens_the_composed_database_with_the_given_settings(
    tmp_path: Path,
) -> None:
    """Gap 9 (2026-10-20): ``compose build`` opened the database with ``Engine(data_dir=...)`` and
    plain defaults, so the configured embedding provider was ignored and every build embedded on
    the local CPU model; the runtime settings next to the database were never read either.
    """
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    merger = NamespaceMerger(output_dir=tmp_path / "out", settings=settings)
    with patch("chaoscypher_core.bootstrap.Engine") as engine_cls:
        engine_cls.return_value = MagicMock()
        await merger.merge([], clean=False)
    assert engine_cls.call_args.kwargs["settings"] is settings
