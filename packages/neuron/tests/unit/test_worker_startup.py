# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for worker startup (run_worker entry point).

Covers RuntimeError on queue connection failure, worker context
initialization, and startup recovery invocation.
"""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# Helpers
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
    resolves the developer's real local DB (under ``%LOCALAPPDATA%``) and,
    if it sits behind unapplied migrations, the worker spins on
    ``asyncio.sleep`` until the surrounding ``wait_for`` raises
    ``TimeoutError``. The four startup-flow tests in this file don't
    exercise the upgrade gate at all — they care about queue connection,
    context population, and config loading — so we stub both helpers to
    short-circuit it.
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


# ============================================================================
# Queue Connection Failure
# ============================================================================


class TestQueueConnectionFailure:
    """Tests that run_worker raises RuntimeError when queue connection fails."""

    @pytest.mark.asyncio
    async def test_raises_runtime_error_on_connection_failure(self) -> None:
        """run_worker raises RuntimeError if queue client never connects."""
        mock_settings = _make_mock_settings()
        mock_settings.queue.connection_max_retries = 2
        mock_settings.queue.connection_retry_delay = 0.001

        with (
            _patch_upgrade_gate(),
            patch(
                "chaoscypher_neuron.worker.load_worker_config",
                return_value={
                    "max_concurrent": 1,
                    "queue_name": "llm",
                    "timeout": 3600,
                    "max_tries": 5,
                },
            ),
            patch(
                "chaoscypher_neuron.worker.setup_shared",
                new_callable=AsyncMock,
            ) as mock_setup_shared,
            patch(
                "chaoscypher_neuron.worker.queue_client",
            ) as mock_qc,
        ):
            # queue_client.client stays None and connect_with_retry raises
            # the same RuntimeError the production wrapper would emit after
            # all retries fail (see ``QueueClient.connect_with_retry``).
            mock_qc.client = None
            mock_qc.connect = AsyncMock(return_value=False)
            mock_qc.connect_with_retry = AsyncMock(
                side_effect=RuntimeError(
                    "Queue server connection failed after 2 attempts. "
                    "Ensure Valkey is running and reachable."
                )
            )

            async def populate_ctx(ctx):
                ctx["settings"] = mock_settings
                ctx["current_database"] = "test_db"
                ctx["storage_adapter"] = MagicMock()

            mock_setup_shared.side_effect = populate_ctx

            with pytest.raises(RuntimeError, match="Queue server connection failed"):
                await asyncio.wait_for(
                    _import_and_run_worker(),
                    timeout=10.0,
                )

    @pytest.mark.asyncio
    async def test_raises_runtime_error_after_all_retries_exhausted(self) -> None:
        """run_worker raises after exhausting all retry attempts."""
        mock_settings = _make_mock_settings()
        mock_settings.queue.connection_max_retries = 3
        mock_settings.queue.connection_retry_delay = 0.001

        connect_call_count = 0

        async def mock_connect(settings):
            nonlocal connect_call_count
            connect_call_count += 1
            return False

        with (
            _patch_upgrade_gate(),
            patch(
                "chaoscypher_neuron.worker.load_worker_config",
                return_value={
                    "max_concurrent": 1,
                    "queue_name": "llm",
                    "timeout": 3600,
                    "max_tries": 5,
                },
            ),
            patch(
                "chaoscypher_neuron.worker.setup_shared",
                new_callable=AsyncMock,
            ) as mock_setup_shared,
            patch(
                "chaoscypher_neuron.worker.queue_client",
            ) as mock_qc,
        ):
            mock_qc.client = None
            mock_qc.connect = mock_connect

            # Drive ``connect_with_retry`` to call our stubbed ``connect``
            # so we can still assert retry behaviour while matching the
            # production wrapper's signature/raise semantics.
            async def fake_connect_with_retry(settings, *, required, delay_cap):
                for _ in range(mock_settings.queue.connection_max_retries):
                    if await mock_connect(settings):
                        return True
                if required:
                    raise RuntimeError(
                        f"Queue server connection failed after "
                        f"{mock_settings.queue.connection_max_retries} attempts."
                    )
                return False

            mock_qc.connect_with_retry = fake_connect_with_retry

            async def populate_ctx(ctx):
                ctx["settings"] = mock_settings
                ctx["current_database"] = "test_db"
                ctx["storage_adapter"] = MagicMock()

            mock_setup_shared.side_effect = populate_ctx

            with pytest.raises(RuntimeError):
                await asyncio.wait_for(
                    _import_and_run_worker(),
                    timeout=10.0,
                )

            # Should have retried (max_retries - 1 reconnect attempts, since first check is free)
            assert connect_call_count >= 1


# ============================================================================
# Worker Context Initialization
# ============================================================================


class TestWorkerContextInitialization:
    """Tests that worker context is properly initialized."""

    @pytest.mark.asyncio
    async def test_setup_shared_populates_context(self) -> None:
        """setup_shared is called and populates required context keys."""
        mock_settings = _make_mock_settings()
        captured_ctx = {}

        async def capture_setup_shared(ctx):
            ctx["settings"] = mock_settings
            ctx["current_database"] = "test_db"
            ctx["storage_adapter"] = MagicMock()
            captured_ctx.update(ctx)

        with (
            _patch_upgrade_gate(),
            patch(
                "chaoscypher_neuron.worker.load_worker_config",
                return_value={
                    "max_concurrent": 1,
                    "queue_name": "llm",
                    "timeout": 3600,
                    "max_tries": 5,
                },
            ),
            patch(
                "chaoscypher_neuron.worker.setup_shared",
                side_effect=capture_setup_shared,
            ),
            patch(
                "chaoscypher_neuron.worker.queue_client",
            ) as mock_qc,
        ):
            mock_qc.client = None
            mock_qc.connect = AsyncMock(return_value=False)
            mock_qc.connect_with_retry = AsyncMock(
                side_effect=RuntimeError("Queue server connection failed")
            )
            mock_settings.queue.connection_max_retries = 1
            mock_settings.queue.connection_retry_delay = 0.001

            with pytest.raises(RuntimeError):
                await asyncio.wait_for(
                    _import_and_run_worker(),
                    timeout=10.0,
                )

        # Verify context was populated
        assert "settings" in captured_ctx
        assert "current_database" in captured_ctx
        assert captured_ctx["current_database"] == "test_db"


# ============================================================================
# Startup Recovery
# ============================================================================


class TestStartupRecovery:
    """Tests that startup recovery is called before worker starts."""

    @pytest.mark.asyncio
    async def test_startup_recovery_called(self) -> None:
        """_run_startup_recovery is called during worker startup."""
        from chaoscypher_neuron.worker import _run_startup_recovery

        mock_settings = _make_mock_settings()
        mock_adapter = MagicMock()

        ctx = {
            "settings": mock_settings,
            "current_database": "test_db",
            "storage_adapter": mock_adapter,
        }

        with (
            patch(
                "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
                new_callable=AsyncMock,
                return_value={"recovered": 0, "skipped": 0, "failed": 0},
            ) as mock_task_recovery,
            patch(
                "chaoscypher_neuron.worker.recover_stuck_sources",
                new_callable=AsyncMock,
                return_value={"reset": 0, "marked_failed": 0},
            ) as mock_source_recovery,
        ):
            await _run_startup_recovery(ctx)

        mock_task_recovery.assert_called_once_with(
            adapter=mock_adapter,
            database_name="test_db",
            settings=mock_settings,
        )
        mock_source_recovery.assert_called_once_with(
            adapter=mock_adapter,
            database_name="test_db",
        )

    @pytest.mark.asyncio
    async def test_startup_recovery_skipped_without_adapter(self) -> None:
        """_run_startup_recovery skips if storage_adapter is None."""
        from chaoscypher_neuron.worker import _run_startup_recovery

        mock_settings = _make_mock_settings()

        ctx = {
            "settings": mock_settings,
            "current_database": "test_db",
            # No storage_adapter
        }

        with patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new_callable=AsyncMock,
        ) as mock_task_recovery:
            await _run_startup_recovery(ctx)

        # Should not have been called
        mock_task_recovery.assert_not_called()

    @pytest.mark.asyncio
    async def test_startup_recovery_handles_exception(self) -> None:
        """_run_startup_recovery catches and logs exceptions without crashing."""
        from chaoscypher_neuron.worker import _run_startup_recovery

        mock_settings = _make_mock_settings()
        mock_adapter = MagicMock()

        ctx = {
            "settings": mock_settings,
            "current_database": "test_db",
            "storage_adapter": mock_adapter,
        }

        with patch(
            "chaoscypher_neuron.worker.recover_orphaned_extraction_tasks",
            new_callable=AsyncMock,
            side_effect=RuntimeError("recovery failed"),
        ):
            # Should not raise
            await _run_startup_recovery(ctx)


# ============================================================================
# Config Loading
# ============================================================================


class TestConfigLoading:
    """Tests that both queue configs are loaded during startup."""

    @pytest.mark.asyncio
    async def test_both_configs_loaded(self) -> None:
        """run_worker loads configs for both llm_worker and operations_worker."""
        mock_settings = _make_mock_settings()
        mock_settings.queue.connection_max_retries = 1
        mock_settings.queue.connection_retry_delay = 0.001

        loaded_configs = []

        def capture_load(worker_type):
            loaded_configs.append(worker_type)
            return {
                "max_concurrent": 1,
                "queue_name": "llm" if worker_type == "llm_worker" else "operations",
                "timeout": 3600,
                "max_tries": 5,
            }

        with (
            _patch_upgrade_gate(),
            patch(
                "chaoscypher_neuron.worker.load_worker_config",
                side_effect=capture_load,
            ),
            patch(
                "chaoscypher_neuron.worker.setup_shared",
                new_callable=AsyncMock,
            ) as mock_setup_shared,
            patch(
                "chaoscypher_neuron.worker.queue_client",
            ) as mock_qc,
        ):
            mock_qc.client = None
            mock_qc.connect = AsyncMock(return_value=False)
            mock_qc.connect_with_retry = AsyncMock(
                side_effect=RuntimeError("Queue server connection failed")
            )

            async def populate_ctx(ctx):
                ctx["settings"] = mock_settings
                ctx["current_database"] = "test_db"
                ctx["storage_adapter"] = MagicMock()

            mock_setup_shared.side_effect = populate_ctx

            with pytest.raises(RuntimeError):
                await asyncio.wait_for(
                    _import_and_run_worker(),
                    timeout=10.0,
                )

        assert "llm_worker" in loaded_configs
        assert "operations_worker" in loaded_configs


# ============================================================================
# Orphan Task Cleanup Loop
# ============================================================================


class TestOrphanTaskCleanupLoop:
    """Tests for the _orphan_task_cleanup_loop background task."""

    @pytest.mark.asyncio
    async def test_calls_adapter_on_interval(self) -> None:
        """The cleanup loop calls adapter.cleanup_orphaned_chunk_tasks every interval."""
        import contextlib

        from chaoscypher_neuron.worker import _orphan_task_cleanup_loop

        calls = []

        class FakeAdapter:
            @contextlib.asynccontextmanager
            async def session_scope(self):  # type: ignore[no-untyped-def]
                yield None

            def cleanup_orphaned_chunk_tasks(self, *, older_than_seconds: int) -> int:
                calls.append(older_than_seconds)
                return 0

        adapter = FakeAdapter()

        task = asyncio.create_task(
            _orphan_task_cleanup_loop(
                adapter=adapter,
                retention_days=7,
                interval_seconds=0.05,
            )
        )
        await asyncio.sleep(0.18)  # ~3 intervals
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(calls) >= 2, (
            f"Expected >=2 calls in ~0.18s with 0.05s interval, got {len(calls)}"
        )
        assert all(c == 7 * 86400 for c in calls), f"Unexpected arg values: {calls}"

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self) -> None:
        """Transient adapter errors don't kill the loop — it retries next interval."""
        import contextlib

        from chaoscypher_neuron.worker import _orphan_task_cleanup_loop

        call_count = 0
        recovered = asyncio.Event()

        class FakeAdapter:
            @contextlib.asynccontextmanager
            async def session_scope(self):  # type: ignore[no-untyped-def]
                yield None

            def cleanup_orphaned_chunk_tasks(self, *, older_than_seconds: int) -> int:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("simulated DB error")
                # The second call proves the loop retried after the error.
                recovered.set()
                return 5

        adapter = FakeAdapter()

        task = asyncio.create_task(
            _orphan_task_cleanup_loop(
                adapter=adapter,
                retention_days=7,
                interval_seconds=0.01,
            )
        )
        try:
            # Event-based wait — deterministic under parallel load, unlike a
            # fixed sleep that can starve and under-count loop iterations.
            await asyncio.wait_for(recovered.wait(), timeout=5.0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert call_count >= 2, "Loop should have recovered from the first error and retried"

    @pytest.mark.asyncio
    async def test_cancellable_during_sleep(self) -> None:
        """Loop exits cleanly when cancelled while sleeping."""
        import contextlib

        from chaoscypher_neuron.worker import _orphan_task_cleanup_loop

        class FakeAdapter:
            @contextlib.asynccontextmanager
            async def session_scope(self):  # type: ignore[no-untyped-def]
                yield None

            def cleanup_orphaned_chunk_tasks(self, *, older_than_seconds: int) -> int:
                return 0

        adapter = FakeAdapter()

        # Very long interval so it's always sleeping when cancelled
        task = asyncio.create_task(
            _orphan_task_cleanup_loop(
                adapter=adapter,
                retention_days=7,
                interval_seconds=3600,
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert task.done(), "Task should be done after cancellation"


# ============================================================================
# AOF Wipe Sentinel (Task 5.5)
# ============================================================================


class TestConsumeWipeSentinel:
    """Tests for _consume_wipe_sentinel helper."""

    def test_sentinel_present_returns_true_and_deletes(self, tmp_path) -> None:
        """When the sentinel exists it is deleted and the helper returns True."""
        from chaoscypher_neuron.worker import _consume_wipe_sentinel

        sentinel = tmp_path / ".valkey_was_wiped"
        sentinel.touch()

        result = _consume_wipe_sentinel(tmp_path)

        assert result is True
        assert not sentinel.exists(), "Sentinel file should be deleted after detection"

    def test_sentinel_absent_returns_false(self, tmp_path) -> None:
        """When no sentinel exists the helper returns False without error."""
        from chaoscypher_neuron.worker import _consume_wipe_sentinel

        result = _consume_wipe_sentinel(tmp_path)

        assert result is False

    def test_sentinel_present_logs_warning_with_caplog(
        self,
        tmp_path,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,  # pytest fixture, side-effect only
    ) -> None:
        """Warning log event fires with expected name when sentinel found."""
        import logging

        from chaoscypher_neuron.worker import _consume_wipe_sentinel

        sentinel = tmp_path / ".valkey_was_wiped"
        sentinel.touch()

        with caplog.at_level(logging.WARNING):
            _consume_wipe_sentinel(tmp_path)

        # structlog events reach the stdlib logging bridge as the event string
        # or as part of the formatted message.
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("valkey_aof_was_wiped" in m for m in warning_messages), (
            f"Expected valkey_aof_was_wiped in warning logs, got: {warning_messages}"
        )

    def test_unlink_failure_logs_but_does_not_raise(self, tmp_path, monkeypatch) -> None:
        """OSError during sentinel unlink is caught and logged, not re-raised."""
        from pathlib import Path

        from chaoscypher_neuron.worker import _consume_wipe_sentinel

        sentinel = tmp_path / ".valkey_was_wiped"
        sentinel.touch()

        def failing_unlink(self, _missing_ok=False):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        # Should not raise even when unlink fails
        result = _consume_wipe_sentinel(tmp_path)
        assert result is True


# ============================================================================
# Helper — import run_worker lazily to avoid module-level side effects
# ============================================================================


async def _import_and_run_worker():
    """Import and call run_worker, isolated from module-level logging setup."""
    from chaoscypher_neuron.worker import run_worker

    await run_worker()
