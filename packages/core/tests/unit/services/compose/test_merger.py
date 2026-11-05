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
import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.database.migrations.state import get_upgrade_state
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
from chaoscypher_core.services.package import extract_archive
from chaoscypher_core.templates.default_templates import SHIPPED_TEMPLATE_IDS
from chaoscypher_core.testing.ccx_packages import build_ccx_package


# ---------------------------------------------------------------------------
# Helpers: build real packages
# ---------------------------------------------------------------------------


# Every SQLite file a test opens, evicted (engine disposed, pooled
# connections closed) after the test so nothing leaks into later tests —
# the loader suite has a ResourceWarning detector that would otherwise blame
# whichever test happens to run when the garbage collector gets to them.
_OPENED_DBS: list[Path] = []


def _package(tmp_path: Path, name: str, people: tuple[str, ...]) -> ResolvedPackage:
    """Build a real .ccx on disk and wrap it as an archive-backed ResolvedPackage."""
    archive = build_ccx_package(tmp_path, name, people)
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
    async def test_clean_build_after_the_upgrade_gate_opened_the_same_database(
        self, tmp_path: Path
    ) -> None:
        """A clean rebuild works after the CLI's upgrade gate opened the composed database.

        With ``CHAOSCYPHER_DATA_DIR`` equal to the output folder, the CLI's
        pre-command gate opens ``databases/default/app.db`` through the engine
        cache; the clean build then deletes that folder. The schema used to be
        built through the stale cached engine, into the deleted file, and the
        import failed with ``no such table: sources``.
        """
        out = tmp_path / "out"
        merger = NamespaceMerger(output_dir=out, strategy=MergeStrategy.NAMESPACE)
        first = await merger.merge([_package(tmp_path, "pack-a", ("Ada",))], clean=True)
        assert first.success, first.errors
        db_path = merger.database_dir / "app.db"
        # `compose build` runs in a new process: nothing of the first build is cached
        evict_engine(db_path)
        # the CLI's pre-command gate opens the composed database through the engine cache
        get_upgrade_state(db_path)

        result = await merger.merge([_package(tmp_path, "pack-b", ("Grace",))], clean=True)

        assert result.success, result.errors
        assert result.total_entities == 1  # Grace only: a clean build keeps nothing of Ada
        # the file on disk holds the build, not only the engine that built it
        with closing(sqlite3.connect(db_path)) as conn:
            assert conn.execute("SELECT count(*) FROM graph_nodes").fetchone()[0] > 0
        assert _count(out)[0] > 0

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
# A cleanup failure after a failed build never hides the build's own error
# ---------------------------------------------------------------------------


async def _write_to_read_only_database(
    self: NamespaceMerger, engine: object, pkg: object
) -> object:
    """A real database failure in the import step: SQLite refuses a write to a read-only file.

    The file is opened read-only (rather than chmod'ed, which root ignores), and
    the write SQLite rejects is the one a read-only composition folder produces.
    """
    with closing(sqlite3.connect(f"file:{self.database_file}?mode=ro", uri=True)) as conn:
        conn.execute("CREATE TABLE pack_a_rows (id INTEGER PRIMARY KEY)")
    raise AssertionError("the write to a read-only database should have failed")


def _removal_denied(monkeypatch: pytest.MonkeyPatch, denied: Path) -> None:
    """Make removing ``denied`` raise PermissionError, as a read-only folder does."""
    real_rmtree = shutil.rmtree

    def _rmtree(path: Path | str, *args: Any, **kwargs: Any) -> None:
        if Path(path) == denied:
            raise PermissionError(errno.EACCES, "Permission denied", str(denied))
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(merger_module.shutil, "rmtree", _rmtree)


@pytest.mark.unit
class TestCleanupFailureAfterAFailedBuild:
    """`compose build --no-clean` into a read-only folder: the real cause stays first.

    The import failed with a plain `attempt to write a readonly database`, then
    removing the half-built database raised PermissionError out of `merge()`, and
    the caller's last line became `PermissionError: [Errno 13] Permission denied`.
    """

    @pytest.mark.asyncio
    async def test_clean_build_reports_the_failure_then_the_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        merger = NamespaceMerger(output_dir=out)
        monkeypatch.setattr(NamespaceMerger, "_merge_package", _write_to_read_only_database)
        _removal_denied(monkeypatch, merger.database_dir)

        result = await merger.merge([_package(tmp_path, "pack-a", ("Alice",))], clean=True)

        assert result.success is False
        assert result.errors[0] == (
            f"Failed to merge pack-a: the database at {merger.database_file} is read-only "
            "while importing package pack-a"
        )
        assert result.errors[1:] == [
            f"could not remove the half-built database at {merger.database_dir}: permission denied"
        ]
        evict_engine(merger.database_file)

    @pytest.mark.asyncio
    async def test_upsert_build_keeps_the_copy_and_says_the_next_build_restores_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")

        merger = NamespaceMerger(output_dir=out)
        monkeypatch.setattr(NamespaceMerger, "_merge_package", _write_to_read_only_database)
        _removal_denied(monkeypatch, merger.database_dir)

        result = await merger.merge([_package(tmp_path, "pack-a", ("Carol",))], clean=False)

        assert result.success is False
        assert result.errors[0].startswith("Failed to merge pack-a: the database at ")
        assert "is read-only while importing package pack-a" in result.errors[0]
        snapshot = out / ".default.pre-build"
        assert result.errors[1:] == [
            f"could not put back the composition from before the build "
            f"({merger.database_dir}: permission denied); its copy is kept at {snapshot} "
            "and the next build restores it"
        ]
        assert (snapshot / "database" / "app.db").exists()
        evict_engine(merger.database_file)

    @pytest.mark.asyncio
    async def test_an_interruption_is_not_replaced_by_the_cleanup_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        merger = NamespaceMerger(output_dir=out)

        async def _interrupted(self: NamespaceMerger, engine: object, pkg: object) -> object:
            raise KeyboardInterrupt

        monkeypatch.setattr(NamespaceMerger, "_merge_package", _interrupted)
        _removal_denied(monkeypatch, merger.database_dir)

        with pytest.raises(KeyboardInterrupt):
            await merger.merge([_package(tmp_path, "pack-a", ("Alice",))], clean=True)
        evict_engine(merger.database_file)


def _real_missing_table_error(tmp_path: Path) -> Exception:
    """A real SQLAlchemy OperationalError (``no such table``) from an empty file."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT id FROM sources"))
    except OperationalError as exc:
        return exc
    finally:
        engine.dispose()
    raise AssertionError("the query against an empty database should have failed")


@pytest.mark.unit
class TestWholeBuildFailure:
    """The `Merge failed:` branch: a failure outside one package's import."""

    async def _merge_with_a_failed_package_then_a_failed_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, merger: NamespaceMerger
    ) -> Any:
        async def _bad_manifest(self: NamespaceMerger, engine: object, pkg: object) -> object:
            raise ValueError("manifest is missing")

        error = _real_missing_table_error(tmp_path)

        def _count_fails(*_args: object, **_kwargs: object) -> int:
            raise error

        monkeypatch.setattr(NamespaceMerger, "_merge_package", _bad_manifest)
        monkeypatch.setattr(GraphRepository, "count_nodes", _count_fails)
        return await merger.merge([_package(tmp_path, "pack-a", ("Alice",))], clean=True)

    @pytest.mark.asyncio
    async def test_keeps_the_package_errors_and_names_the_database_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        merger = NamespaceMerger(output_dir=tmp_path / "out")

        result = await self._merge_with_a_failed_package_then_a_failed_count(
            tmp_path, monkeypatch, merger
        )

        assert result.success is False
        assert result.errors == [
            "Failed to merge pack-a: manifest is missing",
            f'Merge failed: the database at {merger.database_file} has no "sources" table '
            "while building the composed database "
            "(it may not be set up, or was replaced while open)",
        ]
        assert not merger.database_dir.exists()
        evict_engine(merger.database_file)

    @pytest.mark.asyncio
    async def test_a_cleanup_failure_on_that_path_comes_after_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        merger = NamespaceMerger(output_dir=tmp_path / "out")
        _removal_denied(monkeypatch, merger.database_dir)

        result = await self._merge_with_a_failed_package_then_a_failed_count(
            tmp_path, monkeypatch, merger
        )

        assert result.success is False
        assert result.errors[0] == "Failed to merge pack-a: manifest is missing"
        assert result.errors[1].startswith("Merge failed: the database at ")
        assert result.errors[2:] == [
            f"could not remove the half-built database at {merger.database_dir}: permission denied"
        ]
        evict_engine(merger.database_file)


@pytest.mark.unit
class TestCleanupMessagesSayWhatFailed:
    """Each cleanup message names what actually failed, not a neighbouring step."""

    @pytest.mark.asyncio
    async def test_a_clean_build_that_cannot_remove_the_previous_composition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing was built yet: the old composition, not a half-built one, is in the way."""
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice",))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")
        merger = NamespaceMerger(output_dir=out)
        _removal_denied(monkeypatch, merger.database_dir)

        result = await merger.merge([alpha], clean=True)

        assert result.success is False
        assert result.errors == [
            f"could not remove the previous composition at {merger.database_dir} "
            "before building: permission denied"
        ]
        assert "half-built" not in " ".join(result.errors)
        assert (out / COMPOSITION_MANIFEST_FILENAME).exists()

    async def _failed_upsert_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, deny: str
    ) -> tuple[Any, Path]:
        out = tmp_path / "out"
        alpha = _package(tmp_path, "alpha", ("Alice", "Bob"))
        assert (await NamespaceMerger(output_dir=out).merge([alpha], clean=True)).success
        evict_engine(out / "databases" / "default" / "app.db")
        snapshot = out / ".default.pre-build"

        async def _bad_manifest(self: NamespaceMerger, engine: object, pkg: object) -> object:
            raise ValueError("manifest is missing")

        monkeypatch.setattr(NamespaceMerger, "_merge_package", _bad_manifest)
        if deny == "manifest":
            real_move = shutil.move
            denied = snapshot / COMPOSITION_MANIFEST_FILENAME

            def _move(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
                if Path(src) == denied:
                    raise PermissionError(errno.EACCES, "Permission denied", str(denied))
                return real_move(src, dst, *args, **kwargs)

            monkeypatch.setattr(merger_module.shutil, "move", _move)
        else:
            _removal_denied(monkeypatch, snapshot)

        result = await NamespaceMerger(output_dir=out).merge([alpha], clean=False)
        evict_engine(out / "databases" / "default" / "app.db")
        return result, snapshot

    @pytest.mark.asyncio
    async def test_the_manifest_could_not_be_put_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, snapshot = await self._failed_upsert_build(tmp_path, monkeypatch, deny="manifest")

        assert result.success is False
        assert result.errors[0] == "Failed to merge alpha: manifest is missing"
        manifest = snapshot / COMPOSITION_MANIFEST_FILENAME
        assert result.errors[1:] == [
            "the database from before the build was put back, but not its manifest "
            f"({manifest}: permission denied); it is kept at {snapshot} and the next "
            "build puts it back"
        ]
        assert manifest.exists()

    @pytest.mark.asyncio
    async def test_the_copy_could_not_be_removed_after_putting_everything_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, snapshot = await self._failed_upsert_build(tmp_path, monkeypatch, deny="snapshot")

        assert result.success is False
        assert result.errors[1:] == [
            "the composition from before the build was put back, but its copy at "
            f"{snapshot} could not be removed ({snapshot}: permission denied)"
        ]
        assert _count(tmp_path / "out") == (2, 1, 1)


@pytest.mark.asyncio
async def test_a_database_failure_while_indexing_warns_in_plain_words(tmp_path: Path) -> None:
    """Compose build prints its warnings; the indexing one must not carry SQL either."""
    error = _real_missing_table_error(tmp_path)
    merger = NamespaceMerger(output_dir=tmp_path / "out")

    with patch(
        "chaoscypher_core.operations.importing.imported_source_handler.index_imported_package",
        new=AsyncMock(side_effect=error),
    ):
        result = await merger.merge([_package(tmp_path, "pack-a", ("Alice",))], clean=True)

    assert result.success is True, result.errors
    skipped = [w for w in result.warnings if w.startswith("search indexing skipped:")]
    assert skipped == [
        f'search indexing skipped: the database at {merger.database_file} has no "sources" '
        "table while indexing the composed database for search "
        "(it may not be set up, or was replaced while open)"
    ]
    evict_engine(merger.database_file)


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
