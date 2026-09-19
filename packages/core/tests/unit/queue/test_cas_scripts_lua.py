# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Execute the three CAS-family Lua scripts that no other test runs.

``test_requeue_atomic_lua.py`` proved the harness (fakeredis executes EVAL
via lupa) for ``requeue_atomic.lua``; until 2026-09-17 the other three —
``guarded_status_write.lua``, ``guarded_delete.lua`` and
``atomic_complete.lua`` — were asserted only through mocked ``evalsha``
calls: the KEYS/ARGV layout and a canned return value, never the script's
own semantics (the comma-split allowed-status match, the ``HGET`` ``false``
coercion, the ``for i = 5, #ARGV, 2`` field-pair loop, the ``srem``/``zrem``
dispatch, the conditional ``EXPIRE``). A regression in any of those passed
CI green. These tests run the shipped ``.lua`` bytes through the production
client methods, with a mutation-based negative control on the field-pair
loop so the suite is shown to discriminate.
"""

from __future__ import annotations

from typing import cast

import pytest
from fakeredis import aioredis
from valkey.asyncio import Valkey

from chaoscypher_core.queue import client as client_module
from chaoscypher_core.queue.client import GUARDED_MISSING, GUARDED_OK, QueueClient


QUEUE = "operations"
TASK_ID = "task-cas"
TASK_KEY = f"queue:task:{TASK_ID}"
PENDING_KEY = f"queue:{QUEUE}:pending"
RUNNING_KEY = f"queue:{QUEUE}:running"
HEARTBEAT_KEY = f"queue:task:{TASK_ID}:heartbeat"


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _queue_client(valkey: aioredis.FakeRedis) -> QueueClient:
    """A QueueClient bound to ``valkey`` without running ``connect()``."""
    client = QueueClient.__new__(QueueClient)
    client.client = cast("Valkey", valkey)
    client._atomic_complete_sha = None
    client._guarded_status_write_sha = None
    client._guarded_delete_sha = None
    return client


async def _task_fields(valkey: aioredis.FakeRedis) -> dict[str, str]:
    return {_text(k): _text(v) for k, v in (await valkey.hgetall(TASK_KEY)).items()}


async def _seed(valkey: aioredis.FakeRedis, status: str) -> None:
    await valkey.hset(TASK_KEY, mapping={"task_id": TASK_ID, "status": status, "attempts": "1"})


# ---------------------------------------------------------------------------
# guarded_status_write.lua
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guarded_status_write_transitions_removes_and_expires() -> None:
    """Happy path: status + extra fields written, id removed from the set, TTL applied."""
    valkey = aioredis.FakeRedis()
    await _seed(valkey, "running")
    await valkey.sadd(RUNNING_KEY, TASK_ID)

    result = await _queue_client(valkey).guarded_status_write(
        TASK_ID,
        new_status="failed",
        allowed_from=("queued", "running"),
        extra_fields={"error": "boom", "error_type": "RuntimeError"},
        remove_from=RUNNING_KEY,
        removal="srem",
        expire_seconds=600,
    )

    assert result == GUARDED_OK
    task = await _task_fields(valkey)
    assert task["status"] == "failed"
    assert task["error"] == "boom"
    assert task["error_type"] == "RuntimeError"
    assert task["attempts"] == "1", "unrelated fields survive the write"
    assert await valkey.smembers(RUNNING_KEY) == set()
    assert 0 < await valkey.ttl(TASK_KEY) <= 600


@pytest.mark.asyncio
async def test_guarded_status_write_refuses_when_status_not_allowed() -> None:
    """Lost race: nothing is written, removed or expired; the current status comes back."""
    valkey = aioredis.FakeRedis()
    await _seed(valkey, "completed")
    await valkey.sadd(RUNNING_KEY, TASK_ID)

    result = await _queue_client(valkey).guarded_status_write(
        TASK_ID,
        new_status="cancelled",
        allowed_from=("queued", "running"),
        extra_fields={"cancelled_at": "now"},
        remove_from=RUNNING_KEY,
        removal="srem",
        expire_seconds=600,
    )

    assert result == "completed"
    task = await _task_fields(valkey)
    assert task["status"] == "completed"
    assert "cancelled_at" not in task
    assert await valkey.smembers(RUNNING_KEY) == {TASK_ID.encode()}
    assert await valkey.ttl(TASK_KEY) == -1, "EXPIRE must not run on the refusal path"


@pytest.mark.asyncio
async def test_guarded_status_write_matches_whole_status_tokens_only() -> None:
    """The comma-split match is exact: ``queued`` is not matched by ``queued_x``."""
    valkey = aioredis.FakeRedis()
    await _seed(valkey, "queued_x")

    result = await _queue_client(valkey).guarded_status_write(
        TASK_ID, new_status="cancelled", allowed_from=("queued", "running")
    )

    assert result == "queued_x"
    assert (await _task_fields(valkey))["status"] == "queued_x"


@pytest.mark.asyncio
async def test_guarded_status_write_treats_missing_status_field_as_empty() -> None:
    """``HGET`` returns false for a hash with no ``status``; the script coerces it to ''.

    Such a hash is neither ``__missing__`` nor transitionable: the empty
    current status is reported back (an empty allowed list matches nothing,
    since ``gmatch`` over ``''`` yields no tokens) and nothing is written.
    """
    valkey = aioredis.FakeRedis()
    await valkey.hset(TASK_KEY, mapping={"task_id": TASK_ID})

    result = await _queue_client(valkey).guarded_status_write(
        TASK_ID, new_status="queued", allowed_from=("queued", "running")
    )

    assert result == ""
    assert "status" not in await _task_fields(valkey)


@pytest.mark.asyncio
async def test_guarded_status_write_missing_hash_and_zrem_mode() -> None:
    valkey = aioredis.FakeRedis()
    client = _queue_client(valkey)

    assert (
        await client.guarded_status_write(TASK_ID, new_status="x", allowed_from=("queued",))
        == GUARDED_MISSING
    )

    await _seed(valkey, "queued")
    await valkey.zadd(PENDING_KEY, {TASK_ID: 50.0})
    result = await client.guarded_status_write(
        TASK_ID,
        new_status="cancelled",
        allowed_from=("queued",),
        remove_from=PENDING_KEY,
        removal="zrem",
    )
    assert result == GUARDED_OK
    assert await valkey.zcard(PENDING_KEY) == 0
    assert await valkey.ttl(TASK_KEY) == -1, "no expire_seconds → no TTL"


@pytest.mark.asyncio
async def test_guarded_status_write_field_pair_loop_negative_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound error in the ARGV pair loop drops the last pair — and is caught.

    The mutated script still returns ``__ok__``, so a mocked-``evalsha``
    test asserting the return value passes; only a key-state assertion
    sees the missing field.
    """
    real_script = client_module._GUARDED_STATUS_WRITE_SCRIPT
    anchor = "for i = 5, #ARGV, 2 do"
    assert anchor in real_script, "anchor drifted; the mutation below is no longer faithful"
    monkeypatch.setattr(
        client_module,
        "_GUARDED_STATUS_WRITE_SCRIPT",
        real_script.replace(anchor, "for i = 5, #ARGV - 2, 2 do"),
    )
    valkey = aioredis.FakeRedis()
    await _seed(valkey, "running")

    result = await _queue_client(valkey).guarded_status_write(
        TASK_ID, new_status="failed", allowed_from=("running",), extra_fields={"error": "boom"}
    )

    assert result == GUARDED_OK, "the mutated script still reports success..."
    task = await _task_fields(valkey)
    assert not (task.get("status") == "failed" and task.get("error") == "boom"), (
        "...so only a key-state assertion can see the dropped pair"
    )


# ---------------------------------------------------------------------------
# guarded_delete.lua
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guarded_delete_removes_hash_and_pending_entry_when_allowed() -> None:
    valkey = aioredis.FakeRedis()
    await _seed(valkey, "queued")
    await valkey.zadd(PENDING_KEY, {TASK_ID: 50.0})

    result = await _queue_client(valkey).guarded_delete(
        TASK_ID, allowed_from=("queued",), remove_from=PENDING_KEY, removal="zrem"
    )

    assert result == GUARDED_OK
    assert await valkey.exists(TASK_KEY) == 0
    assert await valkey.zcard(PENDING_KEY) == 0


@pytest.mark.asyncio
async def test_guarded_delete_keeps_a_task_that_raced_to_running() -> None:
    """cancel_by_metadata's contract: a live hash is never destroyed mid-flight."""
    valkey = aioredis.FakeRedis()
    await _seed(valkey, "running")
    await valkey.zadd(PENDING_KEY, {TASK_ID: 50.0})

    result = await _queue_client(valkey).guarded_delete(
        TASK_ID, allowed_from=("queued",), remove_from=PENDING_KEY, removal="zrem"
    )

    assert result == "running"
    assert await valkey.exists(TASK_KEY) == 1
    assert await valkey.zcard(PENDING_KEY) == 1, "refusal touches neither the hash nor the set"


@pytest.mark.asyncio
async def test_guarded_delete_missing_hash() -> None:
    valkey = aioredis.FakeRedis()
    assert (
        await _queue_client(valkey).guarded_delete(TASK_ID, allowed_from=("queued",))
        == GUARDED_MISSING
    )


# ---------------------------------------------------------------------------
# atomic_complete.lua
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_atomic_complete_srems_and_deletes_heartbeat() -> None:
    valkey = aioredis.FakeRedis()
    client = _queue_client(valkey)
    await valkey.sadd(RUNNING_KEY, TASK_ID, "task-other")
    await client.set_heartbeat(TASK_ID, ttl_seconds=30)

    await client.complete_task_atomic(QUEUE, TASK_ID)

    assert await valkey.smembers(RUNNING_KEY) == {b"task-other"}
    assert await valkey.exists(HEARTBEAT_KEY) == 0


@pytest.mark.asyncio
async def test_atomic_complete_is_idempotent_on_missing_keys() -> None:
    """Documented: returns without error when neither key exists."""
    valkey = aioredis.FakeRedis()
    await _queue_client(valkey).complete_task_atomic(QUEUE, TASK_ID)
    assert await valkey.exists(RUNNING_KEY) == 0
