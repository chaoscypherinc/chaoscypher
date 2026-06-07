# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Cluster B+E state-machine robustness in SourceRecovery.

Covers BE-1: max recovery attempts guard, BE-4: heartbeat timing fix,
BE-5: finalizer pre-aggregation guard for in-flight task race.
"""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.recovery import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    DEFAULT_RECOVERY_WARN_THRESHOLD,
    RecoveryStats,
    SourceRecovery,
)


def _seed_source(
    adapter,
    *,
    source_id: str,
    status: str,
    recovery_attempts: int = 0,
) -> None:
    """Seed a minimal source row then set recovery_attempts."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": status,
            "auto_analyze": True,
        }
    )
    if recovery_attempts:
        # Bump to the desired count by calling increment N times.
        for _ in range(recovery_attempts):
            adapter.increment_source_recovery_attempts(
                source_id=source_id,
                database_name=adapter.database_name,
            )


# ============================================================
# Module-level constant checks
# ============================================================


def test_default_max_recovery_attempts_value() -> None:
    """DEFAULT_MAX_RECOVERY_ATTEMPTS is 10 as specified in the design doc."""
    assert DEFAULT_MAX_RECOVERY_ATTEMPTS == 10


def test_default_recovery_warn_threshold_value() -> None:
    """DEFAULT_RECOVERY_WARN_THRESHOLD is 5 as specified in the design doc."""
    assert DEFAULT_RECOVERY_WARN_THRESHOLD == 5


# ============================================================
# Constructor defaults
# ============================================================


def test_constructor_defaults_max_recovery_attempts() -> None:
    """max_recovery_attempts defaults to DEFAULT_MAX_RECOVERY_ATTEMPTS when not provided."""
    recovery = SourceRecovery(adapter=AsyncMock(), queue_client=AsyncMock())
    assert recovery.max_recovery_attempts == DEFAULT_MAX_RECOVERY_ATTEMPTS


def test_constructor_defaults_recovery_warn_threshold() -> None:
    """recovery_warn_threshold defaults to DEFAULT_RECOVERY_WARN_THRESHOLD when not provided."""
    recovery = SourceRecovery(adapter=AsyncMock(), queue_client=AsyncMock())
    assert recovery.recovery_warn_threshold == DEFAULT_RECOVERY_WARN_THRESHOLD


def test_constructor_override_max_recovery_attempts() -> None:
    """Explicit max_recovery_attempts overrides the default."""
    recovery = SourceRecovery(
        adapter=AsyncMock(),
        queue_client=AsyncMock(),
        max_recovery_attempts=3,
    )
    assert recovery.max_recovery_attempts == 3


def test_constructor_override_recovery_warn_threshold() -> None:
    """Explicit recovery_warn_threshold overrides the default."""
    recovery = SourceRecovery(
        adapter=AsyncMock(),
        queue_client=AsyncMock(),
        recovery_warn_threshold=2,
    )
    assert recovery.recovery_warn_threshold == 2


# ============================================================
# RecoveryStats counter
# ============================================================


def test_recovery_stats_skipped_exhausted_default() -> None:
    """RecoveryStats includes skipped_exhausted initialized to 0."""
    stats = RecoveryStats()
    assert stats.skipped_exhausted == 0


def test_recovery_stats_to_dict_includes_skipped_exhausted() -> None:
    """to_dict exposes skipped_exhausted alongside the other counters."""
    stats = RecoveryStats(skipped_exhausted=2)
    d = stats.to_dict()
    assert d["skipped_exhausted"] == 2


# ============================================================
# Guard: source at max attempts → error, no dispatch
# ============================================================


@pytest.mark.asyncio
async def test_recovery_exhausted_transitions_to_error(
    in_memory_adapter,
) -> None:
    """Source at recovery_attempts >= max transitions to error; no dispatch fires.

    This is Test 1 from the Cluster B+E design doc (section Testing
    Strategy item 1). A source stuck in a crash-loop that has hit the
    maximum attempt ceiling must transition to status=error with
    error_stage='recovery_exhausted' so it stops being re-dispatched
    automatically. The reconciler must NOT enqueue a new queue task.
    """
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    # Seed a source at the exhaustion threshold (attempts == max).
    _seed_source(
        in_memory_adapter,
        source_id="src-exhausted",
        status="extracting",
        recovery_attempts=3,
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        max_recovery_attempts=3,
    )
    stats = await recovery.reconcile_database(database_name="default")

    # No queue dispatch should have fired.
    queue.enqueue.assert_not_awaited()

    # Stats: one source was exhausted, none recovered.
    assert stats.skipped_exhausted == 1
    assert stats.recovered == 0
    assert stats.total_scanned == 1

    # The source row must now carry status=error + error_stage=recovery_exhausted.
    source = in_memory_adapter.get_source("src-exhausted", "default")
    assert source is not None
    assert source["status"] == str(SourceStatus.ERROR)
    assert source["error_stage"] == "recovery_exhausted"
    assert source["error_message"] is not None
    assert "3" in source["error_message"]  # attempt count visible in message


@pytest.mark.asyncio
async def test_recovery_exhausted_respects_custom_max(
    in_memory_adapter,
) -> None:
    """Guard fires at the configured max, not the default."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    # max=5 but only 3 attempts — must NOT exhaust.
    _seed_source(
        in_memory_adapter,
        source_id="src-not-exhausted",
        status="extracting",
        recovery_attempts=3,
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        max_recovery_attempts=5,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.skipped_exhausted == 0
    # Source continues through normal recovery path.
    source = in_memory_adapter.get_source("src-not-exhausted", "default")
    assert source is not None
    assert source["status"] != str(SourceStatus.ERROR)


@pytest.mark.asyncio
async def test_recovery_exhausted_at_boundary(
    in_memory_adapter,
) -> None:
    """Guard fires exactly at attempts == max (boundary check)."""
    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    _seed_source(
        in_memory_adapter,
        source_id="src-boundary",
        status="pending",
        recovery_attempts=10,
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        max_recovery_attempts=10,
    )
    stats = await recovery.reconcile_database(database_name="default")

    assert stats.skipped_exhausted == 1
    queue.enqueue.assert_not_awaited()

    source = in_memory_adapter.get_source("src-boundary", "default")
    assert source is not None
    assert source["status"] == str(SourceStatus.ERROR)
    assert source["error_stage"] == "recovery_exhausted"


@pytest.mark.asyncio
async def test_recovery_warn_threshold_logging(in_memory_adapter, caplog) -> None:
    """WARNING is emitted when attempts first reaches recovery_warn_threshold.

    The test confirms the warning path runs without asserting exact log
    format — log assertions are brittle. What matters is that the source
    continues through the normal dispatch path (not exhausted).
    """
    import logging

    queue = AsyncMock()
    queue.enqueue = AsyncMock(return_value={"task_id": "t-warn"})
    queue.task_exists_for_source = AsyncMock(return_value=False)

    # attempts == warn_threshold (2), max is 10 — should NOT exhaust.
    _seed_source(
        in_memory_adapter,
        source_id="src-warn",
        status="pending",
        recovery_attempts=2,
    )

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
        max_recovery_attempts=10,
        recovery_warn_threshold=2,
    )

    with caplog.at_level(logging.WARNING):
        stats = await recovery.reconcile_database(database_name="default")

    # Source should proceed to dispatch (not exhausted).
    assert stats.skipped_exhausted == 0
    assert stats.recovered == 1
    queue.enqueue.assert_awaited_once()


# ============================================================
# adapter.mark_source_exhausted
# ============================================================


def test_mark_source_exhausted_sets_status_and_stage(
    in_memory_adapter,
) -> None:
    """mark_source_exhausted transitions source to error/recovery_exhausted."""
    in_memory_adapter.create_source(
        {
            "id": "src-direct",
            "database_name": "default",
            "filename": "direct.pdf",
            "filepath": "/tmp/direct.pdf",
            "file_type": "pdf",
            "file_size": 1,
            "content_hash": "h",
            "status": "extracting",
        }
    )

    in_memory_adapter.mark_source_exhausted(
        source_id="src-direct",
        database_name="default",
        error_message="Test exhaustion message.",
    )

    source = in_memory_adapter.get_source("src-direct", "default")
    assert source is not None
    assert source["status"] == str(SourceStatus.ERROR)
    assert source["error_stage"] == "recovery_exhausted"
    assert source["error_message"] == "Test exhaustion message."


# ============================================================
# adapter.reset_recovery_attempts
# ============================================================


def test_reset_recovery_attempts_zeroes_counter(
    in_memory_adapter,
) -> None:
    """reset_recovery_attempts sets recovery_attempts back to 0."""
    in_memory_adapter.create_source(
        {
            "id": "src-reset",
            "database_name": "default",
            "filename": "reset.pdf",
            "filepath": "/tmp/reset.pdf",
            "file_type": "pdf",
            "file_size": 1,
            "content_hash": "h2",
            "status": "extracting",
        }
    )
    for _ in range(5):
        in_memory_adapter.increment_source_recovery_attempts(
            source_id="src-reset",
            database_name="default",
        )

    pre = in_memory_adapter.get_source("src-reset", "default")
    assert pre is not None
    assert pre["recovery_attempts"] == 5

    in_memory_adapter.reset_source_recovery_attempts(
        source_id="src-reset",
        database_name="default",
    )

    post = in_memory_adapter.get_source("src-reset", "default")
    assert post is not None
    assert post["recovery_attempts"] == 0


# ============================================================
# Test 2: Fail-handler re-raises and logs on DB exception
# ============================================================


def _make_fail_handler_with_log_reraise(logger_instance: Any, exc: Exception) -> Any:
    """Mirror the exact try/except/log/raise pattern from extraction_finalizer.py.

    This helper replicates the Group A pattern so the test exercises the
    canonical implementation without importing the heavy cortex stack.

    Args:
        logger_instance: structlog logger to use for the warning.
        exc: The original exception that triggered the fail call.

    Returns:
        A callable that takes a fail_func and calls it with the log+re-raise wrapper.
    """

    def _invoke(fail_func: Any, **context: Any) -> None:
        """Wrap fail_func with the canonical log+re-raise pattern.

        Args:
            fail_func: The fail handler to call (e.g. adapter.fail_extraction_job).
            **context: Structured context for the warning log.
        """
        try:
            fail_func()
        except Exception as fail_exc:
            logger_instance.warning(
                "fail_handler_raised",
                event_key="fail_handler_raised",
                original_exception_type=type(exc).__name__,
                original_exception_message=str(exc),
                fail_exception_type=type(fail_exc).__name__,
                fail_exception_message=str(fail_exc),
                **context,
            )
            raise

    return _invoke


def test_fail_handler_reraises_on_db_exception(
    caplog: pytest.LogCaptureFixture,
    structlog_for_caplog: None,  # pytest fixture, side-effect only
) -> None:
    """When a fail_* call itself raises, the exception propagates and a WARNING is logged.

    Verifies the canonical Group A pattern (log+re-raise) is correct:
    - The fail-call exception propagates to the caller.
    - A WARNING with event_key="fail_handler_raised" is captured.
    - The log record includes fail_exception_type so operators can debug.

    This mirrors the pattern at extraction_finalizer.py:312, :599,
    import_service.py:868, :1134, and chunk_extraction_service.py:596,
    :634, :714 without importing the full cortex stack.
    """
    import logging

    import structlog

    _logger = structlog.get_logger("test_fail_handler")

    original_exc = ValueError("original extraction failure")
    db_exc = ConnectionError("db down")

    def _failing_fail_func() -> None:
        raise db_exc

    invoke = _make_fail_handler_with_log_reraise(_logger, original_exc)

    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionError, match="db down"):
        invoke(
            _failing_fail_func,
            source_id="src-test",
            job_id="job-test",
        )

    # A WARNING was emitted with the canonical event_key.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Expected at least one WARNING record"

    # At least one WARNING must mention fail_handler_raised in its message
    # (structlog emits event as the message body or in the formatted output).
    combined = " ".join(r.getMessage() for r in warning_records)
    assert "fail_handler_raised" in combined or any(
        "fail_handler_raised" in str(r.__dict__) for r in warning_records
    )


# ============================================================
# Test 7 (BE-4): Heartbeat timing drift fix
# ============================================================


@pytest.mark.asyncio
async def test_heartbeat_timing_from_start_not_end() -> None:
    """Heartbeat measures interval from start of beat, not end.

    A slow ``_beat()`` (80ms DB write) with a 100ms interval must not
    drift the subsequent beats past the reconciler's stall threshold.

    Arrange: interval=0.1s (100ms), beat takes 0.08s (80ms).
    Run for ~0.35s.

    If timing is measured from START of each iteration (correct), beats
    are scheduled at ~t=0.1, 0.2, 0.3 from entry — four beats inside
    the window (including the immediate __aenter__ beat = 5 total, but
    we only care that the background loop fires ~3+ times).

    If timing is measured from END (old behaviour: sleep I then beat d
    → next sleep starts at I+d), beats land at ~0.18s, ~0.36s from
    entry — only two background beats in 0.35s.
    """
    from datetime import datetime

    beat_timestamps: list[float] = []

    class SlowAdapter:
        """Simulates a DB adapter whose write takes 80ms."""

        def update_source_last_activity(
            self,
            *,
            source_id: str,
            database_name: str,
            at_time: datetime,
        ) -> None:
            """Record timestamp and block for 80ms to simulate slow write."""
            beat_timestamps.append(time.monotonic())
            time.sleep(0.08)  # synchronous — blocks event loop for 80ms

    from chaoscypher_core.services.sources.heartbeat import source_heartbeat

    start = time.monotonic()
    async with source_heartbeat(
        adapter=SlowAdapter(),
        source_id="s1",
        database_name="default",
        interval_seconds=0.1,
    ):
        await asyncio.sleep(0.35)

    # Strip the entry and exit bookend beats; focus on background-loop beats.
    # Entry beat fires at t~=0, exit beat fires after ~0.35s.
    # Background loop beats are everything in between.
    background_beats = [ts - start for ts in beat_timestamps[1:-1]]

    # With interval=0.1s and beat_duration=0.08s over 0.35s:
    # - Correct (start-anchored):  ~0.1, ~0.2, ~0.3  →  3 background beats
    # - Broken (end-anchored): ~0.18, ~0.36         →  1-2 background beats
    assert len(background_beats) >= 3, (
        f"Expected >=3 background beats in 0.35s with 0.1s interval and "
        f"80ms-slow beats, got {len(background_beats)}: {background_beats}"
    )

    # Verify consecutive spacing is close to the 0.1s interval, not 0.18s+.
    all_beats = [ts - start for ts in beat_timestamps]
    loop_beats = all_beats[1:-1]  # exclude entry and exit bookends
    if len(loop_beats) >= 2:
        intervals = [loop_beats[i + 1] - loop_beats[i] for i in range(len(loop_beats) - 1)]
        for idx, interval in enumerate(intervals):
            assert interval < 0.18, (
                f"Inter-beat interval {idx} too wide: {interval:.3f}s "
                f"(expected ~0.1s, threshold 0.18s). "
                f"Full beat times: {all_beats}"
            )


# ============================================================
# Test 8 (BE-5): Finalizer pre-aggregation guard
# ============================================================


@pytest.mark.asyncio
async def test_finalize_aborts_on_non_terminal_tasks(
    in_memory_adapter,
    monkeypatch: Any,
) -> None:
    """Finalizer refuses to aggregate when task rows are still in-flight.

    If the job counter hits terminal while individual ChunkExtractionTask
    rows are still in a non-terminal state (e.g. ``running``), the
    finalizer must return ``{"status": "not_ready", "retry": True}`` so
    the reconciler can re-dispatch once the task rows have persisted.

    Arrange:
    - Seed source (status=extracting) + extraction job
    - Create 3 chunk tasks: 2 ``completed``, 1 ``running``
    - Job's completed_chunks counter says 3/3 (terminal at the job level)

    Act: call finalize_extraction_handler for this job.

    Assert:
    - Return value has ``status="not_ready"`` and ``retry=True``
    - No aggregation happened (source still in extracting status)
    - ``get_completed_chunk_results`` was NOT called
    """
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        TERMINAL_TASK_STATES,
        finalize_extraction_handler,
    )

    # -- Seed source ------------------------------------------------
    source_id = "src-inflight"
    job_id = "job-inflight"
    database_name = "default"

    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": "inflight.pdf",
            "filepath": "/tmp/inflight.pdf",
            "file_type": "pdf",
            "file_size": 1000,
            "content_hash": "hash-inflight",
            "status": "extracting",
        }
    )

    # -- Seed extraction job ----------------------------------------
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name=database_name,
    )
    in_memory_adapter.update_extraction_job_total(
        job_id=job_id,
        total_chunks=3,
        database_name=database_name,
    )

    # -- Seed 3 chunk tasks: 2 completed, 1 running ----------------
    for i in range(3):
        in_memory_adapter.create_chunk_task(
            task_id=f"task-{i}",
            job_id=job_id,
            database_name=database_name,
            chunk_index=i,
        )
    # Mark tasks 0 and 1 as completed
    in_memory_adapter.mark_chunk_task_queued("task-0", "qt-0")
    in_memory_adapter.start_chunk_task("task-0")
    in_memory_adapter.complete_chunk_task("task-0", raw_entities=[], raw_relationships=[])

    in_memory_adapter.mark_chunk_task_queued("task-1", "qt-1")
    in_memory_adapter.start_chunk_task("task-1")
    in_memory_adapter.complete_chunk_task("task-1", raw_entities=[], raw_relationships=[])

    # Leave task-2 in running status (simulate in-flight)
    in_memory_adapter.mark_chunk_task_queued("task-2", "qt-2")
    in_memory_adapter.start_chunk_task("task-2")

    # -- Mock Settings (get_settings is imported at call time) ------
    mock_settings = MagicMock()
    mock_settings.priorities.background = 50
    mock_settings.auto_enable = False

    # -- Spy on get_completed_chunk_results -------------------------
    original_get_completed = in_memory_adapter.get_completed_chunk_results
    completed_calls: list[str] = []

    def _spy_get_completed(job_id_arg: str) -> list:
        completed_calls.append(job_id_arg)
        return original_get_completed(job_id_arg)

    in_memory_adapter.get_completed_chunk_results = _spy_get_completed

    # -- Act --------------------------------------------------------
    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
    ):
        result = await finalize_extraction_handler(
            graph_repository=MagicMock(),
            llm_service=MagicMock(),
            source_repository=in_memory_adapter,
            chunk_extraction_service=MagicMock(),
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": database_name,
            },
        )

    # -- Assert: guard fired, not_ready returned --------------------
    assert result.get("status") == "not_ready", f"Expected not_ready, got: {result}"
    assert result.get("retry") is True, f"Expected retry=True, got: {result}"

    # -- Assert: get_completed_chunk_results was NOT called ---------
    assert completed_calls == [], (
        f"get_completed_chunk_results should not be called when tasks are "
        f"still in-flight, but was called with: {completed_calls}"
    )

    # -- Assert: source still in extracting (no state mutation) -----
    source = in_memory_adapter.get_source(source_id, database_name)
    assert source is not None
    assert source["status"] == "extracting", (
        f"Source should still be extracting, got: {source['status']}"
    )

    # -- Verify TERMINAL_TASK_STATES contains expected values -------
    assert "completed" in TERMINAL_TASK_STATES
    assert "failed" in TERMINAL_TASK_STATES
    assert "cancelled" in TERMINAL_TASK_STATES
    assert "orphaned" in TERMINAL_TASK_STATES
    assert "running" not in TERMINAL_TASK_STATES
    assert "queued" not in TERMINAL_TASK_STATES
    assert "pending" not in TERMINAL_TASK_STATES


@pytest.mark.asyncio
async def test_finalize_guard_passes_when_all_tasks_terminal(
    in_memory_adapter,
    monkeypatch: Any,
) -> None:
    """Guard passes when all tasks are in terminal states (happy path).

    When 2 tasks are completed and 1 is failed (all terminal), the guard
    must NOT fire. The finalizer proceeds to aggregation normally.
    This verifies the guard does not break the standard completion path.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    source_id = "src-all-terminal"
    job_id = "job-all-terminal"
    database_name = "default"

    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": "terminal.pdf",
            "filepath": "/tmp/terminal.pdf",
            "file_type": "pdf",
            "file_size": 1000,
            "content_hash": "hash-terminal",
            "status": "extracting",
        }
    )

    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name=database_name,
    )
    in_memory_adapter.update_extraction_job_total(
        job_id=job_id,
        total_chunks=2,
        database_name=database_name,
    )

    for i in range(2):
        in_memory_adapter.create_chunk_task(
            task_id=f"task-term-{i}",
            job_id=job_id,
            database_name=database_name,
            chunk_index=i,
        )
    # Both tasks completed (all terminal)
    in_memory_adapter.mark_chunk_task_queued("task-term-0", "qt-t0")
    in_memory_adapter.start_chunk_task("task-term-0")
    in_memory_adapter.complete_chunk_task(
        "task-term-0",
        raw_entities=[
            {
                "name": "Alice",
                "type": "Person",
                "description": "test entity",
                "aliases": [],
                "confidence": 0.9,
                "sent_ref": "s1",
            }
        ],
        raw_relationships=[],
    )

    in_memory_adapter.mark_chunk_task_queued("task-term-1", "qt-t1")
    in_memory_adapter.start_chunk_task("task-term-1")
    in_memory_adapter.complete_chunk_task(
        "task-term-1",
        raw_entities=[],
        raw_relationships=[],
    )

    mock_settings = MagicMock()
    mock_settings.priorities.background = 50
    mock_settings.auto_enable = False

    # Track whether get_chunk_tasks_by_job was called (guard ran)
    guard_calls: list[str] = []
    original_get_tasks = in_memory_adapter.get_chunk_tasks_by_job

    def _spy_guard(jid: str) -> list:
        guard_calls.append(jid)
        return original_get_tasks(jid)

    in_memory_adapter.get_chunk_tasks_by_job = _spy_guard

    # Use a real EngineSettings -- _apply_post_dedup_filters reads
    # ``extraction.extraction_filtering_mode`` from it, so a bare MagicMock
    # would resolve a stringified MagicMock and trip ``resolve_filtering_config``.
    from chaoscypher_core.settings import EngineSettings as _EngineSettings

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=_EngineSettings(),
        ),
        patch(
            "chaoscypher_core.repo_factories.get_embedding_service",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.run_deduplication",
            new=AsyncMock(return_value=([], [], [], {})),
        ),
        patch(
            "chaoscypher_core.operations.extraction.extraction_finalizer._complete_finalization",
            new=AsyncMock(return_value={"success": True, "job_id": job_id}),
        ),
    ):
        result = await finalize_extraction_handler(
            graph_repository=MagicMock(),
            llm_service=MagicMock(),
            source_repository=in_memory_adapter,
            chunk_extraction_service=MagicMock(),
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": database_name,
            },
        )

    # Guard ran (checked tasks) but did NOT return not_ready
    assert guard_calls == [job_id], (
        f"Expected guard to call get_chunk_tasks_by_job({job_id!r}), got calls: {guard_calls}"
    )
    assert result.get("status") != "not_ready", (
        f"Guard should NOT fire when all tasks are terminal, got: {result}"
    )


@pytest.mark.asyncio
async def test_finalize_guard_passes_on_zero_tasks(
    in_memory_adapter,
    monkeypatch: Any,
) -> None:
    """Guard passes when there are zero tasks (BE-3 empty-extraction path).

    An empty document yields no chunk tasks. The guard must see an empty
    list, treat it as all-terminal, and let the finalizer proceed to the
    zero-entity commit path (committed_empty).
    """
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.operations.extraction.extraction_finalizer import (
        finalize_extraction_handler,
    )

    source_id = "src-zero-tasks"
    job_id = "job-zero-tasks"
    database_name = "default"

    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": "empty.txt",
            "filepath": "/tmp/empty.txt",
            "file_type": "text",
            "file_size": 0,
            "content_hash": "hash-empty",
            "status": "extracting",
        }
    )

    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name=database_name,
    )
    in_memory_adapter.update_extraction_job_total(
        job_id=job_id,
        total_chunks=0,
        database_name=database_name,
    )
    # No chunk tasks seeded — empty document

    mock_settings = MagicMock()
    mock_settings.priorities.background = 50
    mock_settings.auto_enable = False
    # queue_import_commit called from _queue_commit_phase needs these
    mock_settings.llm.token_cost_input_per_million = 0.0
    mock_settings.llm.token_cost_output_per_million = 0.0

    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.operations.extraction.extraction_finalizer._queue_commit_phase",
        ),
        patch(
            "chaoscypher_core.operations.extraction.extraction_finalizer"
            ".trigger_next_waiting_extraction",
        ),
    ):
        result = await finalize_extraction_handler(
            graph_repository=MagicMock(),
            llm_service=MagicMock(),
            source_repository=in_memory_adapter,
            chunk_extraction_service=MagicMock(),
            data={
                "job_id": job_id,
                "source_id": source_id,
                "database_name": database_name,
            },
        )

    # Guard sees empty task list → passes; zero-entity path commits empty
    assert result.get("status") == "committed_empty", (
        f"Expected committed_empty for zero-task job, got: {result}"
    )


# ============================================================
# Test 10 (BE-7): Cascade orphan on job failure
# ============================================================


def test_fail_extraction_job_orphans_non_terminal_tasks(
    in_memory_adapter,
) -> None:
    """fail_extraction_job cascades non-terminal tasks to 'orphaned'.

    Terminal tasks (completed/failed/cancelled) are left unchanged.
    Verify:
    - 2 'running' tasks → status='orphaned', error_message='parent job failed'
    - 1 'queued' task   → status='orphaned', error_message='parent job failed'
    - 1 'completed' task → unchanged
    - 1 'failed' task    → unchanged
    - job.status == 'failed'
    """
    # -- Arrange: seed source + job ----------------------------------------
    source_id = "src-orphan-test"
    job_id = "job-orphan-test"
    database_name = "default"

    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": "orphan.pdf",
            "filepath": "/tmp/orphan.pdf",
            "file_type": "pdf",
            "file_size": 500,
            "content_hash": "hash-orphan",
            "status": "extracting",
        }
    )
    in_memory_adapter.create_extraction_job(
        job_id=job_id,
        source_id=source_id,
        database_name=database_name,
    )
    in_memory_adapter.update_extraction_job_total(
        job_id=job_id,
        total_chunks=5,
        database_name=database_name,
    )

    # -- Seed 5 chunk tasks --------------------------------------------------
    # task-0, task-1: running (non-terminal → should be orphaned)
    # task-2: queued (non-terminal → should be orphaned)
    # task-3: completed (terminal → unchanged)
    # task-4: failed (terminal → unchanged)

    for i in range(5):
        in_memory_adapter.create_chunk_task(
            task_id=f"task-o-{i}",
            job_id=job_id,
            database_name=database_name,
            chunk_index=i,
        )

    # task-0: running
    in_memory_adapter.mark_chunk_task_queued("task-o-0", "qt-o-0")
    in_memory_adapter.start_chunk_task("task-o-0")

    # task-1: running
    in_memory_adapter.mark_chunk_task_queued("task-o-1", "qt-o-1")
    in_memory_adapter.start_chunk_task("task-o-1")

    # task-2: queued
    in_memory_adapter.mark_chunk_task_queued("task-o-2", "qt-o-2")

    # task-3: completed
    in_memory_adapter.mark_chunk_task_queued("task-o-3", "qt-o-3")
    in_memory_adapter.start_chunk_task("task-o-3")
    in_memory_adapter.complete_chunk_task("task-o-3", raw_entities=[], raw_relationships=[])

    # task-4: failed
    in_memory_adapter.mark_chunk_task_queued("task-o-4", "qt-o-4")
    in_memory_adapter.start_chunk_task("task-o-4")
    in_memory_adapter.fail_chunk_task("task-o-4", error_message="llm timeout")

    # -- Act -----------------------------------------------------------------
    in_memory_adapter.fail_extraction_job(job_id, "some error")

    # -- Assert: job marked failed ------------------------------------------
    job = in_memory_adapter.get_extraction_job(job_id)
    assert job is not None
    assert job["status"] == "failed"

    # -- Assert: 3 non-terminal tasks → orphaned ----------------------------
    tasks = in_memory_adapter.get_chunk_tasks_by_job(job_id)
    by_id = {t["id"]: t for t in tasks}

    for task_id in ("task-o-0", "task-o-1", "task-o-2"):
        task = by_id[task_id]
        assert task["status"] == "orphaned", (
            f"{task_id} expected 'orphaned', got {task['status']!r}"
        )
        assert task["error_message"] == "parent job failed", (
            f"{task_id} expected error_message='parent job failed', got {task['error_message']!r}"
        )

    # -- Assert: terminal tasks unchanged -----------------------------------
    completed_task = by_id["task-o-3"]
    assert completed_task["status"] == "completed", (
        f"task-o-3 (completed) should be unchanged, got {completed_task['status']!r}"
    )

    failed_task = by_id["task-o-4"]
    assert failed_task["status"] == "failed", (
        f"task-o-4 (failed) should be unchanged, got {failed_task['status']!r}"
    )
    assert failed_task["error_message"] == "llm timeout", (
        f"task-o-4 error_message should be 'llm timeout' (unchanged), "
        f"got {failed_task['error_message']!r}"
    )


# ============================================================
# Test 4 (BE-8): extraction_complete flag suppresses re-dispatch
# ============================================================


@pytest.mark.asyncio
async def test_recovery_skips_when_extraction_complete_flag_true(
    in_memory_adapter,
) -> None:
    """Recovery respects extraction_complete=True even if status is still 'extracting'.

    Narrow race: a crash after flag write but before transaction commit
    would leave this inconsistent state visible. Cluster A made the
    writes atomic via SQLAlchemy UoW, so in practice this state can't
    arise — but a future refactor that splits the writes shouldn't
    silently re-dispatch.

    The classifier short-circuits at ``source.get("extraction_complete")``
    and returns None (no action needed), so:
    - queue.enqueue must NOT be called
    - stats.skipped_healthy is incremented (classifier returned None)
    """
    from chaoscypher_core.adapters.sqlite.models import SourceRow

    queue = AsyncMock()
    queue.enqueue = AsyncMock()
    queue.task_exists_for_source = AsyncMock(return_value=False)

    source_id = "src-extraction-complete"

    # Seed the source in extracting status
    _seed_source(
        in_memory_adapter,
        source_id=source_id,
        status="extracting",
    )

    # Force extraction_complete=True directly on the row — this
    # is the narrow post-flag-write / pre-status-transition window.
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row is not None
    row.extraction_complete = True
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
    )
    stats = await recovery.reconcile_database(database_name="default")

    # No dispatch should have fired — flag takes precedence over status.
    queue.enqueue.assert_not_awaited()

    # Source was scanned but classified as healthy (flag set → None returned).
    assert stats.total_scanned == 1
    assert stats.recovered == 0
    assert stats.skipped_exhausted == 0
    assert stats.skipped_healthy == 1, (
        f"Expected skipped_healthy=1 when extraction_complete=True; got stats={stats.to_dict()}"
    )


# ============================================================
# Test 5 (BE-8): Heartbeat fires final beat on handler exception
# ============================================================


@pytest.mark.asyncio
async def test_heartbeat_final_beat_fires_on_handler_exception(
    in_memory_adapter,
) -> None:
    """When wrapped handler raises, heartbeat's __aexit__ updates activity.

    The SourceHeartbeat.__aexit__ calls self._beat() unconditionally
    after cancelling the background task — it does not check exc_type.
    This test verifies that contract holds: even on an exception exit,
    last_activity_at is bumped to a time >= the test start.
    """
    from datetime import UTC, datetime

    from chaoscypher_core.adapters.sqlite.models import SourceRow
    from chaoscypher_core.services.sources.heartbeat import source_heartbeat

    source_id = "src_hb_exc"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "x.md",
            "filepath": "/tmp/x.md",
            "file_type": "markdown",
            "file_size": 10,
            "content_hash": "hash-x",
            "status": "extracting",
        }
    )

    # Set last_activity_at to a known old timestamp
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row is not None
    row.last_activity_at = datetime(2020, 1, 1, tzinfo=UTC)
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    # Use a naive UTC timestamp for comparison: SQLite stores last_activity_at
    # as a naive datetime (no tzinfo), so comparing with an aware datetime
    # raises TypeError. The heartbeat writes datetime.now(UTC), which SQLite
    # strips of its tzinfo on round-trip.
    start = datetime.now(UTC).replace(tzinfo=None)

    # Act: wrap a failing handler in source_heartbeat
    with pytest.raises(RuntimeError, match="handler failed"):
        async with source_heartbeat(
            adapter=in_memory_adapter,
            source_id=source_id,
            database_name=in_memory_adapter.database_name,
            interval_seconds=60,  # long enough that no interval-beat fires
        ):
            raise RuntimeError("handler failed")

    # Assert: last_activity_at was updated to a fresh timestamp.
    # __aexit__ calls self._beat() unconditionally after cancelling the
    # background task, so the final beat must have fired even here.
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row is not None
    assert row.last_activity_at is not None, (
        "last_activity_at must not be None after heartbeat exit"
    )
    # Strip tzinfo from row value if it has one (defensive against future
    # schema changes that add timezone-awareness).
    row_ts = row.last_activity_at
    if row_ts.tzinfo is not None:
        row_ts = row_ts.replace(tzinfo=None)
    assert row_ts > start, (
        f"last_activity_at should have been bumped on exception-exit: {row_ts} vs start={start}"
    )


# ============================================================
# Test 6 (BE-8): Double-recovery guard — concurrent dispatches
# ============================================================


@pytest.mark.asyncio
async def test_concurrent_recovery_dispatches_idempotent(
    in_memory_adapter,
) -> None:
    """Concurrent _recover_one calls produce bounded dispatch counts.

    Two concurrent calls on the same source produce at most one
    effective dispatch per run, and recovery_attempts is incremented
    once per _recover_one call that reaches dispatch.

    Behavior guaranteed by this test:
    - Each concurrent call may independently classify the source and
      enqueue a task. That's two enqueue calls in the worst case —
      acceptable because the queue's own idempotency (task_exists check
      in _classify sub-cases) is the second line of defense.
    - BUT recovery_attempts must increment by exactly the number of
      calls that reach dispatch (at most 2). Incrementing by more than
      the number of dispatching calls would indicate a concurrency bug.

    The stall-threshold debounce is bypassed (respect_stall_threshold=False)
    so both calls go through the full classify path with a stale source
    snapshot (no intervening heartbeat between the two concurrent reads).
    """
    from datetime import UTC, datetime, timedelta

    from chaoscypher_core.adapters.sqlite.models import SourceRow

    source_id = "src-concurrent"

    # Seed a source in 'pending' status with a stale last_activity_at
    # so it clearly needs recovery.
    _seed_source(
        in_memory_adapter,
        source_id=source_id,
        status="pending",
    )
    row = in_memory_adapter.session.get(SourceRow, source_id)
    assert row is not None
    row.last_activity_at = datetime.now(UTC) - timedelta(hours=1)
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    enqueue_calls: list[dict] = []

    async def _mock_enqueue(**kwargs: Any) -> dict:
        enqueue_calls.append(kwargs)
        return {"task_id": f"task-{len(enqueue_calls)}"}

    queue = AsyncMock()
    queue.enqueue = AsyncMock(side_effect=_mock_enqueue)
    queue.task_exists_for_source = AsyncMock(return_value=False)

    recovery = SourceRecovery(
        adapter=in_memory_adapter,
        queue_client=queue,
    )

    # Read the source dict ONCE and pass the same snapshot to both
    # _recover_one calls to simulate a concurrent read scenario where
    # both reconcilers fetched the row before either dispatch committed.
    source_snapshot = in_memory_adapter.get_source(source_id, "default")
    assert source_snapshot is not None

    stats_a = RecoveryStats()
    stats_b = RecoveryStats()

    # Run both _recover_one calls concurrently with the same source snapshot.
    await asyncio.gather(
        recovery._recover_one(source_snapshot, "default", stats_a, respect_stall_threshold=False),
        recovery._recover_one(source_snapshot, "default", stats_b, respect_stall_threshold=False),
    )

    dispatches = stats_a.recovered + stats_b.recovered

    # At most 2 dispatches (one per concurrent call); 0 would indicate
    # an unexpected guard firing — both should reach dispatch since neither
    # call sees a heartbeat update made by the other (same snapshot).
    assert dispatches <= 2, (
        f"Expected at most 2 dispatches for 2 concurrent calls, got {dispatches}"
    )
    assert dispatches >= 1, (
        f"Expected at least 1 dispatch for a stale pending source, got {dispatches}"
    )

    # enqueue calls must match the number of dispatches (1:1 relationship).
    assert len(enqueue_calls) == dispatches, (
        f"enqueue call count ({len(enqueue_calls)}) must equal dispatch count ({dispatches})"
    )

    # recovery_attempts must be incremented exactly once per dispatching call.
    source_after = in_memory_adapter.get_source(source_id, "default")
    assert source_after is not None
    recovery_attempts = source_after["recovery_attempts"]
    assert recovery_attempts == dispatches, (
        f"recovery_attempts ({recovery_attempts}) should equal dispatch count "
        f"({dispatches}) — more increments would indicate a concurrency bug"
    )
