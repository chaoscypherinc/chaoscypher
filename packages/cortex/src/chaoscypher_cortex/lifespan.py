# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lifespan management and long-running background tasks for Cortex.

Defines the FastAPI lifespan context manager (`lifespan_full`) plus the
background loops it spawns: queue-reconciliation safety net,
chat-stuck sweeper, health-probe evaluator, and backup scheduler.
All of these are registered at app-startup time and torn down cleanly on
shutdown.

Source recovery is owned exclusively by Neuron's worker (audit
fix #H1 / Decision 4). Cortex used to run a parallel
reconciliation loop here; the two reconcilers raced and
double-incremented recovery_attempts. The Cortex tier remains
the API/UI surface, the chat-stuck sweeper, and the queue
safety net (_cortex_reconcile_safety_net_loop, which scans
queue rows — not source rows).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ConfigError
from chaoscypher_core.utils.task_callbacks import log_task_exception

# Import boot first to ensure configure_logging() has run before any
# structlog logger in this module is created.
from chaoscypher_cortex import boot as _boot  # noqa: F401
from chaoscypher_cortex.shutdown import CortexShutdownState


if TYPE_CHECKING:
    from fastapi import FastAPI

    from chaoscypher_core.app_config import Settings


logger = structlog.get_logger(__name__)


async def _connect_queue(settings: Settings) -> None:
    """Connect to Valkey queue with retry logic.

    Best-effort: cortex tolerates a missing queue and starts without
    queue support. The retry loop handles the Valkey startup race in the
    all-in-one container.
    """
    from chaoscypher_core.queue import queue_client

    if await queue_client.connect_with_retry(settings, required=False, delay_cap=10.0):
        logger.info("queue_client_connected", backend="valkey")


async def _backup_scheduler(settings: Settings) -> None:
    """Run scheduled backups based on configuration.

    Re-reads settings on each iteration so changes to backup interval,
    retention, or enabled flag take effect without restart. Uses a file
    lock to prevent duplicate backups when multiple uvicorn workers are
    running.

    Cancellation handling: both the initial pre-loop sleep and each loop
    iteration are wrapped in explicit ``except asyncio.CancelledError``
    blocks that emit a structured-log breadcrumb (events
    ``backup_scheduler_cancelled_during_initial_sleep`` and
    ``backup_scheduler_cancelled`` respectively) and re-raise so the task
    unwinds cleanly.

    Args:
        settings: Initial startup settings (used for first interval wait).

    """
    from chaoscypher_core import policy
    from chaoscypher_core.app_config import get_settings as _get_settings
    from chaoscypher_core.services.backup import BackupService

    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]  # Windows dev — single worker, no lock needed

    lock_path = Path(settings.paths.data_dir) / "backups" / ".scheduler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Initial wait — always uses startup settings regardless of runtime changes
    interval = policy.BACKUP_INTERVAL_PRESETS.get(settings.backup.interval, policy.SECONDS_PER_DAY)
    try:
        await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("backup_scheduler_cancelled_during_initial_sleep")
        raise

    while True:
        try:
            current = _get_settings()
            if current.backup.enabled:
                acquired = False
                lock_file = None
                try:
                    if fcntl is not None:
                        lock_file = await asyncio.to_thread(open, lock_path, "w")
                        try:
                            await asyncio.to_thread(
                                fcntl.flock,
                                lock_file,
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                            acquired = True
                        except OSError:
                            logger.debug("backup_scheduler_skipped_lock_held")
                            await asyncio.to_thread(lock_file.close)
                            lock_file = None
                    else:
                        acquired = True  # type: ignore[unreachable]  # Windows dev — single worker, no lock needed

                    if acquired:
                        backup_service = BackupService(
                            str(current.data_dir),
                            current.backup.backup_dir,
                        )
                        result = await asyncio.to_thread(
                            backup_service.create_backup, current.current_database
                        )
                        removed = await asyncio.to_thread(
                            backup_service.cleanup_old_backups,
                            current.current_database,
                            current.backup.retention_count,
                        )
                        logger.info(
                            "scheduled_backup_completed",
                            removed_old=removed,
                            **result,
                        )
                except Exception:
                    logger.exception("scheduled_backup_failed")
                finally:
                    if lock_file is not None:
                        if fcntl is not None:
                            await asyncio.to_thread(
                                fcntl.flock,
                                lock_file,
                                fcntl.LOCK_UN,
                            )
                        await asyncio.to_thread(lock_file.close)
            # Re-read interval in case it changed
            interval = policy.BACKUP_INTERVAL_PRESETS.get(
                current.backup.interval, policy.SECONDS_PER_DAY
            )
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("backup_scheduler_cancelled")
            raise


async def _cortex_reconcile_safety_net_loop(
    *,
    service: Any,
    interval_seconds: int,
    should_shutdown: Any,
) -> None:
    """Cortex-side safety-net queue reconciliation.

    Runs ``service.force_reconcile(queue_name=None)`` every
    ``interval_seconds`` until ``should_shutdown()`` returns True. This
    complements the worker's reconcile_loop by guaranteeing reconciliation
    even if all workers for a queue are restarting or absent.

    Per-iteration errors are logged but do not kill the loop — a transient
    Valkey blip should not permanently disable the safety net.

    Args:
        service: A ``QueueService`` with a ``force_reconcile`` method.
            Untyped here to avoid a circular import at module level.
        interval_seconds: Cadence between passes.
        should_shutdown: Zero-arg async callable returning True when the
            loop should exit.
    """
    loop_logger = structlog.get_logger(__name__)
    while not await should_shutdown():
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            stats = await service.force_reconcile(queue_name=None)
            if sum(stats.values()) > 0:
                loop_logger.warning("cortex_safety_net_reconciled", **stats)
        except Exception as exc:
            loop_logger.exception("cortex_safety_net_error", error=str(exc))


async def _cortex_chat_reconcile_loop(
    *,
    list_databases: Any,
    chat_recovery_fn: Any,
    interval_seconds: float,
    should_shutdown: Any,
) -> None:
    """Cortex-side chat-stuck sweeper loop.

    Sweeps every database for chats stuck in "processing" with no active
    worker and moves them to "error". Runs on a periodic cadence.

    Source recovery is NOT performed here — that is owned exclusively by
    the Neuron worker (audit fix #H1 / Decision 4).

    Per-iteration errors are logged but do NOT kill the loop: a transient
    SQLite contention on one database must not permanently disable the
    sweeper for other databases.

    Args:
        list_databases: Zero-arg callable returning a list of active
            database names.
        chat_recovery_fn: Callable taking a database_name and returning
            the number of chats recovered.
        interval_seconds: Sleep between passes (float so tests can run
            sub-second ticks).
        should_shutdown: Zero-arg async callable returning True when the
            loop should exit cleanly.
    """
    loop_logger = structlog.get_logger(__name__)
    while not await should_shutdown():
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            dbs = list_databases()
            for db in dbs:
                # Chat status sweep: find "processing" chats with no
                # active worker and move them to "error".
                try:
                    chat_recovered = chat_recovery_fn(db)
                    if chat_recovered:
                        loop_logger.warning(
                            "cortex_chat_recovery_acted",
                            database=db,
                            chats_recovered=chat_recovered,
                        )
                except Exception as chat_exc:
                    loop_logger.exception(
                        "cortex_chat_recovery_error",
                        database=db,
                        error_type=type(chat_exc).__name__,
                    )
        except Exception as exc:
            loop_logger.exception(
                "cortex_chat_safety_net_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )


async def _health_evaluator_loop(
    *,
    evaluator: Any,
    interval_seconds: float,
    should_shutdown: Any,
) -> None:
    """Cortex-side periodic health evaluation loop.

    Runs ``evaluator.tick()`` every ``interval_seconds`` until
    ``should_shutdown()`` returns True. System-level (not per-database).

    Per-tick errors are logged but do not kill the loop.

    Args:
        evaluator: A HealthPauseEvaluator instance.
        interval_seconds: Cadence between ticks.
        should_shutdown: Zero-arg async callable returning True when the
            loop should exit.
    """
    while not await should_shutdown():
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            await evaluator.tick()
        except Exception:
            logger.exception("health_evaluator_tick_failed")


def _start_chat_recovery_loop(
    settings: Settings,
) -> tuple[asyncio.Task[None], dict[str, bool]]:
    """Start the chat-stuck sweeper loop.

    Launches the background task that sweeps every known database for
    chats stuck in "processing" with no active worker and moves them to
    "error". Source recovery is NOT performed here — that is owned
    exclusively by the Neuron worker (audit fix #H1 / Decision 4).

    Args:
        settings: Application settings with ``paths`` and
            ``source_recovery`` attributes (reuses the same scan interval).

    Returns:
        Tuple of (asyncio task, shutdown flag dict).

    """
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.database.repository import DatabaseRepository
    from chaoscypher_core.services.chat.recovery import reconcile_stuck_chats

    shutdown_flag: dict[str, bool] = {"value": False}

    async def _should_shutdown() -> bool:
        """Return True once the lifespan signals chat-recovery shutdown."""
        return shutdown_flag["value"]

    def _list_db_names() -> list[str]:
        db_repo = DatabaseRepository(data_root=str(settings.paths.data_dir))
        return [d.name for d in db_repo.list_databases()]

    def _recover_stuck_chats(db_name: str) -> int:
        db_repo = DatabaseRepository(data_root=str(settings.paths.data_dir))
        db_path = db_repo.get_database_path(db_name)
        if db_path is None:
            msg = f"invalid database path for {db_name!r} (path traversal rejected)"
            raise ConfigError(msg)
        adapter = SqliteAdapter(
            db_path=db_path,
            database_name=db_name,
        )
        adapter.connect()
        try:
            return reconcile_stuck_chats(adapter, db_name)
        finally:
            adapter.disconnect()

    task = asyncio.create_task(
        _cortex_chat_reconcile_loop(
            list_databases=_list_db_names,
            chat_recovery_fn=_recover_stuck_chats,
            interval_seconds=settings.source_recovery.cortex_scan_interval_seconds,
            should_shutdown=_should_shutdown,
        )
    )
    task.add_done_callback(log_task_exception)
    return task, shutdown_flag


def _start_health_monitor(
    settings: Settings,
) -> tuple[asyncio.Task[None] | None, dict[str, bool], Any]:
    """Start the periodic health evaluation monitor.

    If the health monitor is enabled in settings, configures disk-space
    probes, the event bus, and launches a background loop. Otherwise logs
    that monitoring is disabled and returns ``None`` for the task.

    Args:
        settings: Application settings with ``health_monitor``, ``paths``,
            and ``current_database`` attributes.

    Returns:
        Tuple of (asyncio task or None, shutdown flag dict, health adapter or None).

    """
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.database.repository import DatabaseRepository

    shutdown_flag: dict[str, bool] = {"value": False}

    async def _should_shutdown() -> bool:
        """Return True once the lifespan signals health-monitor shutdown."""
        return shutdown_flag["value"]

    _hm = settings.health_monitor
    if not _hm.enabled:
        logger.info("health_monitor_disabled")
        return None, shutdown_flag, None

    from chaoscypher_core.services.events.health.pause_evaluator import HealthPauseEvaluator
    from chaoscypher_core.services.events.health.probes.disk_space import DiskSpaceProbe
    from chaoscypher_core.services.events.health.registry import HealthRegistry

    _h_db_repo = DatabaseRepository(data_root=str(settings.paths.data_dir))
    _h_db_path = _h_db_repo.get_database_path(settings.current_database)
    if _h_db_path is None:
        msg = f"invalid database path for {settings.current_database!r} (path traversal rejected)"
        raise ConfigError(msg)
    _h_adapter = SqliteAdapter(
        db_path=_h_db_path,
        database_name=settings.current_database,
    )
    _h_adapter.connect()

    # Configure the centralized event bus so emit() calls persist events.
    from chaoscypher_core.services.events import event_bus

    event_bus.configure(_h_adapter)

    _h_registry = HealthRegistry()
    _h_registry.register(
        DiskSpaceProbe(
            path=str(settings.paths.data_dir),
            warn_bytes=_hm.disk_warn_bytes,
            error_bytes=_hm.disk_error_bytes,
        )
    )

    _h_evaluator = HealthPauseEvaluator(
        registry=_h_registry,
        adapter=_h_adapter,
        trip_threshold=_hm.trip_threshold,
        clear_threshold=_hm.clear_threshold,
    )
    task = asyncio.create_task(
        _health_evaluator_loop(
            evaluator=_h_evaluator,
            interval_seconds=_hm.check_interval_seconds,
            should_shutdown=_should_shutdown,
        )
    )
    task.add_done_callback(log_task_exception)
    logger.info(
        "health_monitor_started",
        interval_seconds=_hm.check_interval_seconds,
        trip_threshold=_hm.trip_threshold,
    )
    return task, shutdown_flag, _h_adapter


@asynccontextmanager
async def lifespan_full(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan context manager for Full mode.

    Initializes:
    - ConfigManager
    - Queue client (Valkey)
    - Database connection
    """
    # Startup
    logger.info("cortex_api_starting", mode="full", architecture="VSA")

    from chaoscypher_core.app_config import get_settings

    settings = get_settings()

    await _connect_queue(settings)
    logger.info("shared_services_initialized")

    # Check for sources with outdated quality score caches (non-blocking)
    try:
        from chaoscypher_cortex.features.quality.startup import (
            queue_outdated_quality_score_recalculation,
        )

        await queue_outdated_quality_score_recalculation(settings)
    except Exception as e:
        # Don't fail startup if score check fails
        logger.warning(
            "quality_score_version_check_failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )

    # Start MCP server
    from chaoscypher_cortex.features.mcp.service import get_mcp_manager

    mcp_manager = get_mcp_manager()

    try:
        await mcp_manager.start()
    except Exception as e:
        logger.warning("mcp_startup_failed", error=str(e))

    backup_task = asyncio.create_task(_backup_scheduler(settings))
    backup_task.add_done_callback(log_task_exception)

    # Self-healing: Cortex-side safety-net reconciliation
    from chaoscypher_cortex.features.queue.service import QueueService

    _safety_net_shutdown = {"value": False}

    async def _should_shutdown() -> bool:
        """Return True once the lifespan signals safety-net loop shutdown."""
        return _safety_net_shutdown["value"]

    safety_net_task = asyncio.create_task(
        _cortex_reconcile_safety_net_loop(
            service=QueueService(),
            interval_seconds=settings.queue_recovery.cortex_reconcile_interval_seconds,
            should_shutdown=_should_shutdown,
        )
    )
    safety_net_task.add_done_callback(log_task_exception)

    # Chat-stuck sweeper (source recovery is owned by Neuron — audit fix #H1)
    chat_safety_task, _chat_safety_shutdown = _start_chat_recovery_loop(settings)

    # Health monitor
    health_eval_task, _health_shutdown, _h_adapter = _start_health_monitor(settings)

    logger.info(
        "cortex_api_ready",
        mode="full",
        host="0.0.0.0",
        port=settings.ports.web_ui_api,
        url=f"http://localhost:{settings.ports.web_ui_api}",
    )

    # Expose shutdown state on app.state so dispatch endpoints can guard
    # against new work during shutdown.
    _shutdown_state = CortexShutdownState()
    app.state.shutdown_state = _shutdown_state

    yield

    # Shutdown
    _shutdown_state.initiate()
    logger.info(
        "cortex_api_shutting_down",
        mode="full",
        grace_seconds=settings.shutdown.cortex_shutdown_grace_seconds,
    )

    _safety_net_shutdown["value"] = True
    safety_net_task.cancel()
    with suppress(asyncio.CancelledError):
        await safety_net_task

    _chat_safety_shutdown["value"] = True
    chat_safety_task.cancel()
    with suppress(asyncio.CancelledError):
        await chat_safety_task

    _health_shutdown["value"] = True
    if health_eval_task is not None:
        health_eval_task.cancel()
        with suppress(asyncio.CancelledError):
            await health_eval_task
    if _h_adapter is not None:
        with suppress(Exception):
            _h_adapter.disconnect()

    backup_task.cancel()
    with suppress(asyncio.CancelledError):
        await backup_task

    import contextlib

    with contextlib.suppress(Exception):
        await mcp_manager.stop()

    from chaoscypher_core.queue import queue_client

    await queue_client.disconnect()
    logger.info("queue_client_disconnected")
