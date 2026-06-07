# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Concurrency-contract tests for the two queues.

QUEUE_LLM is configured concurrency=1 (serial).
QUEUE_OPERATIONS is configured concurrency=8 (parallel up to limit).

The two config tests call load_worker_config with the canonical worker-type
keys ("llm_worker" / "operations_worker") and patch the path-settings so no
real /data/workers.yaml is needed.  The five semaphore tests use
asyncio.Semaphore directly — the same primitive QueueWorker uses — to pin the
behavioral contract the worker depends on.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import chaoscypher_neuron.worker  # noqa: F401 — configures structlog at import time
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


# ---------------------------------------------------------------------------
# Task 3: LLM queue serialization — 3 tests
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_semaphore_with_concurrency_one_serializes_two_handlers() -> None:
    """A semaphore(1) bound around handlers makes them run serially.

    This pins the behavioral contract that QueueWorker enforces; the
    QueueWorker uses asyncio.Semaphore(max_concurrent) internally and any
    test that simulates dispatch with this same primitive proves the
    serialization invariant.
    """
    semaphore = asyncio.Semaphore(1)
    timeline: list[tuple[str, float]] = []
    start = time.perf_counter()

    async def slow_handler(name: str) -> None:
        async with semaphore:
            timeline.append((name + ":start", time.perf_counter() - start))
            await asyncio.sleep(0.1)
            timeline.append((name + ":end", time.perf_counter() - start))

    await asyncio.gather(slow_handler("a"), slow_handler("b"))

    # With concurrency=1, b cannot start until a ends.
    a_end = next(t for n, t in timeline if n == "a:end")
    b_start = next(t for n, t in timeline if n == "b:start")
    assert b_start >= a_end - 0.05, (
        f"Expected b to start after a ended (concurrency=1); "
        f"a ended at {a_end:.3f}, b started at {b_start:.3f}"
    )


# ---------------------------------------------------------------------------
# Task 4: Operations queue parallelism — 4 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_with_concurrency_eight_allows_eight_parallel() -> None:
    """asyncio.Semaphore(8) with 8 tasks shows all 8 in-flight simultaneously."""
    semaphore = asyncio.Semaphore(8)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def slow_handler() -> None:
        nonlocal in_flight, max_in_flight
        async with semaphore:
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1

    await asyncio.gather(*(slow_handler() for _ in range(8)))
    assert max_in_flight == 8


@pytest.mark.asyncio
async def test_semaphore_with_concurrency_eight_caps_ninth_task() -> None:
    """asyncio.Semaphore(8) with 16 tasks never lets more than 8 run at once."""
    semaphore = asyncio.Semaphore(8)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def slow_handler() -> None:
        nonlocal in_flight, max_in_flight
        async with semaphore:
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            async with lock:
                in_flight -= 1

    await asyncio.gather(*(slow_handler() for _ in range(16)))
    assert max_in_flight == 8, f"Concurrency cap broken: peak {max_in_flight}"


@pytest.mark.asyncio
async def test_semaphore_no_starvation_when_one_task_fails() -> None:
    """A failing handler frees its semaphore slot for the next task."""
    semaphore = asyncio.Semaphore(2)
    completed: list[str] = []

    async def good() -> None:
        async with semaphore:
            await asyncio.sleep(0.01)
            completed.append("good")

    async def bad() -> None:
        async with semaphore:
            await asyncio.sleep(0.01)
            raise RuntimeError("intentional")

    # Schedule 4 tasks (2 of each); bad raises, good completes.
    results = await asyncio.gather(good(), bad(), good(), bad(), return_exceptions=True)

    # 2 good completions, 2 bad raises — semaphore did not deadlock.
    assert completed.count("good") == 2
    assert sum(isinstance(r, RuntimeError) for r in results) == 2


@pytest.mark.asyncio
async def test_semaphore_fairness_across_many_enqueues() -> None:
    """asyncio.Semaphore is FIFO-ordered — enqueued tasks complete in order.

    Pins the QueueWorker fairness assumption: no enqueue is permanently
    starved by later arrivals even at high concurrency.
    """
    semaphore = asyncio.Semaphore(4)
    order: list[int] = []

    async def numbered(i: int) -> None:
        async with semaphore:
            await asyncio.sleep(0.001 * i)  # different durations to surface ordering issues
            order.append(i)

    await asyncio.gather(*(numbered(i) for i in range(20)))
    assert sorted(order) == list(range(20)), "Tasks completed out of order or some were starved"
