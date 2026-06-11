# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``seed_default_triggers``.

The dormant "Auto-Embed on Node Update" trigger was removed on 2026-06-11:
no ``node.update`` event is ever published anywhere in the codebase, and
Cortex's ``update_node`` already re-embeds synchronously. Seeding must now
create exactly one auto-embed trigger (``node.create``).
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, select

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import Trigger
from chaoscypher_core.database.seed import seed_default_triggers, seed_default_workflows


def _seeded_triggers(tmp_path: Path) -> list[Trigger]:
    """Seed workflows + triggers into a fresh file-backed DB and return all triggers.

    Workflows are seeded first because the triggers' ``workflow_id`` FK
    (enforced — get_engine turns ``PRAGMA foreign_keys=ON``) points at the
    system Generate Embeddings workflow.
    """
    db_path = tmp_path / "app.db"
    engine = get_engine(db_path)
    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            seed_default_workflows(session, "default")
            seed_default_triggers(session, "default")
            return list(session.exec(select(Trigger)).all())
    finally:
        evict_engine(db_path)


def test_seeds_exactly_one_auto_embed_trigger(tmp_path: Path) -> None:
    """Only the node.create auto-embed trigger is seeded."""
    triggers = _seeded_triggers(tmp_path)
    auto_embed = [t for t in triggers if t.id.startswith("system_trigger_auto_embed")]
    assert len(auto_embed) == 1
    assert auto_embed[0].id == "system_trigger_auto_embed_create_v1"
    assert auto_embed[0].event_source == "node.create"


def test_no_node_update_trigger_is_seeded(tmp_path: Path) -> None:
    """The dormant node.update trigger must not be seeded.

    No ``node.update`` event is published anywhere; the row only confused
    operators into thinking updates re-embed via the trigger path.
    """
    triggers = _seeded_triggers(tmp_path)
    assert all(t.event_source != "node.update" for t in triggers)
    assert all(t.id != "system_trigger_auto_embed_update_v1" for t in triggers)
