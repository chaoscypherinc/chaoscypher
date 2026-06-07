# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Regression: deleting a source cascades to source_recovery_events.

Asserts that a hard DELETE on the sources row (bypassing the app-layer
orchestrator) removes the associated source_recovery_events rows via the
ON DELETE CASCADE foreign key.  Before migration 0017 the FK did not
exist and orphan event rows were silently left behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> SqliteAdapter:
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


@pytest.mark.asyncio
async def test_recovery_event_cascade_on_source_delete(adapter: SqliteAdapter) -> None:
    """ON DELETE CASCADE removes event rows when the parent source is deleted.

    Arrange: seed one source row and one recovery event row.
    Act:     hard-delete the source via raw SQL (bypasses the orchestrator
             so only the FK does the cleanup — not app-layer logic).
    Assert:  the event row is gone, not left as an orphan.
    """
    adapter.create_source(
        {
            "id": "src_cascade",
            "database_name": "default",
            "filename": "cascade.txt",
            "filepath": "/tmp/cascade.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": "hash_cascade",
            "status": "extracting",
        }
    )
    adapter.record_recovery_event(
        source_id="src_cascade",
        database_name="default",
        from_status="extracting",
        action_taken="extract_chunk",
        reason="stalled",
        enqueued_count=1,
    )

    pre = adapter.list_recovery_events(source_id="src_cascade", database_name="default")
    assert len(pre) == 1, "seed event should be visible pre-delete"

    # Hard-delete the source row at the storage layer (the cascade orchestrator
    # already removes recovery events explicitly today; this test asserts the FK
    # does the work even when the orchestrator is bypassed).
    adapter.session.execute(text("DELETE FROM sources WHERE id = :id"), {"id": "src_cascade"})
    adapter.session.commit()

    post = adapter.list_recovery_events(source_id="src_cascade", database_name="default")
    assert post == [], "FK cascade should have removed the event row"
