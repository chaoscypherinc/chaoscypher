# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unified Neuron Worker — single process for LLM and Operations queues.

Merges the previously separate ``llm_worker.py`` and ``operations_worker.py``
into one entry point.  Shared resources (database, config, LLM provider)
are initialised once.  Each queue gets its own poller with independent
concurrency control via :class:`~chaoscypher_core.queue.worker.QueueWorker`.

Entry point:
    cc-neuron  (see pyproject.toml [project.scripts])
"""

import asyncio
import contextlib
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.exceptions import RecoveryTimeoutError
from chaoscypher_core.queue import QueueWorker, queue_client
from chaoscypher_core.services.sources.recovery import SourceRecovery

# Configure logging early
from chaoscypher_core.utils.logging.app_config import (
    configure_logging,
    get_logger,
)
from chaoscypher_core.utils.task_callbacks import log_task_exception

# Import worker config using proper package import
from chaoscypher_neuron.config import load_worker_config

# Extracted modules
from chaoscypher_neuron.recovery import recover_orphaned_extraction_tasks, recover_stuck_sources
from chaoscypher_neuron.settings_sync import listen_for_settings_changes
from chaoscypher_neuron.setup import (
    setup_llm_handlers,
    setup_operations_handlers,
    setup_shared,
)


if TYPE_CHECKING:
    from chaoscypher_neuron.types import WorkerContext


# Logger created here is a lazy structlog proxy; the actual configuration
# is applied by ``main()`` (the cc-neuron entry point) before any log call.
# Module import itself has no side effects on global structlog state.
logger = get_logger(__name__)


# ============================================================================
# Startup Recovery
# ============================================================================


async def _run_startup_recovery(ctx: WorkerContext) -> None:
    """Run extraction recovery on startup.

    Args:
        ctx: Typed worker context with shared services.

    """
    settings = ctx["settings"]
    current_database = ctx["current_database"]

    logger.info("extraction_recovery_starting")

    adapter = ctx.get("storage_adapter")
    if adapter is None:
        logger.warning("startup_recovery_skipped", reason="no_storage_adapter")
        return

    try:
        task_recovery = await recover_orphaned_extraction_tasks(
            adapter=adapter,
            database_name=current_database,
            settings=settings,
        )
        logger.info(
            "extraction_task_recovery_completed",
            recovered=task_recovery["recovered"],
            skipped=task_recovery["skipped"],
            failed=task_recovery["failed"],
        )

        source_recovery = await recover_stuck_sources(
            adapter=adapter,
            database_name=current_database,
        )
        logger.info(
            "extraction_source_recovery_completed",
            reset=source_recovery["reset"],
            marked_failed=source_recovery["marked_failed"],
        )
    except Exception as e:
        logger.exception(
            "extraction_recovery_error",
            error=str(e),
            error_type=type(e).__name__,
        )


# ============================================================================
# AOF Wipe Sentinel
# ============================================================================


def _consume_wipe_sentinel(data_dir: Path) -> bool:
    """Detect, log, and delete the Valkey AOF-wipe sentinel file.

    ``valkey-startup.sh`` touches ``<data_dir>/.valkey_was_wiped`` after it
    wipes the AOF directory due to an unrecoverable repair failure.  This
    helper checks for that sentinel at worker startup — before
    ``rehydrate_queue_from_db`` runs — so that ops logs make the wipe visible
    when the recovery path actually matters.

    Args:
        data_dir: Root data directory (typically ``/data`` in Docker).

    Returns:
        ``True`` if the sentinel was found and deleted, ``False`` otherwise.

    """
    sentinel = data_dir / ".valkey_was_wiped"
    if not sentinel.exists():
        return False

    logger.warning(
        "valkey_aof_was_wiped_forcing_queue_rehydration",
        sentinel_path=str(sentinel),
    )
    try:
        sentinel.unlink()
    except OSError as exc:
        logger.warning("valkey_wipe_sentinel_unlink_failed", error=str(exc))

    return True


# ============================================================================
# Background Warmup
# ============================================================================


async def _source_recovery_loop(
    *,
    recovery: SourceRecovery,
    adapter: Any,
    database_name: str,
    interval_seconds: int,
    reconcile_timeout_seconds: int,
) -> None:
    """Periodic SourceRecovery reconcile loop.

    Runs on a fixed interval (default 60s, configured via
    ``SourceRecoverySettings.worker_scan_interval_seconds``), scanning
    for non-terminal sources whose work got dropped and re-dispatching
    missing queue tasks. Cancellable — the shutdown sequence cancels
    the task and awaits it with suppression.

    Args:
        recovery: The SourceRecovery instance (shares adapter + queue
            client with the startup pass).
        adapter: The storage adapter backing ``recovery``; each pass enters
            its ``session_scope()`` so the reconcile runs on a fresh per-task
            session instead of the shared ``_fallback_session``.
        database_name: Active database to scan.
        interval_seconds: Sleep between passes.
        reconcile_timeout_seconds: Per-call timeout for reconcile_database.
            A timed-out pass is logged and skipped; the loop continues.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            # Each pass runs in its own per-task SafeSession so this loop never
            # shares the singleton _fallback_session with the queue handlers or
            # the sibling cleanup loops (the 2026-05-20 silent-data-loss race).
            async with adapter.session_scope(), asyncio.timeout(reconcile_timeout_seconds):
                stats = await recovery.reconcile_database(database_name=database_name)
            if stats.recovered > 0 or stats.skipped_paused > 0:
                logger.warning(
                    "periodic_source_reconcile_acted",
                    database=database_name,
                    **stats.to_dict(),
                )
            else:
                logger.debug(
                    "periodic_source_reconcile_idle",
                    database=database_name,
                    total_scanned=stats.total_scanned,
                )
        except asyncio.CancelledError:
            return
        except TimeoutError:
            logger.warning(
                "source_recovery_reconcile_timeout",
                database_name=database_name,
                timeout_seconds=reconcile_timeout_seconds,
            )
            continue  # next iteration of the periodic loop
        except Exception as exc:
            logger.exception(
                "source_recovery_loop_error",
                database=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )


async def _orphan_files_cleanup_loop(
    *,
    adapter: Any,
    staging_dir: Path,
    database_name: str,
    retention_days: int,
    interval_seconds: int,
    pass_timeout_seconds: int,
) -> None:
    """Periodic cleanup of orphaned source files in the staging directory.

    Runs on a fixed interval (default 24h, configured via
    ``SourceRecoverySettings.orphan_files_cleanup_interval_seconds``),
    removing ``staging_dir/<source_id>/`` directories with no matching
    ``SourceRow.id`` whose mtime is older than ``retention_days`` ago.

    Orphan directories arise when a hard kill (SIGKILL/OOM/container
    crash) lands between the file write and the row commit in
    ``upload_source``. Without this loop they accumulate until they fill
    disk.

    Cancellable — the shutdown sequence cancels the task and awaits it
    with suppression (matches ``_orphan_task_cleanup_loop``).

    Args:
        adapter: SqliteAdapter instance exposing ``list_source_ids``.
        staging_dir: ``settings.database_dir / "sources"`` for the
            active database.
        database_name: Active database name (multi-DB isolation).
        retention_days: Directories older than this many days are
            considered orphaned and eligible for removal.
        interval_seconds: Sleep between passes.
        pass_timeout_seconds: Hard cap on a single cleanup pass.
            ``cleanup_orphan_source_files`` runs in a worker thread; a
            wedged filesystem would otherwise stall the loop forever.
    """
    from chaoscypher_core.services.sources.orphan_files import cleanup_orphan_source_files

    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            retention_seconds = retention_days * 86400
            # Per-pass session scope: the sweep touches adapter.session inside
            # the worker thread, which inherits this scope via the context copy
            # asyncio.to_thread performs — so it never shares the singleton
            # _fallback_session with concurrent loops/handlers.
            async with adapter.session_scope():
                deleted_count = await asyncio.wait_for(
                    asyncio.to_thread(
                        cleanup_orphan_source_files,
                        staging_dir=staging_dir,
                        adapter=adapter,
                        database_name=database_name,
                        retention_seconds=retention_seconds,
                    ),
                    timeout=pass_timeout_seconds,
                )
            if deleted_count > 0:
                logger.info(
                    "periodic_orphan_files_cleanup_acted",
                    deleted_count=deleted_count,
                    retention_days=retention_days,
                )
            else:
                logger.debug(
                    "periodic_orphan_files_cleanup_idle",
                    retention_days=retention_days,
                )
        except asyncio.CancelledError:
            return
        except TimeoutError:
            logger.warning(
                "orphan_files_cleanup_loop_timeout",
                timeout_seconds=pass_timeout_seconds,
                staging_dir=str(staging_dir),
            )
        except Exception as exc:
            logger.exception(
                "orphan_files_cleanup_loop_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )


async def _orphan_task_cleanup_loop(
    *,
    adapter: Any,
    retention_days: int,
    interval_seconds: int,
) -> None:
    """Periodic cleanup of orphaned chunk extraction tasks.

    Runs on a fixed interval (default 24h, configured via
    ``SourceRecoverySettings.orphan_task_cleanup_interval_seconds``),
    deleting chunk tasks in status='orphaned' whose created_at is
    older than ``retention_days`` ago. Orphaned tasks are created by
    BE-7's cascade update when an ExtractionJob fails; without this
    cleanup they accumulate indefinitely.

    Cancellable — the shutdown sequence cancels the task and awaits
    it with suppression (matches _source_recovery_loop).

    Args:
        adapter: SqliteAdapter instance (shares session with other
            database operations).
        retention_days: Tasks older than this many days are deleted.
        interval_seconds: Sleep between passes.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            older_than_seconds = retention_days * 86400
            # Per-pass session scope (inherited into the worker thread via
            # asyncio.to_thread's context copy) keeps this DELETE off the
            # shared _fallback_session.
            async with adapter.session_scope():
                deleted_count = await asyncio.to_thread(
                    adapter.cleanup_orphaned_chunk_tasks,
                    older_than_seconds=older_than_seconds,
                )
            if deleted_count > 0:
                logger.info(
                    "periodic_orphan_task_cleanup_acted",
                    deleted_count=deleted_count,
                    retention_days=retention_days,
                )
            else:
                logger.debug(
                    "periodic_orphan_task_cleanup_idle",
                    retention_days=retention_days,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception(
                "orphan_task_cleanup_loop_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )


async def _health_monitor_loop(
    evaluator: Any,
    interval: float,
) -> None:
    """Periodic health evaluation loop.

    Runs ``evaluator.tick()`` every ``interval`` seconds until
    cancelled.  Per-tick errors are logged but do not kill the loop.

    Args:
        evaluator: A HealthPauseEvaluator instance.
        interval: Seconds between ticks.
    """
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        try:
            await evaluator.tick()
        except Exception:
            logger.exception("health_monitor_tick_failed")


async def _warmup_embedding_model() -> None:
    """Pre-load the embedding model in the background, gated on operator config.

    The SentenceTransformer model is ~600MB on disk and takes several seconds
    to load + initialize PyTorch. Eager warmup avoids a multi-minute stall on
    the first real embedding request.

    Two gates protect the "zero outbound on default install" contract — an
    operator running airgapped / on a metered network must not see HuggingFace
    traffic until they have explicitly opted in:

    1. **Primary gate — `settings.embedding.is_configured`.** Set by the
       setup wizard / CLI when the operator picks an embedding provider.
       Skips the warmup entirely when ``False``. No network call, no
       provider construction.

    2. **Defense in depth — local cache + `allow_model_download` opt-in.**
       Even when configured, if the provider is ``local`` and the
       SentenceTransformer cache (``<data_dir>/models/embeddings``) holds
       no downloaded model AND ``settings.embedding.allow_model_download``
       is ``False``, skip warmup. The model will still be downloaded
       lazily on the first real embedding request — this gate only
       prevents the *eager* startup download.

    Both skip paths log a structured event so operators can grep for
    ``warmup_skipped_`` and see exactly why eager warmup didn't run.
    """
    settings = get_settings()

    if not settings.embedding.is_configured:
        logger.info(
            "embedding_warmup_skipped_unconfigured",
            reason="embedding.is_configured is False — operator has not run setup wizard",
            provider=settings.embedding.provider,
            model=settings.embedding.model,
        )
        return

    if settings.embedding.provider == "local" and not settings.embedding.allow_model_download:
        cache_dir = Path(settings.paths.data_dir) / "models" / "embeddings"
        if not _embedding_cache_has_model(cache_dir):
            logger.info(
                "embedding_warmup_skipped_no_cache_no_opt_in",
                reason=(
                    "local embedding cache is empty and "
                    "embedding.allow_model_download is False — "
                    "model will load lazily on first real request"
                ),
                cache_dir=str(cache_dir),
                model=settings.embedding.model,
            )
            return

    try:
        from chaoscypher_core.repo_factories import get_embedding_service

        service = get_embedding_service()
        # Trigger model load via a throwaway embedding. Bounded by
        # llm_embedding_wait so a hung Ollama / corrupt local cache
        # doesn't pin a provider connection until shutdown.
        timeout_s = settings.timeouts.llm_embedding_wait
        await asyncio.wait_for(service.embed("warmup"), timeout=timeout_s)
        logger.info("embedding_model_warmed_up", model=service.model_name)
    except TimeoutError:
        logger.warning(
            "embedding_model_warmup_timeout",
            timeout_seconds=settings.timeouts.llm_embedding_wait,
            model=settings.embedding.model,
        )
    except Exception as e:
        # Non-fatal — model will load lazily on first real request
        logger.warning(
            "embedding_model_warmup_failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )


def _embedding_cache_has_model(cache_dir: Path) -> bool:
    """Return True if the SentenceTransformer cache directory holds a model.

    HuggingFace's ``snapshot_download`` (used by ``SentenceTransformer`` when
    ``cache_folder`` is set) creates per-repo subdirectories named
    ``models--<owner>--<name>`` (e.g. ``models--Qwen--Qwen3-Embedding-0.6B``).
    Detection is intentionally lax: we only need to know whether *some* model
    is materialized so the warmup gate doesn't trigger a fresh download. If
    the directory itself is missing or empty, treat the cache as cold.
    """
    if not cache_dir.exists() or not cache_dir.is_dir():
        return False
    try:
        return any(child.is_dir() for child in cache_dir.iterdir())
    except OSError:
        # Permission / FS error — conservatively treat as cold so we err on
        # the side of skipping the download.
        return False


# ============================================================================
# Setup Helpers
# ============================================================================


async def _setup_source_recovery(ctx: WorkerContext) -> asyncio.Task[None] | None:
    """Create the SourceRecovery instance, run an initial reconcile, and start the periodic loop.

    Args:
        ctx: Typed worker context with shared services.

    Returns:
        Background task running the periodic recovery loop, or ``None``
        if prerequisites (adapter, database name) are missing.
    """
    storage_adapter = ctx.get("storage_adapter")
    current_database = ctx.get("current_database")

    # Configure the centralized event bus so emit() calls persist events.
    if storage_adapter is not None:
        from chaoscypher_core.services.events import event_bus

        event_bus.configure(storage_adapter)

    if storage_adapter is None or not current_database:
        logger.warning(
            "source_recovery_disabled",
            reason="missing_adapter_or_database_in_ctx",
        )
        return None

    source_recovery = SourceRecovery(
        adapter=storage_adapter,
        queue_client=queue_client,
        stalled_threshold_seconds=ctx["settings"].source_recovery.stalled_threshold_seconds,
        max_recovery_attempts=ctx["settings"].source_recovery.max_recovery_attempts,
        recovery_warn_threshold=ctx["settings"].source_recovery.recovery_warn_threshold,
    )
    timeout_s = ctx["settings"].source_recovery.reconcile_timeout_seconds
    try:
        async with asyncio.timeout(timeout_s):
            initial_stats = await source_recovery.reconcile_database(database_name=current_database)
        logger.info(
            "startup_source_reconcile_complete",
            database=current_database,
            **initial_stats.to_dict(),
        )
    except TimeoutError as exc:
        logger.warning(
            "startup_source_reconcile_timeout",
            database_name=current_database,
            timeout_seconds=timeout_s,
        )
        msg = f"startup reconcile of {current_database} exceeded {timeout_s}s"
        raise RecoveryTimeoutError(msg) from exc
    except Exception as exc:
        logger.exception(
            "startup_source_reconcile_failed",
            database=current_database,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

    task = asyncio.create_task(
        _source_recovery_loop(
            recovery=source_recovery,
            adapter=storage_adapter,
            database_name=current_database,
            interval_seconds=(ctx["settings"].source_recovery.worker_scan_interval_seconds),
            reconcile_timeout_seconds=timeout_s,
        )
    )
    task.add_done_callback(log_task_exception)
    return task


def _setup_orphan_task_cleanup(ctx: WorkerContext) -> asyncio.Task[None] | None:
    """Start the periodic orphaned chunk task cleanup background task.

    Args:
        ctx: Typed worker context with shared services.

    Returns:
        Background task running the periodic cleanup loop, or ``None``
        if the storage adapter is not available.
    """
    storage_adapter = ctx.get("storage_adapter")

    if storage_adapter is None:
        logger.warning(
            "orphan_task_cleanup_disabled",
            reason="missing_adapter_in_ctx",
        )
        return None

    task = asyncio.create_task(
        _orphan_task_cleanup_loop(
            adapter=storage_adapter,
            retention_days=ctx["settings"].source_recovery.orphan_task_retention_days,
            interval_seconds=ctx["settings"].source_recovery.orphan_task_cleanup_interval_seconds,
        )
    )
    task.add_done_callback(log_task_exception)
    return task


def _setup_orphan_files_cleanup(ctx: WorkerContext) -> asyncio.Task[None] | None:
    """Start the periodic orphan source-file cleanup background task.

    Sweeps ``staging_dir/<source_id>/`` directories with no matching
    SourceRow.id (orphaned by a hard kill between file write and row
    commit in upload_source).

    Args:
        ctx: Typed worker context with shared services.

    Returns:
        Background task running the periodic cleanup loop, or ``None``
        if the storage adapter is not available.
    """
    storage_adapter = ctx.get("storage_adapter")

    if storage_adapter is None:
        logger.warning(
            "orphan_files_cleanup_disabled",
            reason="missing_adapter_in_ctx",
        )
        return None

    settings = ctx["settings"]
    staging_dir = settings.database_dir / "sources"

    return asyncio.create_task(
        _orphan_files_cleanup_loop(
            adapter=storage_adapter,
            staging_dir=staging_dir,
            database_name=settings.current_database,
            retention_days=settings.source_recovery.orphan_files_retention_days,
            interval_seconds=settings.source_recovery.orphan_files_cleanup_interval_seconds,
            pass_timeout_seconds=settings.source_recovery.orphan_files_cleanup_timeout_seconds,
        )
    )


def _setup_search_sweep(ctx: WorkerContext) -> asyncio.Task[None] | None:
    """Start the periodic search-index orphan-sweep background task.

    Deletes orphaned FTS5/vec rows and drains ``pending_search_index``
    on a fixed interval (default 300 s, configured via
    ``settings.intervals.search_sweep_seconds``).

    Args:
        ctx: Typed worker context with shared services.

    Returns:
        Background task running the sweep loop, or ``None`` if
        prerequisites (adapter, search repo) are missing.
    """
    storage_adapter = ctx.get("storage_adapter")
    search_repo = ctx.get("search_repository")

    if storage_adapter is None or search_repo is None:
        logger.warning(
            "search_sweep_disabled",
            reason="missing_adapter_or_search_repo_in_ctx",
        )
        return None

    from chaoscypher_neuron.search_sweep import _search_sweep_loop

    interval = ctx["settings"].intervals.search_sweep_seconds
    max_attempts = ctx["settings"].intervals.search_sweep_max_attempts
    task = asyncio.create_task(
        _search_sweep_loop(
            adapter=storage_adapter,
            search_repo=search_repo,
            interval_seconds=interval,
            max_attempts=max_attempts,
        )
    )
    task.add_done_callback(log_task_exception)
    logger.info(
        "search_sweep_started",
        interval_seconds=interval,
        max_attempts=max_attempts,
    )
    return task


def _setup_health_monitor(ctx: WorkerContext) -> asyncio.Task[None] | None:
    """Create and start the health monitor background task.

    Args:
        ctx: Typed worker context with shared services.

    Returns:
        Background task running the health evaluation loop, or ``None``
        if monitoring is disabled or prerequisites are missing.
    """
    storage_adapter = ctx.get("storage_adapter")
    hm = ctx["settings"].health_monitor

    if not hm.enabled:
        logger.info("health_monitor_disabled")
        return None

    if storage_adapter is None:
        return None

    from chaoscypher_core.services.events.health.pause_evaluator import HealthPauseEvaluator
    from chaoscypher_core.services.events.health.probes.disk_space import DiskSpaceProbe
    from chaoscypher_core.services.events.health.probes.queue import QueueProbe
    from chaoscypher_core.services.events.health.registry import HealthRegistry

    _health_registry = HealthRegistry()
    _health_registry.register(
        DiskSpaceProbe(
            path=str(ctx["settings"].paths.data_dir),
            warn_bytes=hm.disk_warn_bytes,
            error_bytes=hm.disk_error_bytes,
        )
    )
    # redis-py types ``Redis.ping`` as returning ``Awaitable[bool] | bool``
    # (sync/async polymorphism). Wrap it in an async closure so the
    # ``QueueProbe`` contract — ``Callable[[], Coroutine[Any, Any, Any]]`` —
    # is satisfied without leaking that union type.
    _client = queue_client.client
    _ping_fn: Callable[[], Coroutine[Any, Any, Any]] | None
    if _client is not None:

        async def _ping() -> Any:
            """Async wrapper around the Valkey client's ping for the QueueProbe contract."""
            return await _client.ping()

        _ping_fn = _ping
    else:
        _ping_fn = None
    _health_registry.register(QueueProbe(ping_fn=_ping_fn))

    _health_evaluator = HealthPauseEvaluator(
        registry=_health_registry,
        adapter=storage_adapter,
        trip_threshold=hm.trip_threshold,
        clear_threshold=hm.clear_threshold,
    )
    task = asyncio.create_task(
        _health_monitor_loop(
            evaluator=_health_evaluator,
            interval=hm.check_interval_seconds,
        )
    )
    task.add_done_callback(log_task_exception)
    logger.info(
        "health_monitor_started",
        interval_seconds=hm.check_interval_seconds,
        trip_threshold=hm.trip_threshold,
        clear_threshold=hm.clear_threshold,
    )
    return task


# ============================================================================
# Entry Point
# ============================================================================


async def run_worker() -> None:  # noqa: PLR0915
    """Main entry point — initialise everything and start QueueWorker."""
    logger.info("unified_worker_starting")

    # Load configs for both queues
    llm_config = load_worker_config("llm_worker")
    ops_config = load_worker_config("operations_worker")

    ctx: WorkerContext = {}

    # Shared setup (database, config, LLM, Valkey).
    # setup_shared() runs init_database which invokes the tier-aware
    # migration runner; tier-2 migrations set ready=False and we'll
    # sleep-loop below until the operator clicks Apply.
    await setup_shared(ctx)

    # Upgrade gate — if the DB is blocked on a NEEDS_CONFIRMATION
    # migration, sleep-loop until an operator (via Cortex /upgrade/apply
    # or CLI `chaoscypher db migrate apply`) flips ready=True. We hold
    # off on registering queue handlers so no work gets claimed while
    # the schema is mid-upgrade.
    from chaoscypher_core.database.engine import get_db_path
    from chaoscypher_core.database.migrations.state import get_upgrade_state

    _upgrade_db_path = get_db_path(ctx["current_database"])
    _upgrade_state = get_upgrade_state(_upgrade_db_path)
    if not _upgrade_state.ready:
        logger.warning(
            "worker_blocked_on_upgrade",
            blocked_on=_upgrade_state.blocked_on,
            message=_upgrade_state.message,
            last_backup=_upgrade_state.last_backup,
        )
        # Poll loop: the ready flag is flipped by a separate process
        # (operator via /upgrade/apply or `chaoscypher db migrate apply`), so
        # there is no in-process event to await on — asyncio.Event does not
        # apply. See ADR on cross-process upgrade gating.
        while not get_upgrade_state(_upgrade_db_path).ready:  # noqa: ASYNC110
            await asyncio.sleep(get_settings().intervals.upgrade_poll_seconds)
        logger.info("worker_resumed_after_upgrade")

    # Retry Valkey connection with exponential backoff (worker cannot function without it)
    if queue_client.client is None:
        await queue_client.connect_with_retry(ctx["settings"], required=True, delay_cap=30.0)

    # Register handlers for both queues
    await setup_llm_handlers(ctx)
    await setup_operations_handlers(ctx)

    # Validate handlers were registered for both queues
    llm_handlers = queue_client.handlers.get(QUEUE_LLM, {})
    ops_handlers = queue_client.handlers.get(QUEUE_OPERATIONS, {})
    if not llm_handlers:
        logger.error("no_llm_handlers_registered", detail="LLM queue will not process any tasks")
    if not ops_handlers:
        logger.error(
            "no_ops_handlers_registered", detail="Operations queue will not process any tasks"
        )

    # Start background settings listener
    settings_listener_task = asyncio.create_task(listen_for_settings_changes(ctx))
    settings_listener_task.add_done_callback(log_task_exception)
    ctx["settings_listener_task"] = settings_listener_task

    # Run extraction recovery
    await _run_startup_recovery(ctx)
    if queue_client.client is None:
        msg = "Queue client not connected after startup"
        raise RuntimeError(msg)

    # Detect Valkey AOF wipe from previous startup (Task 5.5).
    # valkey-startup.sh leaves a sentinel when it had to wipe the AOF
    # directory; surface it in logs before rehydration fires so ops can
    # correlate the wipe with the recovery event.
    _consume_wipe_sentinel(Path(ctx["settings"].paths.data_dir))

    # Re-enqueue tasks whose Valkey state is missing (Task 5.4).
    # Must run BEFORE QueueWorker starts polling so that rehydrated tasks
    # enter the queue before any worker tries to dequeue.
    #
    # Reuses the already-connected ``ctx["storage_adapter"].session``
    # rather than opening a new session. ``ctx["current_database"]`` is
    # the database *name* (e.g. ``"default"``), not a filesystem path —
    # passing it to ``get_db_session`` triggers
    # ``OperationalError: unable to open database file`` because the
    # engine factory resolves it relative to cwd. The adapter's session
    # is a ``SafeSession`` (what ``rehydrate_queue_from_db`` expects) and
    # is guaranteed connected by the time run_worker reaches this block.
    try:
        from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

        current_database = ctx.get("current_database", "")
        storage_adapter = ctx.get("storage_adapter")
        if not current_database:
            logger.warning("queue_rehydration_skipped", reason="no_current_database")
        elif storage_adapter is None or storage_adapter.session is None:
            logger.warning(
                "queue_rehydration_skipped",
                reason="storage_adapter_not_ready",
                database=current_database,
            )
        else:
            rehydrated_count = await rehydrate_queue_from_db(queue_client, storage_adapter.session)
            if rehydrated_count > 0:
                logger.warning(
                    "queue_rehydrated_from_db",
                    count=rehydrated_count,
                    database=current_database,
                )
    except Exception:
        logger.exception("queue_rehydration_failed")

    _recovery = ctx["settings"].queue_recovery
    worker = QueueWorker(
        client=queue_client.client,
        queues_config={
            "llm": {
                "concurrency": llm_config["max_concurrent"],
                "max_tries": llm_config["max_tries"],
                "timeout": llm_config["timeout"],
            },
            "operations": {
                "concurrency": ops_config["max_concurrent"],
                "max_tries": ops_config["max_tries"],
                "timeout": ops_config["timeout"],
            },
        },
        handlers=queue_client.handlers,
        poll_interval=ctx["settings"].timeouts.queue_poll_interval,
        health_report_interval=ctx["settings"].workers.health_report_interval,
        drain_timeout=ctx["settings"].timeouts.instance_drain_max_wait,
        semaphore_acquire_timeout=ctx["settings"].timeouts.queue_semaphore_acquire,
        poller_error_delay=ctx["settings"].backoff.queue_poller_error_delay,
        queue_client=queue_client,
        heartbeat_ttl_seconds=_recovery.heartbeat_ttl_seconds,
        heartbeat_refresh_interval_seconds=_recovery.heartbeat_refresh_interval_seconds,
        reconcile_interval_seconds=_recovery.worker_reconcile_interval_seconds,
    )
    logger.info(
        "queue_recovery_configured",
        heartbeat_ttl_seconds=_recovery.heartbeat_ttl_seconds,
        heartbeat_refresh_interval_seconds=_recovery.heartbeat_refresh_interval_seconds,
        worker_reconcile_interval_seconds=_recovery.worker_reconcile_interval_seconds,
    )

    logger.info(
        "unified_worker_ready",
        llm_concurrency=llm_config["max_concurrent"],
        ops_concurrency=ops_config["max_concurrent"],
        llm_handlers=list(queue_client.handlers.get(QUEUE_LLM, {}).keys()),
        ops_handlers=list(queue_client.handlers.get(QUEUE_OPERATIONS, {}).keys()),
    )

    # Source-level recovery: immediate startup pass + periodic background loop
    source_recovery_task = await _setup_source_recovery(ctx)

    # Orphaned chunk task cleanup: periodic background loop (default 24h)
    orphan_cleanup_task = _setup_orphan_task_cleanup(ctx)

    # Orphan source-file cleanup: periodic background loop (default 24h).
    # Sweeps staging_dir entries with no matching SourceRow.
    orphan_files_cleanup_task = _setup_orphan_files_cleanup(ctx)

    # Search-index sweep: orphan cleanup + pending_search_index drain (default 5 min)
    search_sweep_task = _setup_search_sweep(ctx)

    # Warm up the embedding model in the background (avoids 2+ minute delay on
    # first embedding request). The worker starts processing queues immediately
    # while the model loads in parallel.
    warmup_task = asyncio.create_task(_warmup_embedding_model())
    warmup_task.add_done_callback(log_task_exception)

    # Health monitor: auto-pause when disk space / queue health degrades
    health_monitor_task = _setup_health_monitor(ctx)

    try:
        await worker.run()
    finally:
        # Stop the trigger dispatcher's event loop and cancel its task.
        trigger_dispatcher = ctx.get("trigger_dispatcher")
        if trigger_dispatcher is not None:
            with contextlib.suppress(Exception):
                await trigger_dispatcher.stop()
        warmup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await warmup_task
        if source_recovery_task is not None:
            source_recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await source_recovery_task
        if orphan_cleanup_task is not None:
            orphan_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orphan_cleanup_task
        if orphan_files_cleanup_task is not None:
            orphan_files_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orphan_files_cleanup_task
        if search_sweep_task is not None:
            search_sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await search_sweep_task
        if health_monitor_task is not None:
            health_monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_monitor_task
        # Cleanup — timeout prevents hanging if pubsub is stuck in I/O
        settings_listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(
                settings_listener_task,
                timeout=ctx["settings"].timeouts.settings_listener_shutdown,
            )

        # Close SqliteAdapter (also closes the shared session used by
        # worker_session and graph_repository)
        storage_adapter = ctx.get("storage_adapter")
        if storage_adapter is not None and isinstance(storage_adapter, SqliteAdapter):
            with contextlib.suppress(Exception):
                storage_adapter.disconnect()
                logger.debug("storage_adapter_closed")

        await queue_client.disconnect()
        logger.info("unified_worker_shutdown_complete")


async def _run_worker_with_circuit_breaker() -> None:
    """Run ``run_worker()`` under a consecutive-failure circuit-breaker.

    A poison-pill crash inside ``run_worker()`` would otherwise exit the
    process immediately and leave recovery to the container's
    ``restart: unless-stopped`` policy — producing a CPU-burning tight
    restart loop. The circuit-breaker slows that loop with exponential
    backoff (defaults: 5s → 5min cap) and re-raises after
    ``run_worker_max_consecutive_failures`` consecutive failures so the
    container restart takes over under genuine breakage.

    A clean ``return`` from ``run_worker()`` exits the loop entirely
    (the unified worker is meant to run forever — a clean return is
    itself unusual and is the only "healthy" signal we have). The
    consecutive-failure counter is intentionally NOT reset on partial
    progress.
    """
    # Lazy import to avoid pulling neuron-config (and its core import
    # chain) at module-import time of worker.py.
    from chaoscypher_neuron.config import get_neuron_settings

    neuron_settings = get_neuron_settings()
    max_failures = neuron_settings.run_worker_max_consecutive_failures
    backoff_seconds = neuron_settings.run_worker_initial_backoff_seconds
    max_backoff = neuron_settings.run_worker_max_backoff_seconds

    consecutive_failures = 0
    while True:
        try:
            await run_worker()
            return
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures >= max_failures:
                logger.exception(
                    "worker_circuit_breaker_open",
                    consecutive_failures=consecutive_failures,
                    max_failures=max_failures,
                    exc_info=exc,
                )
                raise
            logger.warning(
                "worker_restart_after_failure",
                consecutive_failures=consecutive_failures,
                max_failures=max_failures,
                backoff_seconds=backoff_seconds,
                exc_info=exc,
            )
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, max_backoff)


def main() -> None:
    """Entry point for the unified worker (cc-neuron)."""
    use_json_logging = os.getenv("USE_JSON_LOGGING", "false").lower() == "true"
    log_level = os.getenv("LOG_LEVEL", "INFO")
    configure_logging(use_json=use_json_logging, log_level=log_level)

    logger.info("neuron_worker_starting_via_cli")
    asyncio.run(_run_worker_with_circuit_breaker())
