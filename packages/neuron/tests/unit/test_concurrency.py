# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Concurrency-contract tests for the two queues.

QUEUE_LLM is configured concurrency=1 (serial).
QUEUE_OPERATIONS is configured concurrency=8 (parallel up to limit).

The two config tests call load_worker_config with the canonical worker-type
keys ("llm_worker" / "operations_worker") and patch the path-settings so no
real /data/workers.yaml is needed.  The five dispatch tests drive the real
``QueueWorker._poll_queue`` loop over a stateful fake Valkey client, so the
concurrency invariants are pinned against the worker's own semaphore wiring
(``config["concurrency"]`` → acquire-before-pop → release-in-finally), not
against a bare ``asyncio.Semaphore`` reconstruction of it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import chaoscypher_neuron.worker  # noqa: F401 — configures structlog at import time
from chaoscypher_core.queue.worker import QueueWorker
from chaoscypher_neuron.config import load_worker_config


# ---------------------------------------------------------------------------
# Helpers (mirrors test_config.py's patching approach)
# ---------------------------------------------------------------------------


def _make_defaults() -> dict:
    return {
        "llm_worker": {
            "max_concurrent": 1,
            "queue_name": "llm",
            "timeout": 3600,
            "max_tries": 5,
        },
        "operations_worker": {
            "max_concurrent": 8,
            "queue_name": "operations",
            "timeout": 3600,
            "max_tries": 5,
        },
    }


def _mock_path_settings(data_dir: str) -> MagicMock:
    mock_ps = MagicMock()
    mock_ps.data_dir = data_dir
    mock_ps.workers_config_filename = "workers.yaml"
    return mock_ps


def test_llm_queue_config_concurrency_is_one(tmp_path: Path) -> None:
    """load_worker_config('llm_worker') returns max_concurrent == 1."""
    defaults = _make_defaults()
    mock_ps = _mock_path_settings(str(tmp_path))

    with (
        patch("chaoscypher_neuron.config._get_defaults", return_value=defaults),
        patch("chaoscypher_core.app_config.PathSettings", return_value=mock_ps),
    ):
        cfg = load_worker_config("llm_worker")

    assert cfg["max_concurrent"] == 1


def test_operations_queue_config_concurrency_is_eight(tmp_path: Path) -> None:
    """load_worker_config('operations_worker') returns max_concurrent == 8."""
    defaults = _make_defaults()
    mock_ps = _mock_path_settings(str(tmp_path))

    with (
        patch("chaoscypher_neuron.config._get_defaults", return_value=defaults),
        patch("chaoscypher_core.app_config.PathSettings", return_value=mock_ps),
    ):
        cfg = load_worker_config("operations_worker")

    assert cfg["max_concurrent"] == 8


# ---------------------------------------------------------------------------
# QueueWorker dispatch harness (fake-Valkey pattern from
# core/tests/unit/queue/test_worker_process_loop_coverage.py, made stateful:
# zpopmax pops preloaded task IDs and hgetall serves a per-task hash)
# ---------------------------------------------------------------------------

_QUEUE = "operations"


def _task_hash(operation: str) -> dict[bytes, bytes]:
    return {
        b"operation": operation.encode(),
        b"data": json.dumps({}).encode(),
        b"metadata": b"{}",
        b"result_ttl": b"3600",
        b"attempts": b"0",
        b"payload_version": b"1",
        b"priority": b"50",
    }


def _make_stateful_valkey(tasks: dict[str, str]) -> MagicMock:
    """Fake Valkey preloaded with ``{task_id: operation}`` pending tasks."""
    pending = list(tasks)
    valkey = MagicMock()

    async def zpopmax(key: str, count: int = 1) -> list[tuple[bytes, float]]:
        if pending:
            return [(pending.pop(0).encode(), 50.0)]
        return []

    async def hgetall(key: str) -> dict[bytes, bytes]:
        task_id = key.rsplit(":", 1)[-1]
        return _task_hash(tasks[task_id])

    valkey.zpopmax = zpopmax
    valkey.hgetall = hgetall
    valkey.hget = AsyncMock(return_value=None)  # no retry_after backoff
    valkey.hset = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.setex = AsyncMock(return_value=True)
    valkey.persist = AsyncMock(return_value=True)
    valkey.sadd = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.expire = AsyncMock(return_value=True)
    valkey.delete = AsyncMock(return_value=1)
    return valkey


def _make_worker(
    tasks: dict[str, str],
    handlers: dict[str, Any],
    *,
    concurrency: int,
) -> QueueWorker:
    return QueueWorker(
        client=_make_stateful_valkey(tasks),
        queues_config={_QUEUE: {"concurrency": concurrency, "max_tries": 1, "timeout": 30}},
        handlers={_QUEUE: handlers},
        poll_interval=0.0,
        semaphore_acquire_timeout=0.01,
        poller_error_delay=0.0,
    )


async def _run_until(worker: QueueWorker, done: asyncio.Event, timeout: float = 10.0) -> None:
    """Drive the real _poll_queue loop until ``done`` fires, then drain."""
    worker._running = True
    poller = asyncio.create_task(worker._poll_queue(_QUEUE, worker.queues_config[_QUEUE]))
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    finally:
        worker._running = False
        await asyncio.wait_for(poller, timeout=timeout)
        await worker._drain_active_tasks(timeout=timeout)


class _ConcurrencyProbe:
    """Handler that records in-flight overlap across dispatched tasks."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.in_flight = 0
        self.max_in_flight = 0
        self.completed: list[str] = []
        self.all_done = asyncio.Event()

    async def __call__(self, data: dict, *, metadata: dict, task_id: str) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        # Yield repeatedly so the poller has every chance to dispatch
        # another task while this one is mid-flight.
        for _ in range(5):
            await asyncio.sleep(0.01)
        self.in_flight -= 1
        self.completed.append(task_id)
        if len(self.completed) == self.total:
            self.all_done.set()
        return "ok"


@pytest.mark.asyncio
async def test_worker_concurrency_one_serializes_two_handlers() -> None:
    """With config concurrency=1 the worker never overlaps two handlers."""
    probe = _ConcurrencyProbe(total=2)
    worker = _make_worker(
        {"t-a": "op", "t-b": "op"},
        {"op": probe},
        concurrency=1,
    )

    await _run_until(worker, probe.all_done)

    assert probe.completed == ["t-a", "t-b"]
    assert probe.max_in_flight == 1, (
        f"concurrency=1 must serialize dispatch; observed {probe.max_in_flight} in flight"
    )


@pytest.mark.asyncio
async def test_worker_concurrency_eight_allows_eight_parallel() -> None:
    """With config concurrency=8 the worker gets all 8 tasks in flight at once.

    Each handler blocks until all 8 have started: if the worker's semaphore
    were narrower than the configured concurrency, the last task could never
    start and the test would time out.
    """
    started = 0
    all_started = asyncio.Event()
    all_done = asyncio.Event()
    completed: list[str] = []

    async def handler(data: dict, *, metadata: dict, task_id: str) -> str:
        nonlocal started
        started += 1
        if started == 8:
            all_started.set()
        await all_started.wait()
        completed.append(task_id)
        if len(completed) == 8:
            all_done.set()
        return "ok"

    worker = _make_worker(
        {f"t-{i}": "op" for i in range(8)},
        {"op": handler},
        concurrency=8,
    )

    await _run_until(worker, all_done)

    assert started == 8
    assert len(completed) == 8


@pytest.mark.asyncio
async def test_worker_concurrency_eight_caps_ninth_task() -> None:
    """With 16 pending tasks and concurrency=8, the worker holds at 8 in flight."""
    started = 0
    eight_started = asyncio.Event()
    release = asyncio.Event()
    all_done = asyncio.Event()
    completed: list[str] = []

    async def handler(data: dict, *, metadata: dict, task_id: str) -> str:
        nonlocal started
        started += 1
        if started == 8:
            eight_started.set()
        await release.wait()
        completed.append(task_id)
        if len(completed) == 16:
            all_done.set()
        return "ok"

    worker = _make_worker(
        {f"t-{i}": "op" for i in range(16)},
        {"op": handler},
        concurrency=8,
    )

    worker._running = True
    poller = asyncio.create_task(worker._poll_queue(_QUEUE, worker.queues_config[_QUEUE]))
    try:
        await asyncio.wait_for(eight_started.wait(), timeout=10.0)
        # Give the poller ample loop iterations to (incorrectly) dispatch a
        # ninth task; every slot is held, so the count must stay at 8.
        await asyncio.sleep(0.1)
        assert started == 8, f"Concurrency cap broken: {started} tasks started"
        release.set()
        await asyncio.wait_for(all_done.wait(), timeout=10.0)
    finally:
        worker._running = False
        await asyncio.wait_for(poller, timeout=10.0)
        await worker._drain_active_tasks(timeout=10.0)

    assert len(completed) == 16


@pytest.mark.asyncio
async def test_worker_failing_handler_frees_slot_for_next_task() -> None:
    """A handler that raises releases its slot; queued tasks still dispatch.

    concurrency=1 makes the invariant sharp: if the failing task leaked its
    slot, nothing after it could ever start.
    """
    good_done = asyncio.Event()
    completed: list[str] = []
    failed: list[str] = []

    async def bad(data: dict, *, metadata: dict, task_id: str) -> str:
        failed.append(task_id)
        raise RuntimeError("intentional")

    async def good(data: dict, *, metadata: dict, task_id: str) -> str:
        completed.append(task_id)
        if len(completed) == 2:
            good_done.set()
        return "ok"

    worker = _make_worker(
        {"t-bad-1": "bad_op", "t-bad-2": "bad_op", "t-good-1": "good_op", "t-good-2": "good_op"},
        {"bad_op": bad, "good_op": good},
        concurrency=1,
    )

    await _run_until(worker, good_done)

    assert failed == ["t-bad-1", "t-bad-2"]
    assert completed == ["t-good-1", "t-good-2"]


@pytest.mark.asyncio
async def test_worker_dispatches_in_pop_order_without_starvation() -> None:
    """Tasks complete in queue-pop order under serial dispatch — none starved."""
    probe = _ConcurrencyProbe(total=12)
    task_ids = [f"t-{i:02d}" for i in range(12)]
    worker = _make_worker(
        dict.fromkeys(task_ids, "op"),
        {"op": probe},
        concurrency=1,
    )

    await _run_until(worker, probe.all_done)

    assert probe.completed == task_ids, (
        f"Serial dispatch must preserve queue-pop order; got {probe.completed}"
    )
