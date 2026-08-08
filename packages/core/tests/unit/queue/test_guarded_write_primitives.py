# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Guarded-write / atomic-move primitives on ``QueueClient``.

The CAS family (approved 2026-07-13, verdicts 2026-07-23): a guarded
status write, a guarded delete, the reconciler's atomic requeue move,
and a plain string compare-and-swap. These wrapper tests pin the lazy
``script_load`` → ``evalsha`` shape (mirroring ``complete_task_atomic``),
the exact KEYS/ARGV layout each Lua script expects, and the string
return-contract (``__ok__`` / ``__missing__`` / current-status).

Harness mirrors ``test_client_coverage.py``'s ``QueueClient.__new__`` +
recording AsyncMock backend pattern.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import (
    GUARDED_MISSING,
    GUARDED_OK,
    QueueClient,
)


def _bare_client() -> QueueClient:
    """Construct a QueueClient via __new__ with the manual attribute set."""
    client = QueueClient.__new__(QueueClient)
    client.client = None
    client._connected = True
    client._enabled = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 7200
    client._llm_result_ttl = 3600
    client._failed_result_ttl = 14 * 86_400
    client._cancel_ttl = 300
    client._atomic_complete_sha = None
    client._guarded_status_write_sha = None
    client._guarded_delete_sha = None
    client._requeue_atomic_sha = None
    client._string_cas_sha = None
    client.monitor = None
    client._handlers = {}
    client._retry_policy = {}
    client._transient_retry_policy = {}
    client._queues = set()
    return client


def _make_client(evalsha_return: object = b"__ok__") -> tuple[QueueClient, MagicMock]:
    client = _bare_client()
    valkey = MagicMock()
    valkey.script_load = AsyncMock(return_value="sha-abc")
    valkey.evalsha = AsyncMock(return_value=evalsha_return)
    client.client = valkey
    return client, valkey


# ---------------------------------------------------------------------------
# guarded_status_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guarded_status_write_loads_then_caches_sha() -> None:
    """First call script_loads; second call reuses the cached SHA."""
    client, valkey = _make_client()

    await client.guarded_status_write("t-1", new_status="cancelled", allowed_from=("queued",))
    valkey.script_load.assert_awaited_once()
    valkey.evalsha.assert_awaited_once()

    await client.guarded_status_write("t-2", new_status="cancelled", allowed_from=("queued",))
    valkey.script_load.assert_awaited_once()
    assert valkey.evalsha.await_count == 2


@pytest.mark.asyncio
async def test_guarded_status_write_evalsha_shape() -> None:
    """KEYS/ARGV layout matches guarded_status_write.lua's contract."""
    client, valkey = _make_client()

    result = await client.guarded_status_write(
        "t-1",
        new_status="cancelled",
        allowed_from=("queued", "running"),
        extra_fields={"completed_at": "2026-07-23T00:00:00Z"},
        remove_from=f"queue:{QUEUE_OPERATIONS}:running",
        removal="srem",
    )

    assert result == GUARDED_OK
    args = valkey.evalsha.await_args.args
    assert args[0] == "sha-abc"
    assert args[1] == 2  # numkeys
    assert args[2] == "queue:task:t-1"
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:running"
    assert args[4] == "t-1"
    assert args[5] == "queued,running"
    assert args[6] == "srem"
    assert args[7] == ""  # no expire
    # Field pairs: status first, then extras.
    pairs = dict(zip(args[8::2], args[9::2], strict=True))
    assert pairs == {
        "status": "cancelled",
        "completed_at": "2026-07-23T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_guarded_status_write_expire_and_no_removal() -> None:
    """expire_seconds is stringified; remove_from omitted → '' key + 'none'."""
    client, valkey = _make_client()

    await client.guarded_status_write(
        "t-9",
        new_status="failed",
        allowed_from=("running",),
        expire_seconds=1209600,
    )

    args = valkey.evalsha.await_args.args
    assert args[3] == ""  # placeholder removal key
    assert args[6] == "none"
    assert args[7] == "1209600"


@pytest.mark.asyncio
async def test_guarded_status_write_decodes_refusal_and_missing() -> None:
    """Bytes returns decode: current status on refusal, sentinel when gone."""
    client, valkey = _make_client(evalsha_return=b"completed")
    result = await client.guarded_status_write(
        "t-1", new_status="cancelled", allowed_from=("running",)
    )
    assert result == "completed"

    valkey.evalsha = AsyncMock(return_value=b"__missing__")
    result = await client.guarded_status_write(
        "t-1", new_status="cancelled", allowed_from=("running",)
    )
    assert result == GUARDED_MISSING


# ---------------------------------------------------------------------------
# guarded_delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guarded_delete_evalsha_shape() -> None:
    client, valkey = _make_client()

    result = await client.guarded_delete(
        "t-3",
        allowed_from=("queued",),
        remove_from=f"queue:{QUEUE_OPERATIONS}:pending",
        removal="zrem",
    )

    assert result == GUARDED_OK
    args = valkey.evalsha.await_args.args
    assert args[1] == 2
    assert args[2] == "queue:task:t-3"
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:pending"
    assert args[4] == "t-3"
    assert args[5] == "queued"
    assert args[6] == "zrem"


@pytest.mark.asyncio
async def test_guarded_delete_refusal_returns_status() -> None:
    client, _valkey = _make_client(evalsha_return=b"running")
    result = await client.guarded_delete("t-3", allowed_from=("queued",))
    assert result == "running"


# ---------------------------------------------------------------------------
# requeue_task_atomic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requeue_task_atomic_evalsha_shape() -> None:
    client, valkey = _make_client()

    result = await client.requeue_task_atomic(QUEUE_OPERATIONS, "t-4", 75.0)

    assert result == GUARDED_OK
    args = valkey.evalsha.await_args.args
    assert args[1] == 3
    assert args[2] == "queue:task:t-4"
    assert args[3] == f"queue:{QUEUE_OPERATIONS}:pending"
    assert args[4] == f"queue:{QUEUE_OPERATIONS}:running"
    assert args[5] == "t-4"
    assert args[6] == "75.0"


@pytest.mark.asyncio
async def test_requeue_task_atomic_refusal_returns_terminal_status() -> None:
    client, _valkey = _make_client(evalsha_return=b"completed")
    result = await client.requeue_task_atomic(QUEUE_OPERATIONS, "t-4", 50.0)
    assert result == "completed"


# ---------------------------------------------------------------------------
# compare_and_swap_string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_and_swap_string_true_on_swap() -> None:
    client, valkey = _make_client(evalsha_return=1)
    swapped = await client.compare_and_swap_string(
        "approval:c1:t1", expected="__pending__", new="approve"
    )
    assert swapped is True
    args = valkey.evalsha.await_args.args
    assert args[1] == 1
    assert args[2] == "approval:c1:t1"
    assert args[3] == "__pending__"
    assert args[4] == "approve"


@pytest.mark.asyncio
async def test_compare_and_swap_string_false_when_lost() -> None:
    client, _valkey = _make_client(evalsha_return=0)
    swapped = await client.compare_and_swap_string(
        "approval:c1:t1", expected="__pending__", new="reject"
    )
    assert swapped is False


# ---------------------------------------------------------------------------
# NOSCRIPT recovery + connect() SHA reset (Valkey-restart resilience)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evalsha_noscript_reloads_script_and_retries() -> None:
    """A NOSCRIPT (server script cache cleared) reloads and retries once.

    A Valkey restart empties the server-side script cache while the
    client's cached SHA lives on — without the reload, every guarded
    write would fail until the worker process restarted.
    """
    from valkey.exceptions import NoScriptError

    client, valkey = _make_client()
    valkey.evalsha = AsyncMock(side_effect=[NoScriptError("NOSCRIPT"), b"__ok__"])

    result = await client.guarded_status_write(
        "t-1", new_status="cancelled", allowed_from=("queued",)
    )

    assert result == GUARDED_OK
    assert valkey.script_load.await_count == 2  # initial lazy load + reload
    assert valkey.evalsha.await_count == 2  # failed call + retry


@pytest.mark.asyncio
async def test_complete_task_atomic_noscript_reloads_and_retries() -> None:
    """complete_task_atomic recovers from a cleared script cache the same way."""
    from valkey.exceptions import NoScriptError

    client, valkey = _make_client()
    client._atomic_complete_sha = "stale-sha"  # cached before the restart
    valkey.evalsha = AsyncMock(side_effect=[NoScriptError("NOSCRIPT"), None])

    await client.complete_task_atomic(QUEUE_OPERATIONS, "t-1")

    assert valkey.script_load.await_count == 1  # reload only (SHA was cached)
    assert valkey.evalsha.await_count == 2
    assert client._atomic_complete_sha == "sha-abc"  # refreshed from reload


@pytest.mark.asyncio
async def test_connect_resets_all_cached_script_shas() -> None:
    """connect() drops every cached SHA — they are server-side state.

    Uses the queueing-disabled early return so no real connection is
    attempted; the reset happens before the enabled check.
    """
    client = _bare_client()
    for attr in (
        "_atomic_complete_sha",
        "_guarded_status_write_sha",
        "_guarded_delete_sha",
        "_requeue_atomic_sha",
        "_string_cas_sha",
    ):
        setattr(client, attr, "stale-sha")

    settings = MagicMock()
    settings.llm.enable_llm_queueing = False

    await client.connect(settings)

    for attr in (
        "_atomic_complete_sha",
        "_guarded_status_write_sha",
        "_guarded_delete_sha",
        "_requeue_atomic_sha",
        "_string_cas_sha",
    ):
        assert getattr(client, attr) is None, f"{attr} not reset by connect()"
