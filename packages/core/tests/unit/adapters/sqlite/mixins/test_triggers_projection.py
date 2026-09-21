# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``list_triggers`` must project the two JSON config columns.

Regression pin. The projection previously carried
``# EXCLUDE: filters, workflow_inputs (JSON)``. Deferred columns are absent
from the instance ``__dict__``, and ``entity_to_dict``'s ``model_dump`` reads
``__dict__`` without triggering SQLAlchemy's lazy-load refresh — so the
returned dict had no ``filters`` key at all. Its re-hydration fallback is
gated on an EMPTY dump, which a ten-column projection never produces, so
nothing repaired the loss.

Downstream that made ``trigger.get("filters", {})`` yield ``{}``, which
``TriggerExecutor._filters_match`` treats as "match everything", and dropped
``workflow_inputs`` from every dispatched payload.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import Trigger, Workflow


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _seed_configured_trigger(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(Workflow(id="wf-1", database_name="db", name="wf-1-name"))
    with adapter.transaction():
        adapter.session.add(
            Trigger(
                id="t-scoped",
                database_name="db",
                name="scoped-to-one-source",
                event_source="node.created",
                filters={"source_id": "SPECIFIC"},
                workflow_id="wf-1",
                workflow_inputs={"depth": "2"},
            )
        )


def test_list_triggers_returns_filters(adapter: SqliteAdapter) -> None:
    """A configured ``filters`` dict survives the projection."""
    _seed_configured_trigger(adapter)

    (trigger,) = adapter.list_triggers(database_name="db")

    assert "filters" in trigger, "filters must not be deferred out of the projection"
    assert trigger["filters"] == {"source_id": "SPECIFIC"}


def test_list_triggers_returns_workflow_inputs(adapter: SqliteAdapter) -> None:
    """A configured ``workflow_inputs`` dict survives the projection."""
    _seed_configured_trigger(adapter)

    (trigger,) = adapter.list_triggers(database_name="db")

    assert "workflow_inputs" in trigger
    assert trigger["workflow_inputs"] == {"depth": "2"}


def test_listed_filters_do_not_degrade_to_match_everything(adapter: SqliteAdapter) -> None:
    """The dispatch-path read must not collapse to the match-everything case.

    ``TriggerExecutor._filters_match`` returns True for a falsy ``filters``,
    so an absent key silently fires a source-scoped trigger on every source.
    """
    _seed_configured_trigger(adapter)

    (trigger,) = adapter.list_triggers(database_name="db")

    assert trigger.get("filters", {}), "empty filters would match every event"
