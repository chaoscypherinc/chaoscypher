# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Migration 0026: tag names follow the tag rule, unique per database; sources gain a summary.

Every tag name is rewritten to one the rule accepts (lower-case; anything else
becomes ``-``); tags whose rewritten names meet in one database merge into the
oldest. ``(database_name, name)`` becomes unique, a source's tag assignments are
deleted with it (``ON DELETE CASCADE``), and ``sources.summary`` is added. When a
name would change, the migration holds for the owner's confirmation and its
preview lists each rename and merge.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations import tiers
from chaoscypher_core.database.migrations.runner import (
    current_revision,
    downgrade_to,
    head_revision,
    upgrade_to,
)
from chaoscypher_core.database.migrations.tiers import hold_preview
from chaoscypher_core.services.sources.tags import validate_tag

from .rows import insert_row


_UNIQUE = "uq_source_tags_database_name_name"


def _module() -> ModuleType:
    return tiers._migration_module("0026")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "app.db"
    sqlite3.connect(str(path)).close()
    upgrade_to(path, "0025")
    try:
        yield path
    finally:
        evict_engine(path)


def _tag(
    conn: sqlite3.Connection,
    tag_id: str,
    name: str,
    minute: int,
    database_name: str = "default",
    **extra: str,
) -> None:
    insert_row(
        conn,
        "source_tags",
        id=tag_id,
        database_name=database_name,
        name=name,
        created_at=f"2026-10-01 10:{minute:02d}:00.000000",
        **extra,
    )


def _assign(conn: sqlite3.Connection, assignment_id: str, source_id: str, tag_id: str) -> None:
    insert_row(
        conn,
        "source_tag_assignments",
        id=assignment_id,
        source_id=source_id,
        tag_id=tag_id,
        database_name="default",
    )


def _seed(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        for source_id in ("s1", "s2"):
            insert_row(conn, "sources", id=source_id, database_name="default")
        _tag(conn, "t-work", "Work Docs", 1, color="#ff0000")
        _tag(conn, "t-work2", "work docs", 2, color="#00ff00")
        _tag(conn, "t-q3", "Q3", 3)
        _tag(conn, "t-bang", "!!", 4)
        _tag(conn, "t-ok", "ok", 5)
        _assign(conn, "a1", "s1", "t-work")
        _assign(conn, "a2", "s1", "t-work2")
        _assign(conn, "a3", "s2", "t-work2")
        _assign(conn, "a4", "s1", "t-q3")
        _assign(conn, "a5", "s2", "t-bang")
        _assign(conn, "a6", "gone", "t-ok")
        _assign(conn, "a7", "s1", "t-missing")


_SEEDED_NAMES = {"Work Docs", "work docs", "Q3", "!!", "ok"}


def _names(db_path: Path, database_name: str = "default") -> dict[str, str]:
    """``name -> id`` of the tags in ``database_name``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name, id FROM source_tags WHERE database_name = ?", (database_name,)
        ).fetchall()
    return dict(rows)


@pytest.fixture
def seeded(db: Path) -> Path:
    _seed(db)
    return db


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Work Docs", "work-docs"),
        ("  Q3 ", "q3"),
        ("!!", "tag"),
        ("a//b", "a-b"),
        ("--x--", "x"),
        (":lead", "lead"),
        ("x" * 70, "x" * 64),
        ("naïve", "na-ve"),
        ("ok", "ok"),
        ("a:b_c-d", "a:b_c-d"),
        ("\u212aey", "ey"),  # Kelvin sign: never lower-cased into ASCII "k"
        ("Caf\u00c9", "caf"),
        ("a \u00ef b", "a-b"),  # a run with non-ASCII folds into one dash
    ],
)
def test_rewrite_tag_name(given: str, expected: str) -> None:
    rewritten = _module().rewrite_tag_name(given)
    assert rewritten == expected
    assert validate_tag(rewritten) == rewritten


def test_rewrite_never_turns_non_ascii_into_ascii() -> None:
    """``"\u212a".lower() == "k"``: lower-casing first would merge it into "key"."""
    assert "\u212aey".lower() == "key"
    assert _module().rewrite_tag_name("\u212aey") != "key"


def test_0026_does_not_import_the_live_tag_rule() -> None:
    """The migration records what it did: its rule is frozen in the module, not imported."""
    module = _module()
    assert module.TAG_PATTERN.pattern == "^[a-z0-9][a-z0-9:_-]{0,63}$"
    assert module.TAG_MAX_LENGTH == 64
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "services.sources.tags" not in source.split('"""', 2)[2]
    assert "chaoscypher_core.policy" not in source


def test_plan_tag_names_names_the_database_when_there_are_several() -> None:
    module = _module()
    rows = [
        module.TagRow("t1", "alpha", "A", "2026-10-01 10:00:00"),
        module.TagRow("t2", "beta", "b b", "2026-10-01 10:00:00"),
        module.TagRow("t3", "beta", "B-B", "2026-10-01 10:01:00"),
        module.TagRow("t4", "alpha", "ok", "2026-10-01 10:02:00"),
    ]
    plan = module.plan_tag_names(rows)
    assert plan.renames == {"t1": "a", "t2": "b-b"}
    assert plan.merges == {"t3": "t2"}
    assert plan.lines == [
        '"A" → "a" in database "alpha"',
        '"b b" → "b-b" in database "beta"',
        '"B-B" → "b-b" (merged) in database "beta"',
    ]


def test_0026_preview_lists_every_rename_and_merge(seeded: Path) -> None:
    assert hold_preview("0026", seeded) == [
        '"Q3" → "q3"',
        '"!!" → "tag"',
        '"Work Docs" → "work-docs"',
        '"work docs" → "work-docs" (merged)',
    ]


def test_0026_renames_merges_and_keeps_the_oldest_colour(seeded: Path) -> None:
    upgrade_to(seeded, "0026")

    adapter = SqliteAdapter(str(seeded), database_name="default")
    adapter.connect()
    try:
        tags = {t["name"]: t for t in adapter.list_tags("default")}
        assert set(tags) == {"ok", "q3", "tag", "work-docs"}
        assert tags["work-docs"]["id"] == "t-work"
        assert tags["work-docs"]["color"] == "#ff0000"
        assert {t["name"] for t in adapter.get_source_tags("s1")} == {"work-docs", "q3"}
        assert {t["name"] for t in adapter.get_source_tags("s2")} == {"work-docs", "tag"}
    finally:
        adapter.disconnect()

    with sqlite3.connect(str(seeded)) as conn:
        orphans = conn.execute(
            "SELECT id FROM source_tag_assignments WHERE source_id = 'gone'"
        ).fetchall()
        merged = conn.execute("SELECT id FROM source_tags WHERE id = 't-work2'").fetchall()
        tagless = conn.execute(
            "SELECT id FROM source_tag_assignments WHERE tag_id = 't-missing'"
        ).fetchall()
    assert orphans == []
    assert merged == []
    assert tagless == []


def test_0026_merges_a_valid_newer_name_into_the_older_one(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        insert_row(conn, "sources", id="s1", database_name="default")
        _tag(conn, "t-old", "Work Docs", 1, color="#ff0000")
        _tag(conn, "t-new", "work-docs", 2)
        _assign(conn, "a1", "s1", "t-new")
    assert hold_preview("0026", db) == [
        '"Work Docs" → "work-docs"',
        '"work-docs" → "work-docs" (merged)',
    ]

    upgrade_to(db, "0026")

    assert _names(db) == {"work-docs": "t-old"}
    assert _indexes(db, "source_tags")[_UNIQUE] == 1
    with sqlite3.connect(str(db)) as conn:
        tagged = conn.execute("SELECT source_id, tag_id FROM source_tag_assignments").fetchall()
    assert tagged == [("s1", "t-old")]


def test_0026_upgrades_each_database_on_its_own(seeded: Path) -> None:
    with sqlite3.connect(str(seeded)) as conn:
        _tag(conn, "t-other", "OK", 6, database_name="other")

    upgrade_to(seeded, "0026")

    assert _names(seeded, "other") == {"ok": "t-other"}
    assert _names(seeded)["ok"] == "t-ok"
    with sqlite3.connect(str(seeded)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_row(conn, "source_tags", id="t-dup", database_name="other", name="ok")


def test_0026_names_are_unique_per_database(seeded: Path) -> None:
    upgrade_to(seeded, "0026")
    with sqlite3.connect(str(seeded)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_row(conn, "source_tags", id="t-dup", database_name="default", name="ok")
        insert_row(conn, "source_tags", id="t-other", database_name="other", name="ok")


def test_0026_deleting_a_source_deletes_its_assignments(seeded: Path) -> None:
    upgrade_to(seeded, "0026")
    with sqlite3.connect(str(seeded)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM sources WHERE id = 's1'")
        left = conn.execute(
            "SELECT id FROM source_tag_assignments WHERE source_id = 's1'"
        ).fetchall()
    assert left == []


def _columns(db_path: Path, table: str) -> dict[str, tuple[str, int]]:
    """``name -> (declared type, notnull)`` from ``PRAGMA table_info``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (r[2].upper(), r[3]) for r in rows}


def _source_fk_on_delete(db_path: Path) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        fks = conn.execute("PRAGMA foreign_key_list(source_tag_assignments)").fetchall()
    (on_delete,) = [fk[6] for fk in fks if fk[2] == "sources" and fk[3] == "source_id"]
    return str(on_delete)


def _indexes(db_path: Path, table: str) -> dict[str, int]:
    """``index name -> unique`` from ``PRAGMA index_list``."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {r[1]: r[2] for r in rows}


def test_0026_adds_summary(db: Path) -> None:
    upgrade_to(db, "0026")
    assert _columns(db, "sources")["summary"] == ("TEXT", 0)


def test_0026_applies_on_its_own_when_nothing_needs_renaming(db: Path) -> None:
    from chaoscypher_core.database.migrations.startup import run_startup_migrations
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    with sqlite3.connect(str(db)) as conn:
        _tag(conn, "t-ok", "ok", 1)
        _tag(conn, "t-q3", "q3", 2)
    assert hold_preview("0026", db) == []

    run_startup_migrations(db)

    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db).ready is True


def test_0026_holds_at_startup_when_tags_need_renaming(seeded: Path) -> None:
    from chaoscypher_core.database.migrations.startup import run_startup_migrations
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    run_startup_migrations(seeded, auto_apply_destructive=True)

    assert current_revision(seeded) == "0025"
    assert get_upgrade_state(seeded).blocked_on == ["0026"]
    assert set(_names(seeded)) == _SEEDED_NAMES  # nothing changed while held


def test_0026_roundtrip(seeded: Path) -> None:
    upgrade_to(seeded, "0026")
    assert _source_fk_on_delete(seeded) == "CASCADE"
    assert _indexes(seeded, "source_tags")[_UNIQUE] == 1

    downgrade_to(seeded, "0025")
    assert "summary" not in _columns(seeded, "sources")
    assert _UNIQUE not in _indexes(seeded, "source_tags")
    assert _source_fk_on_delete(seeded) == "NO ACTION"
    with sqlite3.connect(str(seeded)) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM source_tags").fetchall()}
    assert names == {"ok", "q3", "tag", "work-docs"}
