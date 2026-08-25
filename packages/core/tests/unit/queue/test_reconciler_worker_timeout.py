# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The reconciler's absolute cutoff must track the worker's *effective* timeout.

``reconcile_queue``'s absolute-timeout branch is deliberately heartbeat-blind
(an event-loop hang keeps the heartbeat firing), and its requeue path —
``requeue_atomic.lua`` — refuses only ``completed``/``cancelled``: a ``running``
task is happily reset to ``queued`` and handed to a second worker. So any cutoff
shorter than the deadline the worker actually enforces is duplicate dispatch.

The worker's real deadline is the ``/data/workers.yaml`` override (clamped),
not ``TimeoutSettings.*_worker_default``. These tests pin that resolution and
the safety margin that keeps the reconciler from racing the worker's own
``asyncio.wait_for`` at the exact instant it fires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core import policy
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.queue.reconciler import reconcile_queue
from chaoscypher_core.queue.worker_timeouts import (
    RECONCILER_SAFETY_MARGIN_SECONDS,
    WorkersConfigUnreadableError,
    read_workers_yaml_timeout,
    reconciler_cutoff_seconds,
    resolve_effective_worker_timeout,
)


_SETTINGS_DEFAULT = 3600
_RAISED_TIMEOUT = 7200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_workers_yaml(data_dir: Path, body: str) -> None:
    """Write a ``workers.yaml`` into a fake data dir.

    Args:
        data_dir: Directory that ``PathSettings.data_dir`` will point at.
        body: Raw YAML text for the file.
    """
    (data_dir / "workers.yaml").write_text(body, encoding="utf-8")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``PathSettings.data_dir`` at an empty temp directory."""
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    return tmp_path


def _running_task_client(*, started_seconds_ago: int) -> MagicMock:
    """Build a fake QueueClient holding one live ``running`` task.

    The task's heartbeat is alive and its status is ``running`` — exactly the
    shape a healthy long task presents to the reconciler.

    Args:
        started_seconds_ago: Age of the task's ``started_at`` field.
    """
    started_at = (datetime.now(UTC) - timedelta(seconds=started_seconds_ago)).isoformat()
    task_hash = {
        "operation": "extract_chunk",
        "attempts": "1",
        "priority": "50",
        "status": "running",
        "started_at": started_at,
    }

    valkey = MagicMock()
    valkey.smembers = AsyncMock(return_value={b"live-task"})
    valkey.exists = AsyncMock(return_value=1)  # hash AND heartbeat both present
    valkey.hgetall = AsyncMock(return_value={k.encode(): v.encode() for k, v in task_hash.items()})
    valkey.hset = AsyncMock(return_value=1)
    valkey.hincrby = AsyncMock(return_value=1)
    valkey.srem = AsyncMock(return_value=1)
    valkey.persist = AsyncMock(return_value=True)
    valkey.zadd = AsyncMock(return_value=1)
    valkey.set = AsyncMock(return_value=True)
    valkey.eval = AsyncMock(return_value=1)

    client = MagicMock()
    client.client = valkey
    client.get_retry_policy = MagicMock(return_value=True)
    client.requeue_task_atomic = AsyncMock(return_value="__ok__")
    client.guarded_status_write = AsyncMock(return_value="__ok__")
    client._reconcile_lock_ttl = 120
    client.failed_result_ttl = 14 * 86_400
    return client


# ---------------------------------------------------------------------------
# resolve_effective_worker_timeout
# ---------------------------------------------------------------------------


def test_no_workers_yaml_falls_back_to_settings_default(data_dir: Path) -> None:
    """With no ``workers.yaml`` present, the settings default is the deadline."""
    assert resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT) == (
        _SETTINGS_DEFAULT
    )


def test_raised_workers_yaml_timeout_wins_over_settings_default(data_dir: Path) -> None:
    """An operator's raised ``workers.yaml`` timeout is the worker's real deadline."""
    _write_workers_yaml(data_dir, f"llm_worker:\n  timeout: {_RAISED_TIMEOUT}\n")

    assert resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT) == (
        _RAISED_TIMEOUT
    )


def test_per_queue_overrides_are_independent(data_dir: Path) -> None:
    """Raising the LLM worker's timeout leaves the operations worker's alone."""
    _write_workers_yaml(data_dir, f"llm_worker:\n  timeout: {_RAISED_TIMEOUT}\n")

    assert resolve_effective_worker_timeout(QUEUE_OPERATIONS, default=_SETTINGS_DEFAULT) == (
        _SETTINGS_DEFAULT
    )


def test_override_is_clamped_to_the_worker_ceiling(data_dir: Path) -> None:
    """An absurd override clamps to the same ceiling Neuron's loader uses.

    ``load_worker_config`` clamps with the same ``policy.WORKER_TIMEOUT_*``
    bounds — that shared constant is what keeps the reconciler's cutoff outside
    the worker's live window.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  timeout: 999999999\n")

    assert resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT) == (
        policy.WORKER_TIMEOUT_MAX_SECONDS
    )


def test_override_is_clamped_to_the_worker_floor(data_dir: Path) -> None:
    """A sub-floor override clamps up to the worker's minimum.

    Uses a ``default`` below the clamp floor so the clamp is observable — with a
    realistic default the settings floor (see the next test) would mask it.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  timeout: 1\n")

    assert resolve_effective_worker_timeout(QUEUE_LLM, default=30) == (
        policy.WORKER_TIMEOUT_MIN_SECONDS
    )


def test_override_lowered_below_default_never_shortens_the_cutoff(data_dir: Path) -> None:
    """An operator reverting the timeout must not pull the cutoff below the default.

    The file is read live; a worker's deadline is pinned at process start. So an
    operator who lowers ``workers.yaml`` without restarting workers leaves the
    worker enforcing the OLD value while this resolver sees the new one. Without
    a floor at the settings default, a revert to something below it would make
    the cutoff shorter than the pre-fix cutoff — re-arming the exact
    duplicate-dispatch defect this module exists to close, and doing it worse
    than before the fix.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  timeout: 1800\n")

    resolved = resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT)

    assert resolved == _SETTINGS_DEFAULT
    assert reconciler_cutoff_seconds(resolved) == (
        _SETTINGS_DEFAULT + RECONCILER_SAFETY_MARGIN_SECONDS
    )


@pytest.mark.parametrize(
    "body",
    [
        "llm_worker:\n  timeout: true\n",  # YAML bool coerces to 1 — must be rejected
        "llm_worker:\n  timeout: forever\n",
        "llm_worker:\n  max_concurrent: 4\n",  # no timeout key
        "llm_worker: not-a-mapping\n",
        "operations_worker:\n  timeout: 600\n",  # different worker
        "",  # empty file
    ],
)
def test_unusable_override_falls_back_to_the_settings_default(data_dir: Path, body: str) -> None:
    """A readable file with no usable override means the worker booted on its default.

    These shapes are *informative*: Neuron's loader rejects them the same way and
    falls back to the default, so we know what the worker is enforcing. Contrast
    with an unreadable file, which tells us nothing — see the ceiling tests.
    """
    _write_workers_yaml(data_dir, body)

    assert resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT) == (
        _SETTINGS_DEFAULT
    )


def test_unparseable_workers_yaml_fails_to_the_clamp_ceiling(data_dir: Path) -> None:
    """Malformed YAML tells us nothing about the worker's deadline — fail LONG.

    Falling back to the settings default here would requeue live work precisely
    when we are least able to detect that we are doing it.
    """
    _write_workers_yaml(data_dir, "{{{ not yaml\n")

    assert resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT) == (
        policy.WORKER_TIMEOUT_MAX_SECONDS
    )


def test_unopenable_workers_yaml_fails_to_the_clamp_ceiling(data_dir: Path) -> None:
    """A present-but-unopenable ``workers.yaml`` also fails to the ceiling.

    Models the split-container case: Neuron read the file at startup, Cortex
    cannot read it now (permissions, mount glitch). A directory standing in for
    the file reproduces it cross-platform — ``exists()`` is True and ``open()``
    raises OSError on both Windows and POSIX.
    """
    (data_dir / "workers.yaml").mkdir()

    assert resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT) == (
        policy.WORKER_TIMEOUT_MAX_SECONDS
    )


def test_unreadable_workers_yaml_raises_from_the_reader(data_dir: Path) -> None:
    """The reader distinguishes "no override" from "cannot tell" by raising."""
    _write_workers_yaml(data_dir, "{{{ not yaml\n")

    with pytest.raises(WorkersConfigUnreadableError):
        read_workers_yaml_timeout("llm_worker")


def test_unknown_queue_uses_the_supplied_default(data_dir: Path) -> None:
    """A queue with no worker-type mapping falls back to the caller's default."""
    _write_workers_yaml(data_dir, f"llm_worker:\n  timeout: {_RAISED_TIMEOUT}\n")

    assert resolve_effective_worker_timeout("nonexistent", default=_SETTINGS_DEFAULT) == (
        _SETTINGS_DEFAULT
    )


# ---------------------------------------------------------------------------
# reconciler_cutoff_seconds
# ---------------------------------------------------------------------------


def test_cutoff_adds_the_safety_margin() -> None:
    """The cutoff sits a margin BEYOND the worker's deadline, never on top of it."""
    assert reconciler_cutoff_seconds(_RAISED_TIMEOUT) == (
        _RAISED_TIMEOUT + RECONCILER_SAFETY_MARGIN_SECONDS
    )
    assert RECONCILER_SAFETY_MARGIN_SECONDS > 0


def test_cutoff_passes_none_through() -> None:
    """``None`` disables the absolute bound and must survive the helper untouched."""
    assert reconciler_cutoff_seconds(None) is None


# ---------------------------------------------------------------------------
# reconcile_queue driven by the effective cutoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_task_inside_the_raised_deadline_is_not_requeued(data_dir: Path) -> None:
    """The regression: a task past the SETTINGS default but inside the operator's
    raised ``workers.yaml`` deadline is still running — it must not be requeued.

    Before the fix, Cortex's safety net derived its cutoff from
    ``settings.timeouts.llm_worker_default``, so at 3600s+1 it reset a live task
    to ``queued`` (``requeue_atomic.lua`` only refuses completed/cancelled) and a
    second worker ran the same handler concurrently.
    """
    _write_workers_yaml(data_dir, f"llm_worker:\n  timeout: {_RAISED_TIMEOUT}\n")
    client = _running_task_client(started_seconds_ago=_SETTINGS_DEFAULT + 60)

    stats = await reconcile_queue(
        client,
        QUEUE_LLM,
        max_tries=5,
        timeout_seconds=reconciler_cutoff_seconds(
            resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        ),
    )

    assert stats.total() == 0, "a still-running task was recovered by the reconciler"
    client.requeue_task_atomic.assert_not_awaited()
    client.client.srem.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverted_override_does_not_requeue_inside_the_prefix_window(
    data_dir: Path,
) -> None:
    """A lowered ``workers.yaml`` must not make the reconciler MORE aggressive.

    The worker still enforces whatever it booted with; only the file changed.
    Reading the reverted 1800 literally would give a 2100s cutoff and requeue
    this 2400s-old live task — something even the pre-fix 3600s cutoff would not
    have done. The floor at the settings default keeps the cutoff at 3900.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  timeout: 1800\n")
    client = _running_task_client(started_seconds_ago=2400)

    stats = await reconcile_queue(
        client,
        QUEUE_LLM,
        max_tries=5,
        timeout_seconds=reconciler_cutoff_seconds(
            resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        ),
    )

    assert stats.total() == 0, "a lowered workers.yaml made the reconciler requeue live work"
    client.requeue_task_atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_workers_yaml_does_not_requeue_a_long_live_task(data_dir: Path) -> None:
    """With the file unreadable, the ceiling keeps a very old live task untouched.

    The worker could legally be enforcing anything up to the clamp ceiling, so
    the reconciler must not act on a task merely because it outran the settings
    default.
    """
    (data_dir / "workers.yaml").mkdir()
    client = _running_task_client(started_seconds_ago=_RAISED_TIMEOUT * 2)

    stats = await reconcile_queue(
        client,
        QUEUE_LLM,
        max_tries=5,
        timeout_seconds=reconciler_cutoff_seconds(
            resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        ),
    )

    assert stats.total() == 0
    client.requeue_task_atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_task_at_the_deadline_survives_the_safety_margin(data_dir: Path) -> None:
    """A task that just hit its deadline is inside the margin — the worker's own
    ``asyncio.wait_for`` is firing right now and owns the failure path.
    """
    _write_workers_yaml(data_dir, f"llm_worker:\n  timeout: {_RAISED_TIMEOUT}\n")
    client = _running_task_client(started_seconds_ago=_RAISED_TIMEOUT + 5)

    stats = await reconcile_queue(
        client,
        QUEUE_LLM,
        max_tries=5,
        timeout_seconds=reconciler_cutoff_seconds(
            resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        ),
    )

    assert stats.total() == 0
    client.requeue_task_atomic.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_older_than_the_effective_deadline_is_still_requeued(data_dir: Path) -> None:
    """The branch stays heartbeat-blind: past the effective deadline + margin a
    task IS recovered even though its heartbeat is alive (event-loop hang).
    """
    _write_workers_yaml(data_dir, f"llm_worker:\n  timeout: {_RAISED_TIMEOUT}\n")
    client = _running_task_client(
        started_seconds_ago=_RAISED_TIMEOUT + RECONCILER_SAFETY_MARGIN_SECONDS + 60
    )

    stats = await reconcile_queue(
        client,
        QUEUE_LLM,
        max_tries=5,
        timeout_seconds=reconciler_cutoff_seconds(
            resolve_effective_worker_timeout(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        ),
    )

    assert stats.recovered_crashed == 1
    client.requeue_task_atomic.assert_awaited_once()
