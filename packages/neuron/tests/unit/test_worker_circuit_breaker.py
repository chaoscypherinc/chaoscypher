# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``_run_worker_with_circuit_breaker``.

The wrapper protects ``run_worker()`` from a poison-pill crash that
would otherwise leave recovery to the container's restart policy and
produce a CPU-burning tight loop. These tests pin three invariants:

1. A clean return from ``run_worker()`` exits the loop immediately.
2. Transient failures retry with exponentially-growing backoff that
   caps at ``run_worker_max_backoff_seconds``.
3. After ``run_worker_max_consecutive_failures`` consecutive failures
   the breaker re-raises so the container restart takes over.

``asyncio.sleep`` is patched throughout so the tests are instant. The
neuron settings cache is invalidated per test to keep parametrised
overrides isolated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_neuron import worker as worker_module
from chaoscypher_neuron.config import NeuronSettings, get_neuron_settings


# ============================================================================
# Helpers
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_neuron_settings_cache() -> None:
    """Clear the ``functools.cache`` on :func:`get_neuron_settings`.

    Each test patches ``NeuronSettings`` fields with different values, so
    the cached singleton must be invalidated between tests to ensure the
    new values are picked up. The fixture runs before AND after each test
    so a stale entry from a previous run cannot leak.
    """
    get_neuron_settings.cache_clear()
    yield
    get_neuron_settings.cache_clear()


def _patched_settings(
    *,
    max_failures: int = 3,
    initial_backoff: float = 1.0,
    max_backoff: float = 8.0,
):
    """Return a ``patch`` ctx that replaces ``get_neuron_settings`` in worker.

    The wrapper imports :func:`get_neuron_settings` lazily inside the
    function body, so we patch the symbol in its origin module —
    ``chaoscypher_neuron.config`` — and the lazy import resolves through
    that patched binding.
    """
    overrides = NeuronSettings(
        run_worker_max_consecutive_failures=max_failures,
        run_worker_initial_backoff_seconds=initial_backoff,
        run_worker_max_backoff_seconds=max_backoff,
    )
    return patch(
        "chaoscypher_neuron.config.get_neuron_settings",
        return_value=overrides,
    )


# ============================================================================
# Tests
# ============================================================================


@pytest.mark.asyncio
async def test_clean_return_exits_loop_without_retry() -> None:
    """A clean return from ``run_worker()`` exits the breaker immediately."""
    fake_run_worker = AsyncMock(return_value=None)
    fake_sleep = AsyncMock()

    with (
        _patched_settings(),
        patch.object(worker_module, "run_worker", fake_run_worker),
        patch.object(worker_module.asyncio, "sleep", fake_sleep),
    ):
        await worker_module._run_worker_with_circuit_breaker()

    assert fake_run_worker.await_count == 1
    assert fake_sleep.await_count == 0


@pytest.mark.asyncio
async def test_retries_on_exception_then_succeeds() -> None:
    """Transient failures retry; a clean return ends the loop."""
    fake_run_worker = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom"), None])
    fake_sleep = AsyncMock()

    with (
        _patched_settings(max_failures=5, initial_backoff=2.0, max_backoff=60.0),
        patch.object(worker_module, "run_worker", fake_run_worker),
        patch.object(worker_module.asyncio, "sleep", fake_sleep),
    ):
        await worker_module._run_worker_with_circuit_breaker()

    assert fake_run_worker.await_count == 3
    # Two failures → two backoff sleeps; the third call returns cleanly.
    assert fake_sleep.await_count == 2


@pytest.mark.asyncio
async def test_backoff_grows_exponentially_and_caps() -> None:
    """Each retry doubles the backoff up to ``max_backoff``."""
    failures = [RuntimeError(f"boom_{i}") for i in range(5)]
    fake_run_worker = AsyncMock(side_effect=[*failures, None])
    fake_sleep = AsyncMock()

    with (
        _patched_settings(max_failures=10, initial_backoff=1.0, max_backoff=8.0),
        patch.object(worker_module, "run_worker", fake_run_worker),
        patch.object(worker_module.asyncio, "sleep", fake_sleep),
    ):
        await worker_module._run_worker_with_circuit_breaker()

    # 1, 2, 4, 8, 8 — fifth attempt is capped at max_backoff.
    sleep_durations = [call.args[0] for call in fake_sleep.await_args_list]
    assert sleep_durations == [1.0, 2.0, 4.0, 8.0, 8.0]


@pytest.mark.asyncio
async def test_reraises_after_max_consecutive_failures() -> None:
    """Once ``max_failures`` is hit, the wrapper re-raises."""

    class PoisonPillError(RuntimeError):
        """Distinct type so the test's ``pytest.raises`` is precise."""

    # 100 entries so we can't accidentally consume the side-effect list.
    fake_run_worker = AsyncMock(side_effect=[PoisonPillError("poison") for _ in range(100)])
    fake_sleep = AsyncMock()

    with (
        _patched_settings(max_failures=3, initial_backoff=1.0, max_backoff=60.0),
        patch.object(worker_module, "run_worker", fake_run_worker),
        patch.object(worker_module.asyncio, "sleep", fake_sleep),
        pytest.raises(PoisonPillError, match="poison"),
    ):
        await worker_module._run_worker_with_circuit_breaker()

    # max_failures=3 means: attempt, sleep, attempt, sleep, attempt → raise.
    assert fake_run_worker.await_count == 3
    # We sleep BEFORE each retry, never after the final raise.
    assert fake_sleep.await_count == 2


@pytest.mark.asyncio
async def test_keyboard_interrupt_propagates_immediately() -> None:
    """KeyboardInterrupt is not an ``Exception`` — propagates without retry.

    ``BaseException`` subclasses (KeyboardInterrupt, SystemExit, the
    asyncio CancelledError lineage) are NOT swallowed by the breaker's
    ``except Exception`` clause. This keeps Ctrl-C and supervisor-issued
    shutdowns crisp: no extra retry, no extra backoff sleep.
    """
    fake_run_worker = AsyncMock(side_effect=KeyboardInterrupt)
    fake_sleep = AsyncMock()

    with (
        _patched_settings(),
        patch.object(worker_module, "run_worker", fake_run_worker),
        patch.object(worker_module.asyncio, "sleep", fake_sleep),
        pytest.raises(KeyboardInterrupt),
    ):
        await worker_module._run_worker_with_circuit_breaker()

    assert fake_run_worker.await_count == 1
    assert fake_sleep.await_count == 0


@pytest.mark.asyncio
async def test_failure_counter_does_not_reset_on_partial_progress() -> None:
    """Consecutive-failure counter accumulates across distinct exceptions.

    The wrapper has no "I'm healthy" signal short of ``run_worker()``
    returning cleanly. Different exception types must all count toward
    the same ceiling.
    """
    fake_run_worker = AsyncMock(
        side_effect=[
            ValueError("first"),
            RuntimeError("second"),
            OSError("third"),
        ]
    )
    fake_sleep = AsyncMock()

    with (
        _patched_settings(max_failures=3, initial_backoff=1.0, max_backoff=60.0),
        patch.object(worker_module, "run_worker", fake_run_worker),
        patch.object(worker_module.asyncio, "sleep", fake_sleep),
        pytest.raises(OSError, match="third"),
    ):
        await worker_module._run_worker_with_circuit_breaker()

    assert fake_run_worker.await_count == 3
