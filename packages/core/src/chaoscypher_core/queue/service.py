# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Task execution utilities for the queue worker.

Provides the handler dispatch function and error classification used by
:class:`~chaoscypher_core.queue.worker.QueueWorker`.

Example:
    Called internally by the worker's ``_process_task`` method::

        result = await _execute_handler(
            handler, task_id, queue, operation,
            data, metadata, result_ttl, client,
        )

"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
import structlog.contextvars

from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.queue.utils import iso_now as _iso_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from valkey.asyncio import Valkey

logger = structlog.get_logger(__name__)

# ============================================================================
# Worker Adapter Registration (per-task session scoping)
# ============================================================================

_worker_adapter: Any = None


def register_worker_adapter(adapter: Any) -> None:
    """Register the worker's storage adapter for per-task session scoping.

    The queue worker uses this to enter ``adapter.session_scope()`` around
    every handler dispatch, so concurrent handlers each get a fresh
    ``SafeSession`` instead of sharing the singleton. Eliminates the
    2026-05-20 silent-data-loss race (parallel imports losing extraction
    jobs + chunk tasks) by removing the shared-session class of bugs
    entirely.

    Only one adapter is registered per worker process; callers (e.g.
    ``chaoscypher_neuron.setup.shared.setup_shared``) overwrite any
    previous registration. Passing ``None`` disables scoping — useful in
    tests that drive ``_execute_handler`` without a real adapter.
    """
    global _worker_adapter
    _worker_adapter = adapter


@asynccontextmanager
async def _maybe_session_scope() -> AsyncIterator[None]:
    """Enter the registered adapter's session_scope, or yield unchanged.

    Yields immediately when no adapter is registered (test paths, the
    Cortex queue client, anything that talks to the queue without owning
    a storage adapter). When one IS registered, every handler runs
    inside a fresh per-task ``SafeSession``.
    """
    if _worker_adapter is None:
        yield
        return
    async with _worker_adapter.session_scope():
        yield


# ============================================================================
# Error Classification
# ============================================================================

# Permanent OSError subclasses that will NOT resolve on retry. These must be
# checked BEFORE ``TRANSIENT_ERROR_TYPES`` below, because that tuple contains
# bare ``OSError`` (for network-related OS errors) and every type here is an
# ``OSError`` subclass — without this pre-check a missing file or a bad mount
# would be retried through the full backoff schedule before terminal-failing.
# ``InterruptedError`` (EINTR) and ``BlockingIOError`` (EAGAIN) are deliberately
# excluded: those genuinely are retryable.
PERMANENT_OS_ERROR_TYPES = (
    FileNotFoundError,
    PermissionError,
    IsADirectoryError,
    NotADirectoryError,
)

# Transient errors that should be retried (connection issues, timeouts)
TRANSIENT_ERROR_TYPES = (
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    TimeoutError,
    OSError,  # Includes network-related OS errors
)

# Try to import httpx errors (used by many LLM clients)
try:
    import httpx

    TRANSIENT_ERROR_TYPES = (
        *TRANSIENT_ERROR_TYPES,
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.ReadTimeout,
    )  # type: ignore[assignment]  # Dynamic tuple extension
except ImportError:
    pass

# Try to import requests errors (used by some LLM clients)
try:
    import requests

    TRANSIENT_ERROR_TYPES = (*TRANSIENT_ERROR_TYPES, requests.ConnectionError, requests.Timeout)  # type: ignore[assignment]  # Dynamic tuple extension
except ImportError:
    pass


def classify_error(exc: Exception) -> str:  # noqa: PLR0911 — linear classification cascade, each return is a distinct outcome
    """Classify an error as transient or permanent.

    Transient errors are connection/timeout issues that may resolve on retry.
    Permanent errors are validation/auth issues that won't resolve on retry.

    LLM errors use their built-in is_retryable flag for classification:
    - Rate limit (not quota): transient (retry after cooldown)
    - Rate limit (quota exceeded): permanent (no point retrying)
    - Auth errors: permanent (need to fix API key)
    - Model errors: permanent (need to change model)
    - Service errors: transient (temporary outage)
    - Content filter: permanent (need different input)
    - Context length: permanent (need smaller input)

    Args:
        exc: The exception to classify

    Returns:
        "transient" or "permanent"

    """
    # Check for LLM errors first - use their is_retryable flag
    if isinstance(exc, LLMError):
        if exc.is_retryable:
            logger.debug(
                "llm_error_classified_transient",
                error_code=exc.code,
                provider=exc.provider,
                will_retry=True,
            )
            return "transient"
        logger.info(
            "llm_error_classified_permanent",
            error_code=exc.code,
            provider=exc.provider,
            suggested_action=exc.suggested_action,
            will_retry=False,
        )
        return "permanent"

    # Filesystem errors that will not resolve on retry (missing file, bad
    # permissions, wrong node type). Checked before the transient tuple because
    # these all subclass OSError, which is itself in TRANSIENT_ERROR_TYPES.
    if isinstance(exc, PERMANENT_OS_ERROR_TYPES):
        return "permanent"

    # Check for transient error types
    if isinstance(exc, TRANSIENT_ERROR_TYPES):
        return "transient"

    # Check error message for connection-related keywords
    error_msg = str(exc).lower()
    transient_keywords = [
        "connection",
        "connect",
        "timeout",
        "timed out",
        "network",
        "unreachable",
        "refused",
        "reset",
        "temporarily unavailable",
    ]

    # Permanent keywords that override transient detection
    permanent_keywords = [
        "api key",
        "invalid key",
        "authentication",
        "unauthorized",
        "quota exceeded",
        "billing",
        "rate limit exceeded",
    ]

    if any(keyword in error_msg for keyword in permanent_keywords):
        return "permanent"

    if any(keyword in error_msg for keyword in transient_keywords):
        return "transient"

    # Default to permanent (auth errors, validation errors, etc.)
    return "permanent"


def _json_default(value: Any) -> Any:
    """JSON serializer fallback for datetime objects."""
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


async def _execute_handler(  # noqa: PLR0912 — linear error-classification flow, each branch is a distinct outcome
    handler: Any,
    task_id: str,
    queue: str,
    operation: str,
    data: dict[str, Any],
    metadata: dict[str, Any],
    result_ttl: int,
    client: Valkey,
    failed_result_ttl: int | None = None,
) -> Any:
    """Execute the handler and update task status.

    Args:
        handler: Async callable to invoke.
        task_id: Unique task identifier.
        queue: Queue name.
        operation: Operation name within the queue.
        data: Task payload data.
        metadata: Task metadata dict.
        result_ttl: Seconds to keep the result in queue server.
        client: Async Valkey connection.
        failed_result_ttl: Dead-letter retention (seconds) applied to the
            ``queue:task:{id}`` hash on terminal-permanent failures so
            operators have a post-mortem window. ``None`` (the legacy
            default used by tests that don't drive a real queue client)
            skips the EXPIRE — the hash then persists until manual
            cleanup, matching pre-retention behavior.

    Returns:
        The handler's return value.

    Raises:
        Exception: Re-raised for transient errors so the worker can retry.

    """
    logger.debug("task_execution_started", task_id=task_id, queue=queue, operation=operation)

    # Bind correlation_id (and request_id for grep-friendliness) from the
    # task's metadata so every log line emitted during handler execution
    # carries the originating Cortex request's ID.  Both keys are unbound
    # in the finally block so that subsequent unrelated tasks on this worker
    # process do not inherit a stale value.
    _correlation_id: str | None = metadata.get("correlation_id") if metadata else None
    if _correlation_id:
        structlog.contextvars.bind_contextvars(
            correlation_id=_correlation_id,
            request_id=_correlation_id,
        )

    # Run the handler inside a fresh per-task session scope so concurrent
    # handlers never share session state. See ``_maybe_session_scope`` and
    # ``register_worker_adapter`` for the architectural rationale.
    #
    # The outer try/finally guarantees that correlation_id and request_id
    # are unbound from structlog contextvars after every code path (success,
    # cancelled, permanent failure, transient re-raise) so that subsequent
    # unrelated tasks never inherit a stale value.
    try:
        try:
            async with _maybe_session_scope():
                result = await handler(data, metadata=metadata, task_id=task_id)
        except asyncio.CancelledError:
            logger.info("task_cancelled", task_id=task_id, queue=queue, operation=operation)
            hset_result = client.hset(
                f"queue:task:{task_id}",
                mapping={
                    "status": "cancelled",
                    "completed_at": _iso_now(),
                },
            )
            if isinstance(hset_result, int):
                pass  # Sync result
            else:
                await hset_result  # Async result
            return {"status": "cancelled", "message": "Task was cancelled"}
        except Exception as exc:
            error_type = classify_error(exc)
            error_msg = str(exc)

            # Extract additional error details if available (LLM errors)
            error_details = {}
            if isinstance(exc, LLMError):
                error_details = {
                    "error_code": exc.code,
                    "provider": exc.provider,
                    "model": exc.model,
                    "suggested_action": exc.suggested_action,
                    "is_retryable": exc.is_retryable,
                }

            if error_type == "transient":
                logger.warning(
                    "task_failed_transient",
                    task_id=task_id,
                    queue=queue,
                    operation=operation,
                    error_type=error_type,
                    error_message=error_msg,
                    will_retry=True,
                    exc_info=False,
                )
            else:
                logger.exception(
                    "task_failed_permanent",
                    task_id=task_id,
                    queue=queue,
                    operation=operation,
                    error_type=error_type,
                    error_message=error_msg,
                    **error_details,
                )

            # Update task metadata with error details
            hset_result = client.hset(
                f"queue:task:{task_id}",
                mapping={
                    "status": "failed",
                    "error": error_msg,
                    "error_type": error_type,
                    "error_code": error_details.get("error_code", "UNKNOWN"),
                    "completed_at": _iso_now(),
                },
            )
            if isinstance(hset_result, int):
                pass  # Sync result
            else:
                await hset_result  # Async result

            # For permanent errors, return failure result (no retry)
            if error_type == "permanent":
                # Apply dead-letter retention TTL so the failed hash stays
                # around for operator post-mortems (default 14 days) instead
                # of either disappearing or persisting forever. Skipped when
                # no TTL is configured (legacy / test paths).
                if failed_result_ttl is not None:
                    expire_result = client.expire(f"queue:task:{task_id}", failed_result_ttl)
                    if isinstance(expire_result, bool):
                        pass  # Sync result
                    else:
                        await expire_result  # Async result
                return {
                    "status": "failed",
                    "error": error_msg,
                    "error_type": error_type,
                    **error_details,
                }

            # Transient error — re-raise so the worker can retry
            raise

        hset_result = client.hset(
            f"queue:task:{task_id}",
            mapping={"status": "completed", "completed_at": _iso_now()},
        )
        if isinstance(hset_result, int):
            pass  # Sync result
        else:
            await hset_result  # Async result

        setex_result = client.setex(
            f"queue:result:{task_id}", result_ttl, json.dumps(result, default=_json_default)
        )
        if isinstance(setex_result, bool):
            pass  # Sync result
        else:
            await setex_result  # Async result

        logger.debug("task_completed", task_id=task_id, queue=queue, operation=operation)
        return result

    finally:
        # Always unbound correlation context vars so subsequent tasks on this
        # worker process never see a stale correlation_id / request_id.
        if _correlation_id:
            structlog.contextvars.unbind_contextvars("correlation_id", "request_id")
