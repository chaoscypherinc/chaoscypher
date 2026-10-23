# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The read-time guard for a database from before the core entity types (clean break, 2026-10-08).

A database from before the core types is not migrated in place. Opening one
logs one warning per database naming the fix (export, update, import) and
gives the UI a one-line notice; nothing is rewritten. The database here is
made the way an old one reaches the product: a v3-era graph written at
migration 0016 (a ``literary`` source, nodes typed ``Character``), then the
startup upgrade to head (0017 backfills every source's ``extraction_packs``,
which is why the predicate cannot read that list).
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
import structlog

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.database import core_types_guard
from chaoscypher_core.database.core_types_guard import (
    PRE_CORE_TYPES_MESSAGE,
    is_pre_core_types_database,
    pre_core_types_notice,
)
from chaoscypher_core.database.migrations.runner import upgrade_to, upgrade_to_head


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    """Insert a row, every NOT NULL column without a default given a filler."""
    row: dict[str, Any] = {}
    for _cid, name, col_type, notnull, default, _pk in conn.execute(f"PRAGMA table_info({table})"):
        if notnull and default is None:
            kind = str(col_type).upper()
            row[name] = 0 if ("INT" in kind or "BOOL" in kind or "FLOAT" in kind) else "x"
    row.update(values)
    for key, value in row.items():
        if isinstance(value, dict):
            row[key] = json.dumps(value)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def _seed(
    db: Path, nodes: list[tuple[str, str | None, str | None]], database_name: str = "default"
) -> None:
    """A literary source, one node template, and ``nodes`` (id, entity_type, source_id)."""
    conn = sqlite3.connect(str(db))
    try:
        _insert(
            conn,
            "sources",
            {"id": "src_wp", "database_name": database_name, "extraction_domain": "literary"},
        )
        _insert(
            conn,
            "graph_templates",
            {"id": "tpl_old", "database_name": database_name, "name": "Character"},
        )
        for node_id, entity_type, source_id in nodes:
            _insert(
                conn,
                "graph_nodes",
                {
                    "id": node_id,
                    "database_name": database_name,
                    "graph_name": "knowledge",
                    "template_id": "tpl_old",
                    "label": node_id,
                    "entity_type": entity_type,
                    "source_id": source_id,
                    "properties": {},
                },
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "app.db"
    sqlite3.connect(str(path)).close()
    yield path
    evict_engine(path)


@pytest.fixture(autouse=True)
def _fresh_warnings() -> Iterator[None]:
    core_types_guard._warned.clear()
    yield
    core_types_guard._warned.clear()


def test_a_database_from_before_the_core_types_is_one(db: Path) -> None:
    upgrade_to(db, "0016")
    _seed(db, [("n_pierre", "Character", "src_wp"), ("n_moscow", "Location", "src_wp")])
    upgrade_to_head(db)

    # 0017 gave the old source a pack list, so the list cannot tell old from new.
    conn = sqlite3.connect(str(db))
    try:
        (packs,) = conn.execute("SELECT extraction_packs FROM sources").fetchone()
    finally:
        conn.close()
    assert json.loads(packs) == ["literary"]

    assert is_pre_core_types_database(get_engine(db), "default") is True
    assert is_pre_core_types_database(get_engine(db), "another") is False


def test_a_database_on_the_core_types_is_not_one(db: Path) -> None:
    upgrade_to_head(db)
    _seed(
        db,
        [
            ("n_pierre", "Person", "src_wp"),
            ("n_glow", "Concept", "src_wp"),  # an unplaced type is kept as Concept
            ("n_by_hand", None, None),  # a node a user made: no type, no source
            ("n_lens", "Lens", None),  # a sourceless node typed by its template's name
            ("n_untyped", "", "src_wp"),
        ],
    )
    assert is_pre_core_types_database(get_engine(db), "default") is False
    assert pre_core_types_notice(get_engine(db), "default") is None


def test_a_database_without_the_schema_is_not_one(db: Path) -> None:
    assert is_pre_core_types_database(get_engine(db), "default") is False


def test_the_notice_is_the_one_message_and_is_logged_once_per_database(db: Path) -> None:
    upgrade_to(db, "0016")
    _seed(db, [("n_pierre", "Character", "src_wp")])
    upgrade_to_head(db)
    before = sqlite3.connect(str(db))
    try:
        rows_before = before.execute("SELECT * FROM graph_nodes ORDER BY id").fetchall()
    finally:
        before.close()

    with structlog.testing.capture_logs() as logs:
        first = pre_core_types_notice(get_engine(db), "default")
        second = pre_core_types_notice(get_engine(db), "default")
    assert first == second == PRE_CORE_TYPES_MESSAGE
    for step in ("export", "update", "import"):
        assert step in PRE_CORE_TYPES_MESSAGE
    warnings = [e for e in logs if e["event"] == "database_predates_core_types"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["message"] == PRE_CORE_TYPES_MESSAGE

    # Read-time only: nothing was rewritten.
    after = sqlite3.connect(str(db))
    try:
        assert after.execute("SELECT * FROM graph_nodes ORDER BY id").fetchall() == rows_before
    finally:
        after.close()


def test_opening_a_database_from_before_the_core_types_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``init_database`` (the product's open) upgrades it, rewrites no node, and warns."""
    from chaoscypher_core.database import engine as database_engine
    from chaoscypher_core.database.engine import get_db_path, init_database

    # The default seeds read the settings' own data directory; not this file's.
    monkeypatch.setattr(database_engine, "seed_default_data", lambda _name: None)

    path = get_db_path("old", data_dir=str(tmp_path))
    path.parent.mkdir(parents=True)
    sqlite3.connect(str(path)).close()
    try:
        upgrade_to(path, "0016")
        _seed(path, [("n_pierre", "Character", "src_wp")], database_name="old")
        with structlog.testing.capture_logs() as logs:
            init_database("old", data_dir=str(tmp_path))
        conn = sqlite3.connect(str(path))
        try:
            (entity_type,) = conn.execute("SELECT entity_type FROM graph_nodes").fetchone()
        finally:
            conn.close()
    finally:
        evict_engine(path)
    assert entity_type == "Character"
    warnings = [e for e in logs if e["event"] == "database_predates_core_types"]
    assert [w["database_name"] for w in warnings] == ["old"]
