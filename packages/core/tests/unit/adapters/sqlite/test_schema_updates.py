# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for apply_schema_updates() auto-migration.

apply_schema_updates() is load-bearing: CLAUDE.md promises that adding a
new field to a SQLModel will "just work" on the next startup. These tests
pin that behavior.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text

from chaoscypher_core.adapters.sqlite.engine import apply_schema_updates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_engine(tmp_path: Any) -> Any:
    """Return a file-backed SQLite engine that each test owns exclusively."""
    return create_engine(f"sqlite:///{tmp_path / 'test.db'}")


def _create_legacy_table(engine: Any, table: str, columns: list[tuple[str, str]]) -> None:
    """Create a table with *columns* only, simulating a pre-migration schema.

    Each column tuple is (name, SQL type).
    """
    col_defs = ", ".join(f"{name} {sql_type}" for name, sql_type in columns)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {table} ({col_defs})"))


def _inspect_columns(engine: Any, table: str) -> dict[str, dict[str, Any]]:
    """Return {column_name: column_info} for *table*."""
    inspector = inspect(engine)
    return {c["name"]: c for c in inspector.get_columns(table)}


# ---------------------------------------------------------------------------
# apply_schema_updates — core behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplySchemaUpdatesOnRealTables:
    """Exercise the real SQLModel metadata path against a fresh DB."""

    def test_runs_cleanly_on_empty_database(self, tmp_path) -> None:
        """An empty database has no tables to update — function must not raise."""
        engine = _fresh_engine(tmp_path)

        apply_schema_updates(engine)

        # No assertions beyond "doesn't raise". The inspector should still be usable.
        assert inspect(engine).get_table_names() == []

    def test_runs_cleanly_after_full_create_all(self, tmp_path) -> None:
        """On a fully-initialized DB there are no missing columns — function is a no-op."""
        from sqlmodel import SQLModel

        from chaoscypher_core.adapters.sqlite import models  # noqa: F401 — register tables

        engine = _fresh_engine(tmp_path)
        SQLModel.metadata.create_all(engine)

        # Snapshot column counts before
        inspector_before = inspect(engine)
        before_counts = {
            t: len(inspector_before.get_columns(t)) for t in inspector_before.get_table_names()
        }

        apply_schema_updates(engine)

        # Same column counts after — nothing was added
        inspector_after = inspect(engine)
        after_counts = {
            t: len(inspector_after.get_columns(t)) for t in inspector_after.get_table_names()
        }
        assert before_counts == after_counts

    def test_is_idempotent(self, tmp_path) -> None:
        """Running apply_schema_updates twice must produce the same schema."""
        from sqlmodel import SQLModel

        from chaoscypher_core.adapters.sqlite import models  # noqa: F401

        engine = _fresh_engine(tmp_path)
        SQLModel.metadata.create_all(engine)

        apply_schema_updates(engine)
        snapshot_1 = {
            t: sorted(c["name"] for c in inspect(engine).get_columns(t))
            for t in inspect(engine).get_table_names()
        }

        apply_schema_updates(engine)
        snapshot_2 = {
            t: sorted(c["name"] for c in inspect(engine).get_columns(t))
            for t in inspect(engine).get_table_names()
        }

        assert snapshot_1 == snapshot_2


def test_schema_drift_logged_for_missing_foreign_key(tmp_path, caplog, capsys, monkeypatch):
    """When the model declares an FK that the live DB lacks, startup logs a warning.

    Uses monkeypatch to swap in a fresh ``SQLModel.metadata`` for the
    duration of the test so the local Parent/Child classes don't leak
    into the global registry and corrupt later tests' ``create_all()``.
    """
    from sqlalchemy import MetaData
    from sqlmodel import Field, SQLModel, create_engine

    db_path = tmp_path / "drift.db"
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE parents (id TEXT PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE children (id TEXT PRIMARY KEY, parent_id TEXT NOT NULL)")
        conn.commit()

    # Isolate the local Parent/Child classes from the global model registry.
    # Without this, defining them at test scope would permanently register
    # them in SQLModel.metadata and later tests' SQLModel.metadata.create_all()
    # would only create 'parents'/'children' instead of the full schema.
    monkeypatch.setattr(SQLModel, "metadata", MetaData())

    class Parent(SQLModel, table=True):
        __tablename__ = "parents"
        id: str = Field(primary_key=True)

    class Child(SQLModel, table=True):
        __tablename__ = "children"
        id: str = Field(primary_key=True)
        parent_id: str = Field(foreign_key="parents.id", nullable=False)

    import logging

    caplog.set_level(logging.WARNING)

    from chaoscypher_core.adapters.sqlite.engine import log_schema_constraint_drift

    log_schema_constraint_drift(engine)

    # structlog by default writes to stdout rather than routing through the standard
    # logging bridge, so check both caplog (for stdlib-bridge setups) and capsys
    # (for structlog's ConsoleRenderer output).
    captured = capsys.readouterr()
    all_output = [r.message for r in caplog.records] + [caplog.text, captured.out, captured.err]
    assert any("schema_constraint_drift" in m for m in all_output), (
        f"Expected schema_constraint_drift warning; got records: {[r.message for r in caplog.records]!r}"
        f"\ncaplog.text: {caplog.text!r}"
        f"\nstdout: {captured.out!r}"
        f"\nstderr: {captured.err!r}"
    )
