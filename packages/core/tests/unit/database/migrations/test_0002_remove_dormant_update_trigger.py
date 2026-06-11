# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data-migration test: 0002 deletes the dormant node.update auto-embed trigger.

Existing databases were seeded with a ``system_trigger_auto_embed_update_v1``
trigger on ``node.update`` — an event no code path ever publishes (Cortex's
``update_node`` re-embeds synchronously instead). Revision 0002 removes that
inert row; everything else in the triggers table must survive untouched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chaoscypher_core.adapters.sqlite.engine import evict_engine
from chaoscypher_core.database.migrations.runner import upgrade_to, upgrade_to_head


_LEGACY_ROWS = (
    ("system_trigger_auto_embed_create_v1", "Auto-Embed on Node Create", "node.create"),
    ("system_trigger_auto_embed_update_v1", "Auto-Embed on Node Update", "node.update"),
)


def _baseline_db_with_legacy_triggers(tmp_path: Path) -> Path:
    """Build a 0001-level DB carrying both pre-0002 auto-embed trigger rows.

    Rows are inserted via raw sqlite3 (FK enforcement off by default) so the
    test doesn't need to materialize the workflows parent row.
    """
    db_path = tmp_path / "app.db"
    sqlite3.connect(str(db_path)).close()
    upgrade_to(db_path, "0001")
    with sqlite3.connect(str(db_path)) as conn:
        for trigger_id, name, event_source in _LEGACY_ROWS:
            conn.execute(
                "INSERT INTO triggers (id, database_name, name, event_source, filters,"
                " workflow_id, workflow_inputs, enabled, priority, created_at, updated_at)"
                " VALUES (?, 'default', ?, ?, '{}',"
                " 'system_workflow_generate_embeddings_v1', '{}', 1, 0,"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
                (trigger_id, name, event_source),
            )
        conn.commit()
    return db_path


def _trigger_ids(db_path: Path) -> set[str]:
    """Return the set of trigger ids currently in the DB."""
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT id FROM triggers")}


def test_upgrade_deletes_dormant_node_update_trigger(tmp_path: Path) -> None:
    """Upgrading past 0002 removes the inert node.update row, keeps node.create."""
    db_path = _baseline_db_with_legacy_triggers(tmp_path)
    try:
        upgrade_to_head(db_path)

        ids = _trigger_ids(db_path)
        assert "system_trigger_auto_embed_update_v1" not in ids
        assert "system_trigger_auto_embed_create_v1" in ids
    finally:
        evict_engine(db_path)


def test_upgrade_leaves_user_triggers_alone(tmp_path: Path) -> None:
    """A user trigger that happens to listen on node.update is NOT deleted.

    The DELETE is scoped to the seeded system row's id AND event_source —
    user-created triggers must never be collateral damage.
    """
    db_path = _baseline_db_with_legacy_triggers(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO triggers (id, database_name, name, event_source, filters,"
            " workflow_id, workflow_inputs, enabled, priority, created_at, updated_at)"
            " VALUES ('user_trigger_1', 'default', 'My Update Hook', 'node.update', '{}',"
            " 'system_workflow_generate_embeddings_v1', '{}', 1, 0,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.commit()
    try:
        upgrade_to_head(db_path)

        ids = _trigger_ids(db_path)
        assert "user_trigger_1" in ids
        assert "system_trigger_auto_embed_update_v1" not in ids
    finally:
        evict_engine(db_path)
