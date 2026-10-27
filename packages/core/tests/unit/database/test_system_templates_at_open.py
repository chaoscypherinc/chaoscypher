# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every open of a database for writing gives it the shipped templates it lacks.

The Engine (the CLI, ``chaoscypher mcp``, the Python API, a package import) built a new
database through the migrations alone, so a database a client made that way had none of
``templates/default_templates.py``: an edge with the default template failed its foreign
key. A database at revision 0007 made like that is repaired on its first open.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import structlog

from chaoscypher_core import Engine, app_config
from chaoscypher_core.database.engine import init_database
from chaoscypher_core.database.migrations.runner import (
    current_revision,
    head_revision,
    upgrade_to,
)
from chaoscypher_core.database.migrations.startup import run_startup_migrations
from chaoscypher_core.database.seed import ensure_system_templates
from chaoscypher_core.models import EdgeCreate, NodeCreate
from chaoscypher_core.settings import GraphSettings
from chaoscypher_core.templates import default_templates
from chaoscypher_core.templates.default_templates import SHIPPED_TEMPLATE_IDS


SHIPPED = set(SHIPPED_TEMPLATE_IDS)
CLIENT_TEMPLATE = "template_note_by_a_client"


def _db(data_dir: Path, name: str = "notes") -> Path:
    db = data_dir / "databases" / name / "app.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def _template_ids(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {row[0] for row in conn.execute("SELECT id FROM graph_templates")}


def _add_client_template(db: Path) -> None:
    """A node template a client made, the only template such a database holds."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO graph_templates (id, database_name, name, template_type, is_system,"
            " properties, created_at, updated_at) VALUES (?, ?, 'Note', 'node', 0, '[]',"
            " '2026-10-15 00:00:00', '2026-10-15 00:00:00')",
            (CLIENT_TEMPLATE, db.parent.name),
        )


def _schema_without_templates(data_dir: Path) -> Path:
    """A database built the way an Engine built one before: the migrations and nothing else."""
    db = _db(data_dir)
    run_startup_migrations(db)
    _add_client_template(db)
    assert _template_ids(db) == {CLIENT_TEMPLATE}
    return db


def test_an_engine_gives_a_database_without_them_every_shipped_template(tmp_path: Path) -> None:
    db = _schema_without_templates(tmp_path)
    with Engine(data_dir=db.parent, initialize_db=True):
        pass
    assert _template_ids(db) == SHIPPED | {CLIENT_TEMPLATE}


def test_an_edge_with_the_default_template_saves_after_the_open(tmp_path: Path) -> None:
    db = _schema_without_templates(tmp_path)
    with Engine(data_dir=db.parent, initialize_db=True) as engine:
        graph = engine.graph_repository
        a = graph.create_node(NodeCreate(template_id=CLIENT_TEMPLATE, label="a"))
        b = graph.create_node(NodeCreate(template_id=CLIENT_TEMPLATE, label="b"))
        edge = graph.create_edge(
            EdgeCreate(
                source_node_id=a.id,
                target_node_id=b.id,
                template_id=GraphSettings().default_edge_template,
                label="about",
            )
        )
    assert edge.template_id == GraphSettings().default_edge_template


def test_a_database_at_0007_is_migrated_and_repaired_on_its_first_open(tmp_path: Path) -> None:
    db = _db(tmp_path)
    upgrade_to(db, "0007")
    _add_client_template(db)
    with Engine(data_dir=db.parent, initialize_db=True):
        pass
    assert current_revision(db) == head_revision()
    assert _template_ids(db) == SHIPPED | {CLIENT_TEMPLATE}


def test_init_database_gives_them_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Cortex and worker path (``init_database``) takes the same step."""
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_config, "_settings", None)
    app_config.get_settings.cache_clear()
    try:
        db = _schema_without_templates(tmp_path)
        init_database("notes", data_dir=str(tmp_path))
        assert _template_ids(db) == SHIPPED | {CLIENT_TEMPLATE}
    finally:
        app_config.get_settings.cache_clear()


def test_a_second_open_inserts_nothing(tmp_path: Path) -> None:
    db = _schema_without_templates(tmp_path)
    assert ensure_system_templates(db, "notes") == len(SHIPPED)
    assert ensure_system_templates(db, "notes") == 0


def test_only_the_missing_ones_are_inserted(tmp_path: Path) -> None:
    db = _schema_without_templates(tmp_path)
    ensure_system_templates(db, "notes")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM graph_templates WHERE id = 'system_template_link'")
        conn.execute("UPDATE graph_templates SET description = 'kept' WHERE id LIKE 'system_%'")
    assert ensure_system_templates(db, "notes") == 1
    with sqlite3.connect(db) as conn:
        kept = conn.execute(
            "SELECT count(*) FROM graph_templates WHERE description = 'kept'"
        ).fetchone()[0]
    assert kept == len(SHIPPED) - 1  # the rows already there were not rewritten


def test_a_complete_database_is_checked_without_taking_the_writer_lock(tmp_path: Path) -> None:
    """A database that has them all is only read: another process's write does not block it."""
    db = _schema_without_templates(tmp_path)
    ensure_system_templates(db, "notes")
    holder = sqlite3.connect(db, timeout=0.1)
    try:
        holder.execute("BEGIN IMMEDIATE")
        assert ensure_system_templates(db, "notes") == 0
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_a_failing_seed_raises_and_logs_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _schema_without_templates(tmp_path)

    def broken() -> list[dict[str, object]]:
        return [{"id": "system_template_broken", "name": "Broken", "properties": [{}]}]

    monkeypatch.setattr(default_templates, "get_all_default_templates", broken)
    with structlog.testing.capture_logs() as logs, pytest.raises(Exception):  # noqa: B017
        Engine(data_dir=db.parent, initialize_db=True)
    failed = [e for e in logs if e["event"] == "system_templates_seed_failed"]
    assert failed and failed[0]["log_level"] == "error"
    assert _template_ids(db) == {CLIENT_TEMPLATE}
