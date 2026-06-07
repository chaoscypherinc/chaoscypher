# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for worker.py setup helpers, the health loop, run_worker tail, and main().

These exercise the branches the loop-focused suites leave uncovered:

- ``_setup_source_recovery`` — disabled branch, happy path (initial reconcile +
  background task), startup-reconcile timeout (-> RecoveryTimeoutError), and a
  generic reconcile failure that is logged but still returns a task.
- ``_setup_orphan_task_cleanup`` / ``_setup_orphan_files_cleanup`` /
  ``_setup_search_sweep`` — None-branch when a prerequisite is missing, and the
  happy-path task creation (cancelled immediately).
- ``_setup_health_monitor`` — disabled, adapter-missing, enabled-with-adapter
  (HealthRegistry/probes/HealthPauseEvaluator patched), and the ping closure
  when ``queue_client.client`` is present vs None.
- ``_health_monitor_loop`` — one tick calls ``evaluator.tick()``, a raised tick
  is swallowed, and the loop is cancellable.
- ``run_worker`` tail — empty-handler error logs, rehydration skip branches,
  and the shutdown ``finally`` that cancels background tasks and disconnects the
  queue client + storage adapter.
- ``main()`` — configure_logging called and asyncio.run invoked.

Intentionally NOT covered (defensive / cross-process arms):
- The cross-process upgrade poll loop (``while not get_upgrade_state(...).ready``)
  — it sleeps on a flag flipped by another process; it is stubbed past via
  ``_patch_upgrade_gate``.
- The deepest ``contextlib.suppress(Exception)`` arms around
  ``trigger_dispatcher.stop()`` raising and ``storage_adapter.disconnect()``
  raising (pure swallow-and-continue noise).
- The circuit-breaker wrapper ``_run_worker_with_circuit_breaker`` (covered by
  test_worker_circuit_breaker.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs  # noqa: F401 — paired with structlog_for_caplog

from chaoscypher_core.exceptions import RecoveryTimeoutError


# ============================================================================
# Helpers (copied from test_worker_startup.py per task instructions)
# ============================================================================


def _make_mock_settings():
    """Create a mock Settings object with all required nested attributes."""
    settings = MagicMock()
    settings.queue.connection_max_retries = 3
    settings.queue.connection_retry_delay = 0.01  # Fast for tests
    settings.timeouts.queue_poll_interval = 1.0
    settings.timeouts.instance_drain_max_wait = 30.0
    settings.timeouts.queue_semaphore_acquire = 5.0
    settings.timeouts.settings_listener_shutdown = 5.0
    settings.workers.health_report_interval = 60.0
    settings.backoff.queue_poller_error_delay = 1.0
    settings.queue_recovery.heartbeat_ttl_seconds = 30
    settings.queue_recovery.heartbeat_refresh_interval_seconds = 10
    settings.queue_recovery.worker_reconcile_interval_seconds = 60
    settings.source_recovery.worker_scan_interval_seconds = 60
    settings.health_monitor.enabled = False
    settings.current_database = "test_db"
    settings.data_dir = "/data"
    settings.paths.data_dir = "/data"
    settings.paths.app_db_filename = "app.db"
    return settings


@contextlib.contextmanager
def _patch_upgrade_gate():
    """Bypass the cross-process upgrade gate inside ``run_worker``.

    ``run_worker`` calls ``get_db_path(current_database)`` then
    ``get_upgrade_state(db_path)`` to decide whether to sleep-poll for an
    operator-driven migration confirmation. With no patches the test
    resolves the developer's real local DB and may spin until the
    surrounding ``wait_for`` raises ``TimeoutError``. We stub both helpers
    to short-circuit it.
    """
    fake_state = MagicMock()
    fake_state.ready = True
    fake_state.blocked_on = []
    fake_state.last_backup = None
    fake_state.message = ""
    with (
        patch(
            "chaoscypher_core.database.engine.get_db_path",
            return_value=Path("/tmp/unused-upgrade-gate.db"),
        ),
        patch(
            "chaoscypher_core.database.migrations.state.get_upgrade_state",
            return_value=fake_state,
        ),
    ):
        yield


def _source_recovery_settings(
    *,
    timeout_s: int = 30,
    scan_interval: int = 3600,
    stalled: int = 60,
    max_attempts: int = 3,
    warn_threshold: int = 2,
) -> MagicMock:
    """Settings stub with the nested attrs ``_setup_source_recovery`` reads."""
    settings = MagicMock()
    settings.source_recovery.reconcile_timeout_seconds = timeout_s
    settings.source_recovery.worker_scan_interval_seconds = scan_interval
    settings.source_recovery.stalled_threshold_seconds = stalled
    settings.source_recovery.max_recovery_attempts = max_attempts
    settings.source_recovery.recovery_warn_threshold = warn_threshold
    return settings


async def _cancel(task: asyncio.Task | None) -> None:
    """Cancel + await a background task created by a setup helper."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ============================================================================
# _setup_source_recovery
# ============================================================================


class TestSetupSourceRecovery:
    """Disabled, happy-path, timeout, and generic-failure branches."""

    @pytest.mark.asyncio
    async def test_disabled_when_adapter_missing(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """No storage_adapter -> returns None and logs source_recovery_disabled."""
        from chaoscypher_neuron.worker import _setup_source_recovery

        ctx = {"settings": _source_recovery_settings(), "current_database": "db"}

        with caplog.at_level(logging.WARNING):
            result = await _setup_source_recovery(ctx)

        assert result is None
        messages = [r.message for r in caplog.records]
        assert any("source_recovery_disabled" in m for m in messages), messages

    @pytest.mark.asyncio
    async def test_disabled_when_database_missing(self) -> None:
        """storage_adapter present but no current_database -> None (event_bus still configured)."""
        from chaoscypher_neuron.worker import _setup_source_recovery

        adapter = MagicMock()
        ctx = {
            "settings": _source_recovery_settings(),
            "current_database": "",
            "storage_adapter": adapter,
        }

        with patch("chaoscypher_core.services.events.event_bus") as mock_bus:
            result = await _setup_source_recovery(ctx)

        assert result is None
        # event_bus.configure runs before the disabled check because the adapter exists.
        mock_bus.configure.assert_called_once_with(adapter)

    @pytest.mark.asyncio
    async def test_happy_path_runs_initial_reconcile_and_returns_task(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """Happy path: initial reconcile awaited, periodic task returned."""
        from chaoscypher_neuron.worker import _setup_source_recovery

        adapter = MagicMock()
        ctx = {
            "settings": _source_recovery_settings(),
            "current_database": "db",
            "storage_adapter": adapter,
        }

        stats = MagicMock()
        stats.to_dict.return_value = {"recovered": 0, "skipped_paused": 0}
        recovery_instance = MagicMock()
        recovery_instance.reconcile_database = AsyncMock(return_value=stats)

        with (
            patch(
                "chaoscypher_neuron.worker.SourceRecovery",
                return_value=recovery_instance,
            ) as mock_cls,
            patch("chaoscypher_core.services.events.event_bus") as mock_bus,
            caplog.at_level(logging.INFO),
        ):
            task = await _setup_source_recovery(ctx)

        try:
            assert isinstance(task, asyncio.Task)
            mock_bus.configure.assert_called_once_with(adapter)
            mock_cls.assert_called_once()
            recovery_instance.reconcile_database.assert_awaited_once_with(database_name="db")
            messages = [r.message for r in caplog.records]
            assert any("startup_source_reconcile_complete" in m for m in messages), messages
        finally:
            await _cancel(task)

    @pytest.mark.asyncio
    async def test_startup_reconcile_timeout_raises_recovery_timeout(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """A reconcile slower than the timeout raises RecoveryTimeoutError."""
        from chaoscypher_neuron.worker import _setup_source_recovery

        adapter = MagicMock()
        ctx = {
            "settings": _source_recovery_settings(timeout_s=0),  # immediate timeout
            "current_database": "db",
            "storage_adapter": adapter,
        }

        async def never_returns(database_name: str):
            await asyncio.sleep(10)

        recovery_instance = MagicMock()
        recovery_instance.reconcile_database = AsyncMock(side_effect=never_returns)

        with (
            patch(
                "chaoscypher_neuron.worker.SourceRecovery",
                return_value=recovery_instance,
            ),
            patch("chaoscypher_core.services.events.event_bus"),
            caplog.at_level(logging.WARNING),
            pytest.raises(RecoveryTimeoutError),
        ):
            await _setup_source_recovery(ctx)

        messages = [r.message for r in caplog.records]
        assert any("startup_source_reconcile_timeout" in m for m in messages), messages

    @pytest.mark.asyncio
    async def test_generic_reconcile_error_logged_but_task_returned(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """A generic reconcile exception is logged and a periodic task is still returned."""
        from chaoscypher_neuron.worker import _setup_source_recovery

        adapter = MagicMock()
        ctx = {
            "settings": _source_recovery_settings(),
            "current_database": "db",
            "storage_adapter": adapter,
        }

        recovery_instance = MagicMock()
        recovery_instance.reconcile_database = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        with (
            patch(
                "chaoscypher_neuron.worker.SourceRecovery",
                return_value=recovery_instance,
            ),
            patch("chaoscypher_core.services.events.event_bus"),
            caplog.at_level(logging.ERROR),
        ):
            task = await _setup_source_recovery(ctx)

        try:
            assert isinstance(task, asyncio.Task)
            messages = [r.message for r in caplog.records]
            assert any("startup_source_reconcile_failed" in m for m in messages), messages
        finally:
            await _cancel(task)


# ============================================================================
# _setup_orphan_task_cleanup / _setup_orphan_files_cleanup / _setup_search_sweep
# ============================================================================


class TestSetupOrphanTaskCleanup:
    @pytest.mark.asyncio
    async def test_none_when_adapter_missing(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        from chaoscypher_neuron.worker import _setup_orphan_task_cleanup

        ctx = {"settings": _make_mock_settings()}
        with caplog.at_level(logging.WARNING):
            result = _setup_orphan_task_cleanup(ctx)

        assert result is None
        assert any("orphan_task_cleanup_disabled" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_happy_path_returns_task(self) -> None:
        from chaoscypher_neuron.worker import _setup_orphan_task_cleanup

        settings = MagicMock()
        settings.source_recovery.orphan_task_retention_days = 7
        settings.source_recovery.orphan_task_cleanup_interval_seconds = 3600
        ctx = {"settings": settings, "storage_adapter": MagicMock()}

        task = _setup_orphan_task_cleanup(ctx)
        try:
            assert isinstance(task, asyncio.Task)
        finally:
            await _cancel(task)


class TestSetupOrphanFilesCleanup:
    @pytest.mark.asyncio
    async def test_none_when_adapter_missing(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        from chaoscypher_neuron.worker import _setup_orphan_files_cleanup

        ctx = {"settings": _make_mock_settings()}
        with caplog.at_level(logging.WARNING):
            result = _setup_orphan_files_cleanup(ctx)

        assert result is None
        assert any("orphan_files_cleanup_disabled" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_happy_path_returns_task(self, tmp_path: Path) -> None:
        from chaoscypher_neuron.worker import _setup_orphan_files_cleanup

        settings = MagicMock()
        settings.database_dir = tmp_path
        settings.current_database = "default"
        settings.source_recovery.orphan_files_retention_days = 1
        settings.source_recovery.orphan_files_cleanup_interval_seconds = 3600
        settings.source_recovery.orphan_files_cleanup_timeout_seconds = 60
        ctx = {"settings": settings, "storage_adapter": MagicMock()}

        task = _setup_orphan_files_cleanup(ctx)
        try:
            assert isinstance(task, asyncio.Task)
        finally:
            await _cancel(task)


class TestSetupSearchSweep:
    @pytest.mark.asyncio
    async def test_none_when_search_repo_missing(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        from chaoscypher_neuron.worker import _setup_search_sweep

        ctx = {"settings": _make_mock_settings(), "storage_adapter": MagicMock()}
        # No search_repository key -> disabled branch.
        with caplog.at_level(logging.WARNING):
            result = _setup_search_sweep(ctx)

        assert result is None
        assert any("search_sweep_disabled" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_happy_path_returns_task_and_logs_started(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        from chaoscypher_neuron.worker import _setup_search_sweep

        settings = MagicMock()
        settings.intervals.search_sweep_seconds = 300
        settings.intervals.search_sweep_max_attempts = 5
        ctx = {
            "settings": settings,
            "storage_adapter": MagicMock(),
            "search_repository": MagicMock(),
        }

        # Patch the lazily-imported loop at its source module so no real sweep runs.
        async def _fake_loop(**_kwargs):
            await asyncio.sleep(3600)

        with (
            patch(
                "chaoscypher_neuron.search_sweep._search_sweep_loop",
                _fake_loop,
            ),
            caplog.at_level(logging.INFO),
        ):
            task = _setup_search_sweep(ctx)

        try:
            assert isinstance(task, asyncio.Task)
            assert any("search_sweep_started" in r.message for r in caplog.records)
        finally:
            await _cancel(task)


# ============================================================================
# _setup_health_monitor
# ============================================================================


def _health_settings(*, enabled: bool = True) -> MagicMock:
    settings = MagicMock()
    hm = settings.health_monitor
    hm.enabled = enabled
    hm.disk_warn_bytes = 1_000_000
    hm.disk_error_bytes = 500_000
    hm.trip_threshold = 3
    hm.clear_threshold = 2
    hm.check_interval_seconds = 3600
    settings.paths.data_dir = "/data"
    return settings


class TestSetupHealthMonitor:
    @pytest.mark.asyncio
    async def test_disabled_returns_none_and_logs(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        from chaoscypher_neuron.worker import _setup_health_monitor

        ctx = {"settings": _health_settings(enabled=False), "storage_adapter": MagicMock()}
        with caplog.at_level(logging.INFO):
            result = _setup_health_monitor(ctx)

        assert result is None
        assert any("health_monitor_disabled" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_none_when_adapter_missing(self) -> None:
        from chaoscypher_neuron.worker import _setup_health_monitor

        ctx = {"settings": _health_settings(enabled=True)}  # no storage_adapter
        result = _setup_health_monitor(ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_enabled_with_client_returns_task_and_builds_ping(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """Enabled + adapter + a truthy queue_client.client -> task created, async ping wired."""
        from chaoscypher_neuron.worker import _setup_health_monitor

        ctx = {"settings": _health_settings(enabled=True), "storage_adapter": MagicMock()}

        mock_registry = MagicMock()
        mock_queue_probe = MagicMock()
        fake_client = MagicMock()
        fake_client.ping = AsyncMock(return_value=True)
        mock_qc = MagicMock()
        mock_qc.client = fake_client

        with (
            patch(
                "chaoscypher_core.services.events.health.registry.HealthRegistry",
                return_value=mock_registry,
            ),
            patch("chaoscypher_core.services.events.health.probes.disk_space.DiskSpaceProbe"),
            patch(
                "chaoscypher_core.services.events.health.probes.queue.QueueProbe",
                mock_queue_probe,
            ),
            patch("chaoscypher_core.services.events.health.pause_evaluator.HealthPauseEvaluator"),
            patch("chaoscypher_neuron.worker.queue_client", mock_qc),
            caplog.at_level(logging.INFO),
        ):
            task = _setup_health_monitor(ctx)
            try:
                assert isinstance(task, asyncio.Task)
                assert any("health_monitor_started" in r.message for r in caplog.records)
                # The QueueProbe was constructed with a non-None async ping_fn;
                # invoke it to cover the closure body.
                ping_fn = mock_queue_probe.call_args.kwargs["ping_fn"]
                assert ping_fn is not None
                assert await ping_fn() is True
                fake_client.ping.assert_awaited_once()
            finally:
                await _cancel(task)

    @pytest.mark.asyncio
    async def test_enabled_without_client_passes_none_ping(self) -> None:
        """When queue_client.client is None the QueueProbe gets ping_fn=None."""
        from chaoscypher_neuron.worker import _setup_health_monitor

        ctx = {"settings": _health_settings(enabled=True), "storage_adapter": MagicMock()}

        mock_queue_probe = MagicMock()
        mock_qc = MagicMock()
        mock_qc.client = None

        with (
            patch("chaoscypher_core.services.events.health.registry.HealthRegistry"),
            patch("chaoscypher_core.services.events.health.probes.disk_space.DiskSpaceProbe"),
            patch(
                "chaoscypher_core.services.events.health.probes.queue.QueueProbe",
                mock_queue_probe,
            ),
            patch("chaoscypher_core.services.events.health.pause_evaluator.HealthPauseEvaluator"),
            patch("chaoscypher_neuron.worker.queue_client", mock_qc),
        ):
            task = _setup_health_monitor(ctx)
            try:
                assert isinstance(task, asyncio.Task)
                assert mock_queue_probe.call_args.kwargs["ping_fn"] is None
            finally:
                await _cancel(task)


# ============================================================================
# _health_monitor_loop
# ============================================================================


class TestHealthMonitorLoop:
    @pytest.mark.asyncio
    async def test_tick_called_repeatedly(self) -> None:
        from chaoscypher_neuron.worker import _health_monitor_loop

        evaluator = MagicMock()
        evaluator.tick = AsyncMock()

        task = asyncio.create_task(_health_monitor_loop(evaluator, interval=0.02))
        await asyncio.sleep(0.08)
        await _cancel(task)

        assert evaluator.tick.await_count >= 2

    @pytest.mark.asyncio
    async def test_tick_exception_swallowed(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        from chaoscypher_neuron.worker import _health_monitor_loop

        recovered = asyncio.Event()
        call_count = 0

        async def flaky_tick() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("tick boom")
            recovered.set()

        evaluator = MagicMock()
        evaluator.tick = AsyncMock(side_effect=flaky_tick)

        task = asyncio.create_task(_health_monitor_loop(evaluator, interval=0.01))
        try:
            with caplog.at_level(logging.ERROR):
                await asyncio.wait_for(recovered.wait(), timeout=5.0)
        finally:
            await _cancel(task)

        assert call_count >= 2, "loop should have retried after the first tick raised"
        assert any("health_monitor_tick_failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_cancellable_during_sleep(self) -> None:
        from chaoscypher_neuron.worker import _health_monitor_loop

        evaluator = MagicMock()
        evaluator.tick = AsyncMock()

        task = asyncio.create_task(_health_monitor_loop(evaluator, interval=3600))
        await asyncio.sleep(0.01)
        await _cancel(task)
        assert task.done()


# ============================================================================
# run_worker tail (post-handler-setup block + shutdown finally)
# ============================================================================


def _run_worker_patches(
    *,
    qc: MagicMock,
    llm_handlers: dict,
    ops_handlers: dict,
    storage_adapter: MagicMock,
):
    """Build the context-manager stack that lets ``run_worker`` reach its tail.

    ``QueueWorker.run`` is stubbed to return immediately so the ``finally``
    shutdown block executes. All ``_setup_*`` helpers return None so the tail's
    cancel arms take their ``is not None`` False branch (kept minimal — the
    helpers have their own dedicated tests above).
    """
    from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS

    qc.client = MagicMock()  # truthy: connected
    qc.handlers = {QUEUE_LLM: llm_handlers, QUEUE_OPERATIONS: ops_handlers}
    qc.connect_with_retry = AsyncMock(return_value=True)
    qc.disconnect = AsyncMock()

    settings = _make_mock_settings()
    settings.queue_recovery.heartbeat_ttl_seconds = 30
    settings.queue_recovery.heartbeat_refresh_interval_seconds = 10
    settings.queue_recovery.worker_reconcile_interval_seconds = 60

    async def populate_ctx(ctx):
        ctx["settings"] = settings
        ctx["current_database"] = "test_db"
        ctx["storage_adapter"] = storage_adapter

    worker_instance = MagicMock()
    worker_instance.run = AsyncMock(return_value=None)

    cfg = {"max_concurrent": 1, "queue_name": "llm", "timeout": 3600, "max_tries": 5}

    return (
        worker_instance,
        settings,
        [
            _patch_upgrade_gate(),
            patch("chaoscypher_neuron.worker.queue_client", qc),
            patch("chaoscypher_neuron.worker.load_worker_config", return_value=cfg),
            patch(
                "chaoscypher_neuron.worker.setup_shared",
                new=AsyncMock(side_effect=populate_ctx),
            ),
            patch("chaoscypher_neuron.worker.setup_llm_handlers", new=AsyncMock()),
            patch("chaoscypher_neuron.worker.setup_operations_handlers", new=AsyncMock()),
            patch("chaoscypher_neuron.worker._run_startup_recovery", new=AsyncMock()),
            patch(
                "chaoscypher_neuron.worker.listen_for_settings_changes",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_neuron.worker._setup_source_recovery",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "chaoscypher_neuron.worker._setup_orphan_task_cleanup",
                return_value=None,
            ),
            patch(
                "chaoscypher_neuron.worker._setup_orphan_files_cleanup",
                return_value=None,
            ),
            patch("chaoscypher_neuron.worker._setup_search_sweep", return_value=None),
            patch("chaoscypher_neuron.worker._setup_health_monitor", return_value=None),
            patch(
                "chaoscypher_neuron.worker._warmup_embedding_model",
                new=AsyncMock(),
            ),
            patch("chaoscypher_neuron.worker.QueueWorker", return_value=worker_instance),
            patch("chaoscypher_neuron.worker._consume_wipe_sentinel", return_value=False),
        ],
    )


class TestRunWorkerTail:
    @pytest.mark.asyncio
    async def test_shutdown_disconnects_and_skips_rehydration_without_session(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """Full tail: handlers present, adapter has no session -> rehydration skipped,
        worker.run returns, finally disconnects queue + adapter.
        """
        from chaoscypher_neuron.worker import run_worker

        qc = MagicMock()
        storage_adapter = MagicMock()
        storage_adapter.session = None  # forces storage_adapter_not_ready skip
        # Make isinstance(storage_adapter, SqliteAdapter) True so disconnect path runs.
        from chaoscypher_core.adapters.sqlite import SqliteAdapter

        storage_adapter.__class__ = SqliteAdapter

        worker_instance, _settings, ctx_managers = _run_worker_patches(
            qc=qc,
            llm_handlers={"op_a": object()},
            ops_handlers={"op_b": object()},
            storage_adapter=storage_adapter,
        )

        with contextlib.ExitStack() as stack:
            for cm in ctx_managers:
                stack.enter_context(cm)
            with caplog.at_level(logging.WARNING):
                await asyncio.wait_for(run_worker(), timeout=10.0)

        worker_instance.run.assert_awaited_once()
        qc.disconnect.assert_awaited_once()
        storage_adapter.disconnect.assert_called_once()
        messages = [r.message for r in caplog.records]
        assert any("queue_rehydration_skipped" in m for m in messages), messages

    @pytest.mark.asyncio
    async def test_empty_handlers_log_errors(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """Empty llm + ops handler dicts each log a 'no_*_handlers_registered' error."""
        from chaoscypher_neuron.worker import run_worker

        qc = MagicMock()
        storage_adapter = MagicMock()
        storage_adapter.session = None

        worker_instance, _settings, ctx_managers = _run_worker_patches(
            qc=qc,
            llm_handlers={},  # empty -> error log
            ops_handlers={},  # empty -> error log
            storage_adapter=storage_adapter,
        )

        with contextlib.ExitStack() as stack:
            for cm in ctx_managers:
                stack.enter_context(cm)
            with caplog.at_level(logging.ERROR):
                await asyncio.wait_for(run_worker(), timeout=10.0)

        messages = [r.message for r in caplog.records]
        assert any("no_llm_handlers_registered" in m for m in messages), messages
        assert any("no_ops_handlers_registered" in m for m in messages), messages
        worker_instance.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rehydration_runs_when_session_present(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """With a non-None adapter.session, rehydrate_queue_from_db is awaited."""
        from chaoscypher_neuron.worker import run_worker

        qc = MagicMock()
        storage_adapter = MagicMock()
        storage_adapter.session = MagicMock()  # ready -> rehydration runs

        worker_instance, _settings, ctx_managers = _run_worker_patches(
            qc=qc,
            llm_handlers={"op_a": object()},
            ops_handlers={"op_b": object()},
            storage_adapter=storage_adapter,
        )

        with contextlib.ExitStack() as stack:
            for cm in ctx_managers:
                stack.enter_context(cm)
            mock_rehydrate = stack.enter_context(
                patch(
                    "chaoscypher_core.queue.rehydrate.rehydrate_queue_from_db",
                    new=AsyncMock(return_value=3),
                )
            )
            with caplog.at_level(logging.WARNING):
                await asyncio.wait_for(run_worker(), timeout=10.0)

        mock_rehydrate.assert_awaited_once()
        worker_instance.run.assert_awaited_once()
        messages = [r.message for r in caplog.records]
        assert any("queue_rehydrated_from_db" in m for m in messages), messages

    @pytest.mark.asyncio
    async def test_shutdown_cancels_background_tasks(self) -> None:
        """The finally block cancels the _setup_* tasks that are not None."""
        from chaoscypher_neuron.worker import run_worker

        qc = MagicMock()
        storage_adapter = MagicMock()
        storage_adapter.session = None

        worker_instance, _settings, ctx_managers = _run_worker_patches(
            qc=qc,
            llm_handlers={"op_a": object()},
            ops_handlers={"op_b": object()},
            storage_adapter=storage_adapter,
        )

        # Replace the None-returning setup patches with ones that return real,
        # long-sleeping tasks so the cancel arms in `finally` are exercised.
        created_tasks: list[asyncio.Task] = []

        def _make_task(*_a, **_k):
            t = asyncio.create_task(asyncio.sleep(3600))
            created_tasks.append(t)
            return t

        async def _make_task_async(*_a, **_k):
            return _make_task()

        with contextlib.ExitStack() as stack:
            for cm in ctx_managers:
                stack.enter_context(cm)
            # Override the four sync setup helpers + the async one.
            stack.enter_context(
                patch("chaoscypher_neuron.worker._setup_orphan_task_cleanup", _make_task)
            )
            stack.enter_context(
                patch("chaoscypher_neuron.worker._setup_orphan_files_cleanup", _make_task)
            )
            stack.enter_context(patch("chaoscypher_neuron.worker._setup_search_sweep", _make_task))
            stack.enter_context(
                patch("chaoscypher_neuron.worker._setup_health_monitor", _make_task)
            )
            stack.enter_context(
                patch(
                    "chaoscypher_neuron.worker._setup_source_recovery",
                    new=AsyncMock(side_effect=_make_task_async),
                )
            )
            await asyncio.wait_for(run_worker(), timeout=10.0)

        worker_instance.run.assert_awaited_once()
        assert created_tasks, "expected setup helpers to have created tasks"
        for t in created_tasks:
            assert t.cancelled() or t.done(), "shutdown must cancel each background task"


# ============================================================================
# main()
# ============================================================================


class TestMain:
    def test_main_configures_logging_and_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() configures logging from env and dispatches asyncio.run."""
        from chaoscypher_neuron import worker

        monkeypatch.setenv("USE_JSON_LOGGING", "true")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        def _consume(coro):
            # Close the coroutine so it isn't reported as "never awaited".
            coro.close()

        with (
            patch.object(worker, "configure_logging") as mock_cfg,
            patch("asyncio.run", side_effect=_consume) as mock_run,
        ):
            worker.main()

        mock_cfg.assert_called_once_with(use_json=True, log_level="DEBUG")
        mock_run.assert_called_once()

    def test_main_defaults_when_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without env vars, defaults to JSON off + INFO level."""
        from chaoscypher_neuron import worker

        monkeypatch.delenv("USE_JSON_LOGGING", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        def _consume(coro):
            coro.close()

        with (
            patch.object(worker, "configure_logging") as mock_cfg,
            patch("asyncio.run", side_effect=_consume) as mock_run,
        ):
            worker.main()

        mock_cfg.assert_called_once_with(use_json=False, log_level="INFO")
        mock_run.assert_called_once()
