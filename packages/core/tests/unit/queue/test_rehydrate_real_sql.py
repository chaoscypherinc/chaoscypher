# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Queue rehydration's row filter, run against a real SQLite table.

The other rehydration tests fake the result set, so they hold for any
statement. Here a Valkey wipe is simulated (no task hash exists) over rows in
every state: only the live, uncancelled ones may be enqueued again. A
user-cancelled chunk keeps its queued/running status (the cancel writes only
``cancelled_at``), so the ``cancelled_at IS NULL`` filter is what stops it.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import ChunkExtractionJob, ChunkExtractionTask
from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


class _WipedQueue:
    """A queue client after a Valkey wipe: no task hash exists; enqueues are recorded."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

        async def _exists(_key: str) -> int:
            return 0

        self.client = SimpleNamespace(exists=_exists)

    async def enqueue(self, **kwargs: Any) -> str:
        self.enqueued.append(kwargs["data"]["chunk_task_id"])
        return f"qt-{len(self.enqueued)}"


def _seed(adapter: SqliteAdapter) -> None:
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "test",
            "filename": "src-1.pdf",
            "filepath": "/tmp/src-1.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": "hash-src-1",
            "status": "extracting",
        }
    )
    with adapter.transaction():
        adapter.session.add(
            ChunkExtractionJob(
                id="job-1", database_name="test", source_id="src-1", status="running"
            )
        )
    cancelled = datetime.now(UTC)
    rows = {
        "live-queued": ("queued", None),
        "live-running": ("running", None),
        "live-pending": ("pending", None),
        "cancelled-queued": ("queued", cancelled),
        "cancelled-running": ("running", cancelled),
        "done": ("completed", None),
        "failed": ("failed", None),
        "orphaned": ("orphaned", None),
    }
    with adapter.transaction():
        for i, (task_id, (status, cancelled_at)) in enumerate(rows.items()):
            adapter.session.add(
                ChunkExtractionTask(
                    id=task_id,
                    job_id="job-1",
                    database_name="test",
                    chunk_index=i,
                    status=status,
                    cancelled_at=cancelled_at,
                    queue_task_id=f"gone-{task_id}",
                )
            )


@pytest.mark.asyncio
async def test_only_live_uncancelled_rows_are_reenqueued(adapter: SqliteAdapter) -> None:
    _seed(adapter)
    queue = _WipedQueue()

    rehydrated = await rehydrate_queue_from_db(queue, adapter.session)  # type: ignore[arg-type]

    assert sorted(queue.enqueued) == ["live-pending", "live-queued", "live-running"]
    assert rehydrated == 3
    adapter.session.expire_all()
    cancelled = adapter.session.get(ChunkExtractionTask, "cancelled-queued")
    assert cancelled is not None
    assert cancelled.queue_task_id == "gone-cancelled-queued"  # never repointed
