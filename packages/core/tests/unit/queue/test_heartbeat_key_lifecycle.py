# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Heartbeat keys against a real key/value backend (2026-09-17 queue section-audit).

Two defects the mock-based suites could not see, because both hinge on what
Valkey does with a key that is *not there*:

- ``refresh_heartbeat`` used to be a bare ``EXPIRE``. ``EXPIRE`` on a missing
  key returns 0 and raises nothing, so a heartbeat that lapsed once (a loop
  stall or Valkey blip longer than the TTL) could never come back — every
  later refresh "succeeded" while writing nothing, and the reconciler
  classified the still-running task as abandoned for the rest of its life.
- ``clear_old_completed_tasks`` scans ``queue:task:*``, which also matches
  the heartbeat STRINGS at ``queue:task:{id}:heartbeat``; ``HGETALL`` on a
  string raises ``WRONGTYPE`` mid-sweep whenever any task is running.

fakeredis is used so the assertions are about key state, not call shapes.
"""

from __future__ import annotations

from typing import cast

import pytest
from fakeredis import aioredis
from valkey.asyncio import Valkey

from chaoscypher_core.queue.client import QueueClient


TASK_ID = "task-live"
HEARTBEAT_KEY = f"queue:task:{TASK_ID}:heartbeat"


def _queue_client(valkey: aioredis.FakeRedis) -> QueueClient:
    """A QueueClient bound to ``valkey`` without running ``connect()``."""
    client = QueueClient.__new__(QueueClient)
    client.client = cast("Valkey", valkey)
    client._connected = True
    return client


@pytest.mark.asyncio
async def test_refresh_heartbeat_recreates_a_lapsed_key() -> None:
    """A refresh after the key vanished brings it back with a fresh TTL."""
    valkey = aioredis.FakeRedis()
    client = _queue_client(valkey)

    await client.set_heartbeat(TASK_ID, ttl_seconds=30)
    assert await valkey.exists(HEARTBEAT_KEY) == 1

    # The lapse: TTL expiry, eviction, or a Valkey restart past the TTL.
    await valkey.delete(HEARTBEAT_KEY)
    assert await valkey.exists(HEARTBEAT_KEY) == 0

    await client.refresh_heartbeat(TASK_ID, ttl_seconds=30)

    assert await valkey.exists(HEARTBEAT_KEY) == 1, "refresh must re-create a lapsed heartbeat"
    assert 0 < await valkey.ttl(HEARTBEAT_KEY) <= 30

    # Negative control: the previous EXPIRE-only implementation is a no-op here.
    await valkey.delete(HEARTBEAT_KEY)
    assert await valkey.expire(HEARTBEAT_KEY, 30) is False
    assert await valkey.exists(HEARTBEAT_KEY) == 0


@pytest.mark.asyncio
async def test_refresh_heartbeat_resets_ttl_on_a_live_key() -> None:
    """The ordinary path still extends a live key rather than replacing it early."""
    valkey = aioredis.FakeRedis()
    client = _queue_client(valkey)

    await client.set_heartbeat(TASK_ID, ttl_seconds=5)
    await client.refresh_heartbeat(TASK_ID, ttl_seconds=30)

    assert await valkey.ttl(HEARTBEAT_KEY) > 5


@pytest.mark.asyncio
async def test_clear_old_completed_tasks_skips_heartbeat_strings() -> None:
    """A live heartbeat string under the task glob neither raises nor is deleted."""
    valkey = aioredis.FakeRedis()
    client = _queue_client(valkey)

    await valkey.hset(
        "queue:task:task-done",
        mapping={"task_id": "task-done", "queue": "operations", "status": "completed"},
    )
    await valkey.set("queue:result:task-done", "{}")
    await valkey.hset(
        f"queue:task:{TASK_ID}",
        mapping={"task_id": TASK_ID, "queue": "operations", "status": "running"},
    )
    await client.set_heartbeat(TASK_ID, ttl_seconds=30)
    await valkey.lpush("queue:recent", "task-done", TASK_ID)

    removed = await client.clear_old_completed_tasks()

    assert removed == 1
    assert await valkey.exists("queue:task:task-done") == 0
    assert await valkey.exists("queue:result:task-done") == 0
    assert await valkey.exists(f"queue:task:{TASK_ID}") == 1
    assert await valkey.exists(HEARTBEAT_KEY) == 1, "the sweep must not touch heartbeat keys"
    # The stale-recent cleanup ran to completion (it was skipped when the
    # WRONGTYPE escaped mid-loop).
    recent = [v.decode() for v in await valkey.lrange("queue:recent", 0, -1)]
    assert recent == [TASK_ID]
