# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A migration may hold itself for confirmation when its preview lists data changes.

A migration module may define ``cc_hold_preview(conn) -> list[str]``: one plain line per
change it would make to user-visible data. A non-empty preview blocks that migration (and
everything after it) whatever ``auto_apply_destructive`` says; an empty one applies as usual.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Connection

from chaoscypher_core.database.migrations import tiers
from chaoscypher_core.database.migrations.runner import (
    current_revision,
    head_revision,
    pending_revisions,
    upgrade_to,
)
from chaoscypher_core.database.migrations.startup import _plan_apply
from chaoscypher_core.database.migrations.tiers import (
    MigrationInfo,
    MigrationTier,
    hold_preview,
    read_migration_info,
)
from chaoscypher_core.database.migrations.upgrade import UpgradeService


_LINES = ['"A" → "a"']

Preview = Callable[[Connection], list[str]]


def _infos() -> list[MigrationInfo]:
    return [
        MigrationInfo("a", MigrationTier.SAFE_AUTO, ""),
        MigrationInfo("b", MigrationTier.NEEDS_CONFIRMATION, "", has_hold=True),
        MigrationInfo("c", MigrationTier.SAFE_AUTO, ""),
    ]


@pytest.mark.parametrize("auto_apply", [True, False])
def test_plan_apply_holds_a_revision_whatever_auto_apply_says(auto_apply: bool) -> None:
    held = frozenset({"b"})
    assert _plan_apply(_infos(), auto_apply_destructive=auto_apply, held=held) == (
        ["a"],
        ["b", "c"],
    )


def test_plan_apply_without_holds_is_unchanged() -> None:
    infos = [
        MigrationInfo("a", MigrationTier.SAFE_AUTO, ""),
        MigrationInfo("b", MigrationTier.MANUAL, ""),
        MigrationInfo("c", MigrationTier.SAFE_AUTO, ""),
    ]
    assert _plan_apply(infos, auto_apply_destructive=True) == (["a", "b", "c"], [])
    assert _plan_apply(infos, auto_apply_destructive=False) == (["a"], ["b", "c"])
    # a has_hold module whose preview came back empty is not in ``held``: it applies
    assert _plan_apply(_infos(), auto_apply_destructive=False) == (["a", "b", "c"], [])


def _db_at_0024(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    sqlite3.connect(str(db)).close()
    upgrade_to(db, "0024")
    return db


def _backups(db: Path) -> list[Path]:
    folder = db.parent / "backups"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_a_migration_without_a_preview_has_no_hold(tmp_path: Path) -> None:
    db = _db_at_0024(tmp_path)
    assert read_migration_info("0025").has_hold is False
    assert hold_preview("0025", db) == []


def _hold_0025(monkeypatch: pytest.MonkeyPatch, preview: Preview) -> None:
    """Give migration 0025 a ``cc_hold_preview``: the real module's metadata plus ``preview``.

    Patches where the module is loaded, so ``read_migration_info`` and ``hold_preview``
    run for real on every surface (startup, ``pending()``, MCP).
    """
    real = tiers._migration_module

    def _module(revision: str) -> object:
        module = real(revision)
        if revision != "0025":
            return module
        return SimpleNamespace(
            CC_TIER=module.CC_TIER, CC_DESCRIPTION=module.CC_DESCRIPTION, cc_hold_preview=preview
        )

    monkeypatch.setattr(tiers, "_migration_module", _module)


def _lines(lines: list[str]) -> Preview:
    return lambda _conn: list(lines)


def _broken(_conn: Connection) -> list[str]:
    raise sqlite3.OperationalError("no such table: missing")


def test_read_migration_info_sees_a_real_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    _hold_0025(monkeypatch, _lines(_LINES))
    assert read_migration_info("0025").has_hold is True

    monkeypatch.setattr(
        tiers,
        "_migration_module",
        lambda _rev: SimpleNamespace(CC_TIER="safe_auto", cc_hold_preview="not callable"),
    )
    assert read_migration_info("0025").has_hold is False


def test_hold_preview_runs_the_module_preview_on_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ``hold_preview`` hands the preview a connection to the database as it stands."""
    from tests.unit.database.migrations.rows import insert_row

    db = _db_at_0024(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        insert_row(conn, "sources", id="doc-a", database_name="default")
        insert_row(conn, "sources", id="doc-b", database_name="default")

    def _count_sources(conn: Connection) -> list[str]:
        rows = conn.exec_driver_sql("SELECT id FROM sources ORDER BY id").all()
        return [f"{row[0]} changes" for row in rows]

    _hold_0025(monkeypatch, _count_sources)

    assert hold_preview("0025", db) == ["doc-a changes", "doc-b changes"]


def test_a_preview_never_runs_on_a_database_with_no_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new database is built, not upgraded: it has no data, so nothing to preview or hold."""
    calls: list[Connection] = []

    def _recording(conn: Connection) -> list[str]:
        calls.append(conn)
        return _LINES

    _hold_0025(monkeypatch, _recording)
    db = tmp_path / "new.db"
    sqlite3.connect(str(db)).close()

    assert hold_preview("0025", db) == []
    assert calls == []


def test_startup_blocks_on_a_held_migration_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.database.migrations import startup
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    db = _db_at_0024(tmp_path)
    _hold_0025(monkeypatch, _lines(_LINES))

    startup.run_startup_migrations(db, auto_apply_destructive=True)

    state = get_upgrade_state(db)
    assert state.ready is False
    # 0025 and everything after it wait (a later migration cannot skip it)
    assert state.blocked_on[0] == "0025"
    assert state.blocked_on == pending_revisions(db)
    assert state.message.startswith("Migration 0025 needs your confirmation")
    assert "Review the 1 change(s) it lists" in state.message
    assert current_revision(db) == "0024"
    # nothing was applied, so nothing was backed up: the explicit apply takes its own
    assert state.last_backup is None
    assert _backups(db) == []


def test_starting_twice_on_a_held_database_takes_no_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every boot of a held database would otherwise add a full copy that is never pruned."""
    from chaoscypher_core.database.migrations import startup

    db = _db_at_0024(tmp_path)
    _hold_0025(monkeypatch, _lines(_LINES))

    startup.run_startup_migrations(db, auto_apply_destructive=True)
    startup.run_startup_migrations(db, auto_apply_destructive=True)

    assert _backups(db) == []


def test_startup_applies_a_migration_whose_preview_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.database.migrations import startup
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    db = _db_at_0024(tmp_path)
    _hold_0025(monkeypatch, _lines([]))

    startup.run_startup_migrations(db, auto_apply_destructive=True)

    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db).ready is True


def test_startup_gates_when_a_preview_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview that raises blocks with its own plain message; it never crashes the boot."""
    from chaoscypher_core.database.migrations import startup
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    db = _db_at_0024(tmp_path)
    _hold_0025(monkeypatch, _broken)

    startup.run_startup_migrations(db, auto_apply_destructive=True)

    state = get_upgrade_state(db)
    assert state.ready is False
    assert state.blocked_on == pending_revisions(db)
    assert state.message.startswith("Migration 0025 could not list its changes: ")
    assert '"missing" table' in state.message
    assert state.message.endswith("; nothing was applied.")
    assert "roll back" not in state.message.lower()
    assert state.last_backup is None
    assert current_revision(db) == "0024"
    assert _backups(db) == []


def _service_for(db: Path, monkeypatch: pytest.MonkeyPatch) -> UpgradeService:
    from chaoscypher_core.database.migrations import upgrade as upgrade_mod

    monkeypatch.setattr(upgrade_mod, "get_db_path", lambda _name: db)
    return upgrade_mod.UpgradeService("test")


def test_pending_lists_the_hold_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db_at_0024(tmp_path)
    _hold_0025(monkeypatch, _lines(_LINES))

    resp = _service_for(db, monkeypatch).pending()

    assert resp.blocked_on[0].revision == "0025"
    assert resp.blocked_on[0].hold == _LINES
    assert resp.first_build is False


def test_pending_reports_a_failing_preview_as_one_hold_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page, the CLI and MCP still render, and still ask, when a preview fails."""
    db = _db_at_0024(tmp_path)
    _hold_0025(monkeypatch, _broken)

    resp = _service_for(db, monkeypatch).pending()

    (line,) = resp.blocked_on[0].hold
    assert line.startswith("Could not list the changes: ")
    assert '"missing" table' in line


def test_pending_on_a_new_database_is_a_first_build_with_no_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Applying to a database with no schema builds it: nothing to preview or confirm."""
    _hold_0025(monkeypatch, _broken)
    db = tmp_path / "new.db"
    sqlite3.connect(str(db)).close()

    resp = _service_for(db, monkeypatch).pending()

    assert resp.first_build is True
    assert all(m.hold == [] for m in resp.blocked_on)
