# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Docker recovery leaves memory sources to the link step (spec 2026-10-24 § 4.3, D17).

A memory is linked by ``link_memory`` only: re-dispatched as an import, it would be
extracted as a plain document, without its seed. Its ``recovery_attempts`` count failed
links, so the exhaustion guard must not read them either. Every other source, including
one whose ``source_type`` is NULL (most rows predate the column), is recovered as before.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.sources.recovery import SourceRecovery


def _seed(
    adapter: Any,
    source_id: str,
    *,
    status: str,
    source_type: str | None,
    recovery_attempts: int = 0,
) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": status,
            "auto_analyze": True,
            "source_type": source_type,
            "recovery_attempts": recovery_attempts,
        }
    )


def _queue() -> AsyncMock:
    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-new"})
    queue.task_exists_for_source = AsyncMock(return_value=False)
    return queue


def _snapshot(adapter: Any, source_id: str) -> tuple[Any, ...]:
    row = adapter.get_source(source_id, database_name="default")
    return (row["status"], row["recovery_attempts"], row["error_stage"], row["error_message"])


async def test_recovery_leaves_memory_sources_alone(in_memory_adapter: Any) -> None:
    memories = {
        "m-pending": "pending",
        "m-indexed": "indexed",
        "m-extracting": "extracting",
    }
    for source_id, status in memories.items():
        _seed(
            in_memory_adapter,
            source_id,
            status=status,
            source_type=MEMORY_SOURCE_TYPE,
            recovery_attempts=2,
        )
    # a memory whose links failed ten times: the exhaustion guard must not end it
    _seed(
        in_memory_adapter,
        "m-tried",
        status="indexed",
        source_type=MEMORY_SOURCE_TYPE,
        recovery_attempts=10,
    )
    _seed(in_memory_adapter, "doc", status="indexed", source_type=None)
    before = {i: _snapshot(in_memory_adapter, i) for i in [*memories, "m-tried"]}

    queue = _queue()
    stats = await SourceRecovery(adapter=in_memory_adapter, queue_client=queue).reconcile_database(
        database_name="default"
    )

    dispatched = [call.kwargs["data"]["file_id"] for call in queue.enqueue.await_args_list]
    assert dispatched == ["doc"]
    assert queue.enqueue.await_args.kwargs["operation"] == "import_analysis"
    assert {i: _snapshot(in_memory_adapter, i) for i in before} == before
    assert (stats.recovered, stats.skipped_healthy, stats.skipped_exhausted) == (1, 4, 0)


async def test_manual_resume_refuses_a_memory(in_memory_adapter: Any) -> None:
    _seed(
        in_memory_adapter,
        "m-indexed",
        status="indexed",
        source_type=MEMORY_SOURCE_TYPE,
        recovery_attempts=3,
    )
    queue = _queue()
    recovery = SourceRecovery(adapter=in_memory_adapter, queue_client=queue)

    assert await recovery.recover_source(source_id="m-indexed", database_name="default") is False
    assert _snapshot(in_memory_adapter, "m-indexed") == ("indexed", 3, None, None)
    queue.enqueue.assert_not_awaited()


def test_the_stuck_extracting_scan_reads_each_source_type(in_memory_adapter: Any) -> None:
    """Neuron's stuck-extracting recovery tells a memory by the type this scan projects."""
    _seed(in_memory_adapter, "m-extracting", status="extracting", source_type=MEMORY_SOURCE_TYPE)
    _seed(in_memory_adapter, "doc", status="extracting", source_type=None)
    stuck = in_memory_adapter.get_stuck_extracting_sources("default")
    assert {s["id"]: s["source_type"] for s in stuck} == {
        "m-extracting": MEMORY_SOURCE_TYPE,
        "doc": None,
    }
