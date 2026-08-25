# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract: retry-on-crash policy is process-independent (entry 566).

``QueueClient._retry_policy`` was, until this fix, populated ONLY by
``register_handlers()`` — a Neuron-only call (see
``chaoscypher_neuron.setup``). Cortex shares the same Valkey-backed task
metadata via the module-level ``queue_client`` singleton but never calls
``register_handlers``, so ``get_retry_policy`` always answered ``False`` in
the Cortex process. ``POST /queue/reconcile`` (Cortex's admin endpoint,
``chaoscypher_cortex.features.queue.service.QueueService.force_reconcile``)
therefore terminally failed every task abandoned by a crashed worker, even
those whose Neuron handler had opted into ``retry_on_crash=True``.

The fix adds ``chaoscypher_core.constants.OPERATION_RETRY_ON_CRASH`` — a
module-level op-name -> bool table, valid in any process without handler
registration — and makes ``get_retry_policy`` fall back to it.
``register_handlers`` also validates every incoming ``HandlerSpec`` against
the table so a contradicting registration fails loudly at Neuron startup
instead of drifting silently.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import (
    OP_IMPORT_COMMIT,
    OPERATION_QUEUE_ROUTING,
    OPERATION_RETRY_ON_CRASH,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.queue.client import QueueClient
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.queue.reconciler import reconcile_queue


async def _ok_handler(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {"data": data}


# ---------------------------------------------------------------------------
# Canonical table completeness
# ---------------------------------------------------------------------------


def test_canonical_table_covers_every_routed_operation() -> None:
    """Every operation in OPERATION_QUEUE_ROUTING has a retry_on_crash entry.

    Keeps the two canonical tables from drifting apart as new operations
    are added — the same completeness guarantee OPERATION_QUEUE_ROUTING
    gets from CC044, applied here at test time instead of lint time.
    """
    missing = sorted(set(OPERATION_QUEUE_ROUTING) - set(OPERATION_RETRY_ON_CRASH))
    assert missing == [], f"operations missing from OPERATION_RETRY_ON_CRASH: {missing}"


def test_canonical_table_has_no_unrouted_operations() -> None:
    """The retry table declares no operation OPERATION_QUEUE_ROUTING doesn't know."""
    extra = sorted(set(OPERATION_RETRY_ON_CRASH) - set(OPERATION_QUEUE_ROUTING))
    assert extra == [], f"operations in OPERATION_RETRY_ON_CRASH but not routed: {extra}"


def test_import_commit_declared_retryable() -> None:
    """Pin the specific op the regression report used as its example."""
    assert OPERATION_RETRY_ON_CRASH[OP_IMPORT_COMMIT] is True


# ---------------------------------------------------------------------------
# get_retry_policy — process-independent fallback
# ---------------------------------------------------------------------------


def test_get_retry_policy_answers_from_canonical_table_without_registration() -> None:
    """A client that never called register_handlers still answers correctly.

    This is the Cortex process shape: the shared queue_client singleton is
    constructed and connected, but register_handlers() is never invoked
    because that only happens in Neuron's worker setup.
    """
    client = QueueClient()  # handlerless — mirrors a fresh Cortex process
    assert client.get_retry_policy(QUEUE_OPERATIONS, OP_IMPORT_COMMIT) is True


def test_get_retry_policy_still_false_for_canonical_false_op() -> None:
    """A handlerless client also correctly reports non-retryable ops as False."""
    client = QueueClient()
    assert client.get_retry_policy(QUEUE_OPERATIONS, "import_ccx") is False


def test_get_retry_policy_unknown_operation_still_defaults_false() -> None:
    """Operations absent from the canonical table keep the safe False default."""
    client = QueueClient()
    assert client.get_retry_policy(QUEUE_OPERATIONS, "totally_made_up_op") is False


def test_get_retry_policy_in_process_registration_overrides_table() -> None:
    """An explicit in-process registration still wins over the canonical table.

    Registering the SAME value as the canonical table is not a contradiction
    and must be honored from the (now-consistent) in-process registry.
    """
    client = QueueClient()
    client.register_handlers(
        QUEUE_OPERATIONS,
        {OP_IMPORT_COMMIT: HandlerSpec(handler=_ok_handler, retry_on_crash=True)},
    )
    assert client.get_retry_policy(QUEUE_OPERATIONS, OP_IMPORT_COMMIT) is True


# ---------------------------------------------------------------------------
# register_handlers — drift protection
# ---------------------------------------------------------------------------


def test_register_handlers_rejects_contradicting_retry_flag() -> None:
    """Registering a canonical op with the WRONG retry_on_crash fails loudly.

    OP_IMPORT_COMMIT is declared retry_on_crash=True in the canonical
    table; registering it as False (or as a bare callable, which defaults
    to False) is drift and must raise at registration time rather than
    silently changing reconcile behavior.
    """
    client = QueueClient()
    with pytest.raises(TypeError, match=r"retry_on_crash"):
        client.register_handlers(QUEUE_OPERATIONS, {OP_IMPORT_COMMIT: _ok_handler})


def test_register_handlers_rejects_contradicting_handlerspec() -> None:
    """An explicit HandlerSpec that contradicts the table is also rejected."""
    client = QueueClient()
    with pytest.raises(TypeError, match=r"retry_on_crash"):
        client.register_handlers(
            QUEUE_OPERATIONS,
            {OP_IMPORT_COMMIT: HandlerSpec(handler=_ok_handler, retry_on_crash=False)},
        )


def test_register_handlers_accepts_agreeing_retry_flag() -> None:
    """Registering a canonical op with the matching flag succeeds normally."""
    client = QueueClient()
    client.register_handlers(
        QUEUE_OPERATIONS,
        {OP_IMPORT_COMMIT: HandlerSpec(handler=_ok_handler, retry_on_crash=True)},
    )
    assert client.get_handler(QUEUE_OPERATIONS, OP_IMPORT_COMMIT) is _ok_handler


def test_register_handlers_untouched_for_non_canonical_ops() -> None:
    """Ad hoc / test operation names outside the canonical table are unchecked."""
    client = QueueClient()
    # Must not raise — "op_a" isn't in OPERATION_RETRY_ON_CRASH at all.
    client.register_handlers(QUEUE_OPERATIONS, {"op_a": _ok_handler})
    assert client.get_retry_policy(QUEUE_OPERATIONS, "op_a") is False


def test_contradicting_registration_does_not_corrupt_registry() -> None:
    """A rejected batch leaves no partial state, matching the existing
    all-or-nothing registration guarantee.
    """
    client = QueueClient()
    with pytest.raises(TypeError):
        client.register_handlers(
            QUEUE_OPERATIONS,
            {"op_a": _ok_handler, OP_IMPORT_COMMIT: _ok_handler},
        )
    assert client.get_handler(QUEUE_OPERATIONS, "op_a") is None


# ---------------------------------------------------------------------------
# reconcile_queue — end-to-end from a handlerless client (the brief's test)
# ---------------------------------------------------------------------------


def _make_valkey() -> MagicMock:
    """Build a recording fake async Valkey client for the reconciler."""
    valkey = MagicMock()
    valkey.smembers = AsyncMock(return_value={b"task-crashed-commit"})
    valkey.exists = AsyncMock(side_effect=[1, 0])  # hash present, heartbeat gone
    valkey.hgetall = AsyncMock(
        return_value={
            b"operation": OP_IMPORT_COMMIT.encode(),
            b"attempts": b"0",
            b"priority": b"50",
        }
    )
    valkey.hset = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.persist = AsyncMock(return_value=True)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.set = AsyncMock(return_value=True)  # pass lock acquired
    valkey.eval = AsyncMock(return_value=1)  # pass lock released
    return valkey


@pytest.mark.asyncio
async def test_reconcile_requeues_retryable_task_from_handlerless_client() -> None:
    """The regression itself: Cortex-side reconcile on a real, handlerless
    QueueClient must requeue an abandoned import_commit task instead of
    terminally failing it.

    Uses a genuine ``QueueClient()`` — not a MagicMock double for
    ``get_retry_policy`` like the sibling reconciler coverage tests use —
    because the bug lived in what the real method returns when no
    ``register_handlers`` call has ever populated ``_retry_policy``, which
    is exactly the Cortex process shape.
    """
    client = QueueClient()
    client.client = _make_valkey()
    client.requeue_task_atomic = AsyncMock(return_value="__ok__")
    client.mark_task_failed_terminal = AsyncMock(return_value=None)
    client.guarded_status_write = AsyncMock(return_value="__ok__")
    client._reconcile_lock_ttl = 120
    client._failed_result_ttl = 14 * 86_400

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3)

    assert stats.recovered_crashed == 1
    assert stats.failed_unrecoverable == 0
    client.requeue_task_atomic.assert_awaited_once_with(
        QUEUE_OPERATIONS, "task-crashed-commit", 50.0
    )
    client.guarded_status_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_still_fails_nonretryable_task_from_handlerless_client() -> None:
    """Sanity check: a genuinely non-retryable op is still terminally failed
    by a handlerless client — the fix must not make everything retryable.
    """
    client = QueueClient()
    client.client = _make_valkey()
    client.client.hgetall = AsyncMock(
        return_value={
            b"operation": b"import_ccx",  # canonical False
            b"attempts": b"0",
            b"priority": b"50",
        }
    )
    client.requeue_task_atomic = AsyncMock(return_value="__ok__")
    client.mark_task_failed_terminal = AsyncMock(return_value=None)
    client.guarded_status_write = AsyncMock(return_value="__ok__")
    client._reconcile_lock_ttl = 120
    client._failed_result_ttl = 14 * 86_400

    stats = await reconcile_queue(client, QUEUE_OPERATIONS, max_tries=3)

    assert stats.recovered_crashed == 0
    assert stats.failed_unrecoverable == 1
    client.requeue_task_atomic.assert_not_awaited()
    client.guarded_status_write.assert_awaited_once()
