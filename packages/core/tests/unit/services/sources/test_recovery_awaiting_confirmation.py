# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Recovery treats awaiting_confirmation as an explicit no-op + counts it.

A parked source is waiting on a human, not on a crashed worker. Recovery
must NEVER re-dispatch it (that would bypass the confirmation gate). The
classifier returns None for awaiting_confirmation via an explicit,
commented branch so no one later "fixes" it into auto-proceeding. A
separate health count surfaces partial-write-stuck parked rows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed_awaiting(adapter, *, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "awaiting_confirmation",
            "confirmation_required": True,
        }
    )


@pytest.mark.asyncio
async def test_classify_awaiting_confirmation_is_no_op(in_memory_adapter) -> None:
    """_classify returns None for a parked source — never a dispatch action."""
    _seed_awaiting(in_memory_adapter, source_id="src-parked")
    queue = AsyncMock()
    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)

    source = in_memory_adapter.get_source("src-parked", database_name="default")
    action = await recovery._classify(source, database_name="default")

    assert action is None, "awaiting_confirmation must classify as a no-op"


@pytest.mark.asyncio
async def test_reconcile_never_dispatches_awaiting_source(in_memory_adapter) -> None:
    """A bulk reconcile pass does not enqueue work for a parked source."""
    _seed_awaiting(in_memory_adapter, source_id="src-parked-bulk")
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t"})
    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)

    await recovery.reconcile_database(database_name="default")

    queue.enqueue.assert_not_awaited()


def test_count_awaiting_confirmation_counts_parked_rows(in_memory_adapter) -> None:
    """The health-count helper reports how many rows are parked."""
    _seed_awaiting(in_memory_adapter, source_id="src-a")
    _seed_awaiting(in_memory_adapter, source_id="src-b")
    queue = AsyncMock()
    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)

    count = recovery.count_awaiting_confirmation(database_name="default")

    assert count == 2
