# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Execute the real ``requeue_atomic.lua`` and pin that it leaves ``attempts``.

Every other test of the reconciler's requeue branch (notably
``packages/cortex/tests/unit/features/queue/test_reconciler_attempts.py``)
hand-writes the script's effect in Python, so re-adding the ``HINCRBY``
that #599 deleted would fail none of them. This test runs the shipped
``.lua`` file's bytes — through ``QueueClient.requeue_task_atomic``, so the
loader, the ``SCRIPT LOAD`` → ``EVALSHA`` shape and the KEYS/ARGV layout are
production's — against a Lua-capable fake, and asserts the retry budget is
untouched: ``attempts`` is charged exactly once per dispatch, by the worker
at claim time (``QueueWorker._process_task``).

The negative control restores the deleted ``HINCRBY`` line in a copy of the
script and shows the same assertions then fail — without it the test could
not tell a correct script from the buggy one.
"""

from __future__ import annotations

from typing import cast

import pytest
from fakeredis import aioredis
from valkey.asyncio import Valkey

from chaoscypher_core.queue import client as client_module
from chaoscypher_core.queue.client import GUARDED_OK, QueueClient


QUEUE = "operations"
TASK_ID = "task-abandoned"
PRIORITY = 5.0
SEEDED_ATTEMPTS = 2
DEAD_LETTER_TTL = 14 * 86_400

TASK_KEY = f"queue:task:{TASK_ID}"
PENDING_KEY = f"queue:{QUEUE}:pending"
RUNNING_KEY = f"queue:{QUEUE}:running"

# The exact line #599 removed, in its original position (after the SREM,
# before the ``__ok__`` return). Restoring it is the mutation below.
_SREM_LINE = "redis.call('SREM', KEYS[3], ARGV[1])\n"
_HINCRBY_LINE = "redis.call('HINCRBY', KEYS[1], 'attempts', 1)\n"


def _fake_valkey() -> aioredis.FakeRedis:
    """A Lua-capable Valkey stand-in (fakeredis executes EVAL via lupa)."""
    return aioredis.FakeRedis()


def _text(value: bytes | str) -> str:
    """Decode a raw reply (fakeredis returns bytes, like Valkey does)."""
    return value.decode() if isinstance(value, bytes) else value


def _queue_client(valkey: aioredis.FakeRedis) -> QueueClient:
    """A QueueClient bound to ``valkey`` without running ``connect()``.

    Mirrors the ``__new__`` harness in ``test_guarded_write_primitives.py``;
    ``requeue_task_atomic`` touches only these two attributes.
    """
    client = QueueClient.__new__(QueueClient)
    # fakeredis speaks the same asyncio command surface valkey-py does.
    client.client = cast("Valkey", valkey)
    client._requeue_atomic_sha = None
    return client


async def _seed_claimed_task(valkey: aioredis.FakeRedis) -> None:
    """Seed a task the worker claimed and then died on.

    ``attempts`` already carries the claim-time charge for the dispatch
    being recovered, the hash carries a terminal-failure dead-letter TTL,
    and the task sits in the running set.
    """
    await valkey.hset(
        TASK_KEY,
        mapping={
            "task_id": TASK_ID,
            "status": "running",
            "attempts": str(SEEDED_ATTEMPTS),
            "error": "worker died",
            "error_type": "WorkerCrash",
        },
    )
    await valkey.expire(TASK_KEY, DEAD_LETTER_TTL)
    await valkey.sadd(RUNNING_KEY, TASK_ID)


async def _task_fields(valkey: aioredis.FakeRedis) -> dict[str, str]:
    """Read the task hash as plain strings."""
    return {_text(k): _text(v) for k, v in (await valkey.hgetall(TASK_KEY)).items()}


async def _assert_requeued_without_charging_budget(valkey: aioredis.FakeRedis) -> None:
    """Assert the post-requeue state the reconciler depends on."""
    task = await _task_fields(valkey)
    assert task["attempts"] == str(SEEDED_ATTEMPTS), (
        f"requeue charged the retry budget: attempts {SEEDED_ATTEMPTS} -> {task['attempts']}"
    )
    assert task["status"] == "queued"
    assert task["error"] == ""
    assert task["error_type"] == ""
    assert await valkey.ttl(TASK_KEY) == -1, "dead-letter TTL was not cleared"
    assert await valkey.zrange(PENDING_KEY, 0, -1, withscores=True) == [
        (TASK_ID.encode(), PRIORITY)
    ]
    assert await valkey.smembers(RUNNING_KEY) == set()


@pytest.mark.asyncio
async def test_requeue_atomic_lua_does_not_charge_attempts() -> None:
    """The shipped script requeues the task and leaves ``attempts`` alone."""
    valkey = _fake_valkey()
    await _seed_claimed_task(valkey)

    result = await _queue_client(valkey).requeue_task_atomic(QUEUE, TASK_ID, PRIORITY)

    assert result == GUARDED_OK
    await _assert_requeued_without_charging_budget(valkey)


@pytest.mark.asyncio
async def test_restoring_the_hincrby_fails_the_attempts_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the #599 regression, re-applied, is caught.

    Runs the identical call path with one mutated script — proof that the
    test above discriminates, rather than passing on a script that bumps
    the counter.
    """
    real_script = client_module._REQUEUE_ATOMIC_SCRIPT
    assert _SREM_LINE in real_script, "anchor drifted; the mutation below is no longer faithful"
    mutated = real_script.replace(_SREM_LINE, _SREM_LINE + _HINCRBY_LINE)
    assert mutated != real_script
    monkeypatch.setattr(client_module, "_REQUEUE_ATOMIC_SCRIPT", mutated)

    valkey = _fake_valkey()
    await _seed_claimed_task(valkey)

    result = await _queue_client(valkey).requeue_task_atomic(QUEUE, TASK_ID, PRIORITY)

    assert result == GUARDED_OK
    assert (await _task_fields(valkey))["attempts"] == str(SEEDED_ATTEMPTS + 1)
    with pytest.raises(AssertionError, match="charged the retry budget"):
        await _assert_requeued_without_charging_budget(valkey)
