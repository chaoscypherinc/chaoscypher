# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Application queue client for task submission and monitoring.

Uses ``valkey.asyncio`` directly (no ARQ dependency). The custom metadata layer
in ``queue:task:{id}`` hashes is the single source of truth for task state.

Example:
    from chaoscypher_core.constants import QUEUE_LLM
    from chaoscypher_core.queue import queue_client

    # Connect to Valkey
    await queue_client.connect(settings)

    # Enqueue a task
    task_id = await queue_client.enqueue_task(
        queue=QUEUE_LLM,
        operation="chat_completion",
        data={"messages": [...]},
        priority=100,  # Higher = pops first (ZPOPMAX)
    )

    # Check task status
    task = await queue_client.get_task(task_id)

"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from sqlmodel import select
from valkey.asyncio import Valkey
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import NoScriptError

from chaoscypher_core.adapters.sqlite.models import ChunkExtractionTask
from chaoscypher_core.constants import (
    OP_CHAT_BACKGROUND,
    OPERATION_RETRY_ON_CRASH,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.exceptions import ExternalServiceError, QueueFullError
from chaoscypher_core.queue.handler_spec import (
    HandlerLike,
    HandlerSpec,
    normalize_handler,
    validate_handler_signature,
)
from chaoscypher_core.queue.monitor import QueueMonitor
from chaoscypher_core.queue.utils import decode_bytes as _decode_bytes
from chaoscypher_core.queue.utils import iso_now as _iso_now
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.session import SafeSession
    from chaoscypher_core.app_config import Settings

# structlog.contextvars is always importable (structlog is a dependency),
# but we guard with a try so a stripped/test environment never hard-fails.
try:
    from structlog.contextvars import get_contextvars as _get_contextvars
except ImportError:  # pragma: no cover

    def _get_contextvars() -> dict:  # type: ignore[misc]
        """Fallback when structlog.contextvars is unavailable."""
        return {}


logger = structlog.get_logger(__name__)

# Payload envelope versioning. Bump when the shape of the `data` dict
# stored in queue task hashes changes incompatibly (renamed/removed
# keys, type changes). A worker refuses to dispatch unknown versions
# and marks the task `failed` permanently — see `queue/worker.py` for
# the check. Add to SUPPORTED_PAYLOAD_VERSIONS only if the running
# worker is backwards-compatible with that older version.
CURRENT_PAYLOAD_VERSION = 1
SUPPORTED_PAYLOAD_VERSIONS: frozenset[int] = frozenset({1})

# ---------------------------------------------------------------------------
# Pending-ZSET score composition (queue FIFO tiebreaker restore, 2026-08-15;
# retry-decay fix + coherence writeup 2026-08-15 review round)
# ---------------------------------------------------------------------------
#
# The composed score is priority minus seq divided by
# _PENDING_SCORE_SEQ_SCALE (see ``compose_pending_score`` below).
#
# `seq` comes from a per-queue Valkey INCR/INCRBY counter
# (``queue:{queue}:seq``), so it is exact and monotonic across every
# process sharing that Valkey instance. It replaces the previous
# ``time.time() / 1e10`` tiebreaker, which relied on the OS wall clock
# advancing far enough between two enqueues to survive floating-point
# subtraction from ``priority`` -- it usually didn't.
#
# Bound (verified 2026-08-15 under ``uv run python``):
#   math.ulp(100.0) is 1.4210854715202004e-14.
# ``100.0 - x`` only resolves changes in ``x`` larger than that ULP, so
# the old scheme needed ``delta_time > ulp * 1e10 ~= 142.1us`` between two
# enqueues to avoid an identical score -- confirmed empirically: two
# ``time.time()`` calls 50us apart produced bit-identical scores, 200us
# apart did not. A tight synchronous loop (``enqueue_tasks_batch``) or two
# closely-timed ``enqueue()`` calls routinely land under 142us.
#
# ``seq / _PENDING_SCORE_SEQ_SCALE`` must stay (a) far enough above that
# same ULP for adjacent integers to be distinguishable, and (b) strictly
# under 1.0 so the fraction can never cross a priority-tier boundary.
# ``_PENDING_SCORE_SEQ_SCALE = 2**40`` (1_099_511_627_776) satisfies both
# with wide margin AT THE VALIDATED [0, 100] PRIORITY RANGE
# (``QueueTaskRequest``/``PrioritySettings``):
#   - adjacent-seq delta = 1 / 2**40 ~= 9.09e-13, ~64x the 1.42e-14 ULP
#     ceiling at priority=100 (dividing by a power of two is itself exact
#     in IEEE754, so that margin is the only rounding source that
#     matters). This is WHY the [0, 100] bound matters, not just a nice
#     round number: ULP doubles every time the score's magnitude crosses
#     a power-of-two boundary, so the 64x margin halves each octave --
#     it reaches parity (1x, i.e. an adjacent seq step and the ULP are
#     the same size) around priority=4096 and drops below 1x -- adjacent
#     seq values collide again -- at priority>=8192. Raising the
#     priority ceiling without revisiting this scale would silently
#     reintroduce the exact bug this fix closes.
#   - ``seq`` must stay under 2**40 for the counter's lifetime to avoid
#     crossing a tier. At a sustained 10,000 enqueues/second to one queue
#     (far above this system's realistic throughput), reaching 2**40
#     takes ~3.5 years of continuous enqueuing without a Valkey restart
#     or data reset.
#
# Reset semantics: a full Valkey reset (FLUSHALL/FLUSHDB, or a data-dir
# wipe) clears ``queue:{queue}:seq`` and ``queue:{queue}:pending``
# together (same keyspace), so a low post-reset seq can never collide
# with a stale high-seq score -- there is nothing left to collide with.
# The app-level ``reset_queue_stats()`` operation is the one path that
# clears ``queue:{queue}:pending`` WITHOUT touching ``queue:{queue}:seq``;
# that is safe in the other direction too -- the pending ZSET is emptied
# and the counter just keeps counting up, so there is no stale entry for
# a resumed counter to misorder against. The inverse direction -- an
# operator (or a targeted key eviction) deleting just ``:seq`` while
# ``:pending`` entries survive -- is a real but bounded exposure: the
# counter restarts near zero, so it hands out small seq values that
# initially score BELOW the surviving old entries (a transient LIFO
# inversion), self-healing once the counter counts back up past them.
# ``:seq`` deliberately carries no TTL so ordinary expiry can't trigger
# this; it would take an explicit ``DEL`` or an eviction policy configured
# to reclaim non-expiring keys under memory pressure.
#
# Coherence with the other pending-ZSET writers:
#   - ``QueueWorker._retry_task`` draws its OWN fresh seq from this SAME
#     per-queue counter at schedule time (via ``compose_pending_score``,
#     identically to a fresh enqueue) -- a retry rejoins its tier at the
#     CURRENT TAIL of the line, not a synthetic "due position". This
#     replaced an earlier design that reused the retired
#     ``time.time()``-based fraction (~0.18 at any 2026-era epoch): that
#     fraction is CONSTANT once written, while fresh enqueues' seq-based
#     fraction keeps growing, so the two would cross -- silently
#     flipping every outstanding retry to the FRONT of its tier, ahead
#     of all fresh same-tier work -- once the per-queue counter passed
#     ~1.965e11 (0.18 * 2**40). That is deep inside the 2**40
#     tier-crossing ceiling above, not a multi-year-away edge case, so
#     it was a real defect, not a documentable one. Actual dispatch
#     timing is unaffected either way: `_get_retry_after` still gates
#     whether a popped task is truly due; this score only decides queue
#     position among items that already are.
#   - ``requeue_task_atomic`` (reconciler recovery) is the one
#     deliberate exception: it passes the raw priority integer
#     (fraction == 0), the tier's ceiling, so a recovered/abandoned task
#     always jumps to the FRONT of its tier ahead of both fresh and
#     retried work. Unlike the retry case above, this does not decay:
#     0 is a fixed floor that no positive fraction (fresh or retried)
#     can ever cross, rather than a static value being overtaken by a
#     growing one.
_PENDING_SCORE_SEQ_SCALE = 2**40


def compose_pending_score(priority: float, seq: int) -> float:
    """Compose a ``queue:{queue}:pending`` ZSET score from priority and sequence.

    Shared by every writer that should take a fresh position in the
    queue's FIFO order: ``QueueClient.enqueue``, ``QueueClient.
    enqueue_tasks_batch``, and ``QueueWorker._retry_task``. See the
    module comment above ``_PENDING_SCORE_SEQ_SCALE`` for the full
    precision bound, the seq-wraparound/reset reasoning, and why
    ``requeue_task_atomic`` deliberately does NOT use this helper.

    Args:
        priority: Task priority tier (higher pops first under
            ZPOPMAX). Validated to [0, 100] by callers; not
            re-validated here -- see the module comment for why
            raising that ceiling would need this scale revisited.
        seq: Monotonic sequence number from a per-queue Valkey
            INCR/INCRBY counter. Smaller values sort higher (pop
            sooner), giving FIFO order within a tier.

    Returns:
        The ZSET score: ``priority - seq / _PENDING_SCORE_SEQ_SCALE``.
    """
    return float(priority) - seq / _PENDING_SCORE_SEQ_SCALE


@contextmanager
def _adapter_db_session(database_name: str) -> Generator[SafeSession]:
    """Yield a session bound to a per-database SqliteAdapter under a transaction.

    Used by the queue client's durable cancellation persistence path so we
    do not reintroduce the deleted ``get_db_session`` helper. Acquires a
    fresh adapter per call, runs the body inside ``adapter.transaction()``,
    and disconnects the adapter on exit so the SQLite file handle is
    released (Windows cleanliness).
    """
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter

    adapter = get_sqlite_adapter(database_name)
    try:
        with adapter.transaction():
            session = adapter.session
            assert session is not None
            yield session
    finally:
        adapter.disconnect()


# Lua script for atomic SREM + DEL completion. Loaded once at module
# import; each QueueClient caches its SHA after the first evalsha call.
_ATOMIC_COMPLETE_SCRIPT = (Path(__file__).parent / "scripts" / "atomic_complete.lua").read_text(
    encoding="utf-8"
)

_GUARDED_STATUS_WRITE_SCRIPT = (
    Path(__file__).parent / "scripts" / "guarded_status_write.lua"
).read_text(encoding="utf-8")

_GUARDED_DELETE_SCRIPT = (Path(__file__).parent / "scripts" / "guarded_delete.lua").read_text(
    encoding="utf-8"
)

_REQUEUE_ATOMIC_SCRIPT = (Path(__file__).parent / "scripts" / "requeue_atomic.lua").read_text(
    encoding="utf-8"
)

# Plain string compare-and-swap (approval broker et al.). Small enough to
# inline; ``streaming/chat/approval_broker.py`` duplicates it for raw-client
# callers — keep the two in sync.
_STRING_CAS_SCRIPT = (
    "local v = redis.call('GET', KEYS[1]) "
    "if v == ARGV[1] then redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL') return 1 end "
    "return 0"
)

# Return-contract sentinels shared by the guarded Lua scripts. Any other
# return value is the task's current status (the caller lost the race).
GUARDED_OK = "__ok__"
GUARDED_MISSING = "__missing__"


class TaskHandler(Protocol):
    """Protocol for task handler functions."""

    async def __call__(
        self, data: dict[str, Any], *, metadata: dict[str, Any], task_id: str
    ) -> Any:
        """Execute task with data, metadata, and task_id."""
        ...


class QueueUnavailableError(ExternalServiceError):
    """Raised when the queue backend (Valkey) is unreachable."""

    def __init__(self, reason: str, details: dict | None = None):
        """Initialize the instance.

        Args:
            reason: Description of why the queue is unavailable.
            details: Additional context about the failure.

        """
        super().__init__(service_name="Valkey", reason=reason, details=details)


class QueueClient:
    """Queue client facade (delegates to QueueMonitor for stats).

    Responsibilities:
    - Task submission (enqueue) via sorted sets
    - Task metadata CRUD
    - Cancellation (queued and running)
    - Handler registration
    """

    def __init__(self) -> None:
        """Initialize queue client."""
        self.client: Valkey | None = None
        self._handlers: dict[str, dict[str, TaskHandler]] = {}
        self._retry_policy: dict[str, dict[str, bool]] = {}
        self._transient_retry_policy: dict[str, dict[str, bool]] = {}
        self._queues: set[str] = set()
        self._enabled: bool = True
        self._connected: bool = False
        self.monitor: QueueMonitor | None = None
        self._operations_result_ttl: int = 7200
        self._llm_result_ttl: int = 3600
        # Dead-letter retention default mirrors TimeoutSettings.failed_result_ttl
        # (14 days). Overridden from settings on connect(). Kept as a non-zero
        # default so terminal-failed tasks always get a finite expiry even
        # before settings are wired (pre-injection paths in tests/CLI).
        self._failed_result_ttl: int = 14 * 86_400
        self._max_pending_queue_depth: int = 10000
        self._atomic_complete_sha: str | None = None
        self._guarded_status_write_sha: str | None = None
        self._guarded_delete_sha: str | None = None
        self._requeue_atomic_sha: str | None = None
        self._string_cas_sha: str | None = None
        # TTL for the Valkey cancel flag.  Set to llm_worker_default + 300 so
        # the fast-path check outlives the longest possible handler lifetime.
        # Defaults to 300 (legacy value) until overridden by connect().
        self._cancel_ttl: int = 300
        # Reconciler pass-lock TTL; mirrors TimeoutSettings.reconcile_lock_ttl
        # and is overridden from settings on connect().
        self._reconcile_lock_ttl: int = 120

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self, settings: Settings) -> bool:
        """Initialise a queue server connection if queueing is enabled."""
        self.client = None
        self._connected = False
        # Cached script SHAs are server-side state: a fresh connection may
        # point at a server whose script cache no longer holds them.
        self._atomic_complete_sha = None
        self._guarded_status_write_sha = None
        self._guarded_delete_sha = None
        self._requeue_atomic_sha = None
        self._string_cas_sha = None

        if not settings.llm.enable_llm_queueing:
            self._enabled = False
            logger.info("queue_client_disabled", reason="disabled_via_settings")
            return False

        self._enabled = True

        host = settings.queue.queue_host
        port = settings.queue.queue_port
        db = settings.queue.queue_database
        password = (
            settings.queue.queue_password.get_secret_value()
            if settings.queue.queue_password
            else None
        )
        ssl = settings.queue.queue_ssl

        try:
            self.client = Valkey(
                host=host,
                port=port,
                db=db,
                password=password,
                ssl=ssl,
                decode_responses=False,  # We handle decoding ourselves
            )
            # Verify connectivity
            await self.client.ping()
        except (OSError, ValkeyConnectionError) as exc:
            logger.warning(
                "queue_unavailable",
                host=host,
                port=port,
                database=db,
                error_type=type(exc).__name__,
                error_message=str(exc),
                action="continuing_without_queue_support",
            )
            self.client = None
            return False

        logger.info(
            "queue_connected",
            host=host,
            port=port,
            database=db,
        )
        self._connected = True
        self.monitor = QueueMonitor(self.client, self._queues)

        # Store TTL settings from config
        if hasattr(settings, "timeouts"):
            self._operations_result_ttl = settings.timeouts.operations_result_ttl
            self._llm_result_ttl = settings.timeouts.llm_result_ttl
            self._failed_result_ttl = settings.timeouts.failed_result_ttl
            # Extend cancel-flag TTL to cover the worst-case handler lifetime
            # so a long-running LLM call (up to llm_worker_default seconds) can
            # still check the flag at its last poll without the key having expired.
            self._cancel_ttl = settings.timeouts.llm_worker_default + 300
            self._reconcile_lock_ttl = settings.timeouts.reconcile_lock_ttl

        # Store queue depth limit from config
        if hasattr(settings, "queue"):
            self._max_pending_queue_depth = settings.queue.max_pending_queue_depth

        return True

    async def connect_with_retry(
        self,
        settings: Settings,
        *,
        required: bool = False,
        delay_cap: float = 30.0,
    ) -> bool:
        """Connect to Valkey with exponential-backoff retry.

        Wraps :meth:`connect` with the cross-process Valkey-startup-race
        retry loop. Both Cortex and Neuron use this; they differ only in
        whether queue access is required to function.

        Args:
            settings: Application settings.
            required: If True, raises ``RuntimeError`` when all retries are
                exhausted. If False, logs a warning and returns False
                (graceful degradation — queueing is best-effort).
            delay_cap: Maximum backoff delay in seconds. Cortex caps at
                10s; Neuron at 30s.

        Returns:
            True if connected. False if queueing is disabled in settings
            or (when ``required=False``) the queue is unreachable.

        Raises:
            RuntimeError: When ``required=True`` and all retries fail.
        """
        max_retries = settings.queue.connection_max_retries
        retry_delay = settings.queue.connection_retry_delay
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                connected = await self.connect(settings)
                if connected:
                    return True
                if not settings.llm.enable_llm_queueing:
                    return False
            except Exception as e:
                last_error = e

            if attempt < max_retries:
                logger.info(
                    "queue_connection_retry",
                    attempt=attempt,
                    max_retries=max_retries,
                    retry_in_seconds=retry_delay,
                    error=str(last_error) if last_error else None,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, delay_cap)

        if required:
            msg = (
                f"Queue server connection failed after {max_retries} attempts. "
                "Ensure Valkey is running and reachable."
            )
            raise RuntimeError(msg)

        logger.warning(
            "queue_client_connection_failed",
            action="continuing_without_queue_support",
            attempts=max_retries,
        )
        return False

    async def disconnect(self) -> None:
        """Disconnect from queue server."""
        if self.client:
            await self.client.aclose()
            self.client = None
        self._connected = False

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------
    def register_handlers(self, queue: str, handlers: dict[str, HandlerLike]) -> None:
        """Register task handlers for a specific queue.

        Accepts either bare async callables or ``HandlerSpec`` instances.
        Bare callables are auto-wrapped with ``retry_on_crash=False``. Call
        sites that want retry-on-crash semantics pass ``HandlerSpec``
        explicitly.

        Each handler's signature is validated against the dispatcher's
        calling convention (``data``, ``metadata``, ``task_id``) — see
        ``validate_handler_signature``. Any handler that does not match
        causes the entire batch to be rejected with ``TypeError``;
        registry state is unchanged on failure (all-or-nothing).

        Args:
            queue: Queue name ("llm", "operations").
            handlers: Mapping of operation name to callable or HandlerSpec.

        Raises:
            TypeError: If any handler in ``handlers`` does not satisfy
                the ``TaskHandler`` protocol contract.
        """
        # Validate the whole batch BEFORE mutating registry state so a bad
        # handler never half-registers a sibling.
        normalized: list[tuple[str, HandlerSpec, TaskHandler]] = []
        for op, h in handlers.items():
            spec = normalize_handler(h)
            handler_fn = spec.handler
            if handler_fn is None:
                msg = (
                    f"Cannot register unbound HandlerSpec (handler=None) for "
                    f"{queue}:{op} — unbound specs carry recovery flags only"
                )
                raise TypeError(msg)
            validate_handler_signature(handler_fn, queue=queue, operation=op)
            canonical_retry = OPERATION_RETRY_ON_CRASH.get(op)
            if canonical_retry is not None and canonical_retry != spec.retry_on_crash:
                msg = (
                    f"Handler for {queue}:{op} registers retry_on_crash="
                    f"{spec.retry_on_crash!r}, which contradicts "
                    f"chaoscypher_core.constants.OPERATION_RETRY_ON_CRASH"
                    f"[{op!r}] = {canonical_retry!r}. Fix the HandlerSpec at "
                    f"the registration site, or update the canonical table "
                    f"if the operation's idempotency semantics genuinely "
                    f"changed — they must agree."
                )
                raise TypeError(msg)
            normalized.append((op, spec, handler_fn))

        self._handlers.setdefault(queue, {})
        self._retry_policy.setdefault(queue, {})
        self._transient_retry_policy.setdefault(queue, {})
        for op, spec, handler_fn in normalized:
            self._handlers[queue][op] = handler_fn
            self._retry_policy[queue][op] = spec.retry_on_crash
            self._transient_retry_policy[queue][op] = spec.retry_on_transient
        self._queues.add(queue)

    def get_handler(self, queue: str, operation: str) -> TaskHandler | None:
        """Look up a registered handler callable.

        Args:
            queue: Queue name.
            operation: Operation name.

        Returns:
            The registered handler, or None if not found.
        """
        return self._handlers.get(queue, {}).get(operation)

    def get_retry_policy(self, queue: str, operation: str) -> bool:
        """Look up retry_on_crash for an operation.

        Prefers the value recorded by an in-process ``register_handlers()``
        call. Falls back to the process-independent
        ``chaoscypher_core.constants.OPERATION_RETRY_ON_CRASH`` table for
        any operation this process never registered a handler for — the
        case for Cortex, which shares the same Valkey-backed task metadata
        via this client but never calls ``register_handlers`` (that's a
        Neuron-only call; see ``chaoscypher_neuron.setup``). Without this
        fallback, Cortex's ``POST /queue/reconcile`` always saw an empty
        in-process registry and terminally failed every abandoned task
        regardless of its handler's actual policy.

        The queue reconciler consults this to decide whether an abandoned
        task should be requeued or marked failed.

        Args:
            queue: Queue name.
            operation: Operation name.

        Returns:
            True if the operation is registered/declared retry-on-crash;
            False for operations unknown to both the in-process registry
            and the canonical table — the safe default.
        """
        registered = self._retry_policy.get(queue, {})
        if operation in registered:
            return registered[operation]
        return OPERATION_RETRY_ON_CRASH.get(operation, False)

    def get_transient_retry_policy(self, queue: str, operation: str) -> bool:
        """Look up retry_on_transient for a registered handler.

        Returns True for unknown (queue, operation) pairs so bare worker
        setups and legacy tests keep the historical queue-level retry
        behavior unless a handler explicitly opts out.

        Args:
            queue: Queue name.
            operation: Operation name.

        Returns:
            True if the queue worker should retry transient failures for
            this handler; False if the handler owns that retry budget.
        """
        return getattr(self, "_transient_retry_policy", {}).get(queue, {}).get(operation, True)

    # ------------------------------------------------------------------
    # Heartbeat primitives (queue self-healing)
    # ------------------------------------------------------------------

    @staticmethod
    def _heartbeat_key(task_id: str) -> str:
        """Well-known heartbeat key for a task."""
        return f"queue:task:{task_id}:heartbeat"

    async def set_heartbeat(self, task_id: str, ttl_seconds: int) -> None:
        """Create the heartbeat key at task claim time.

        Must be called BEFORE adding the task_id to queue:{queue}:running
        so a reconciler can never observe a just-claimed task as dead.

        Args:
            task_id: Task identifier.
            ttl_seconds: Initial TTL. The refresh loop extends this
                periodically during handler execution.
        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        await self.client.set(self._heartbeat_key(task_id), "1", ex=ttl_seconds)

    async def refresh_heartbeat(self, task_id: str, ttl_seconds: int) -> None:
        """Reset the heartbeat TTL during handler execution.

        Called periodically by the worker's heartbeat co-task.

        Args:
            task_id: Task identifier.
            ttl_seconds: New TTL (replaces the existing one).
        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        await self.client.expire(self._heartbeat_key(task_id), ttl_seconds)

    async def delete_heartbeat(self, task_id: str) -> None:
        """Remove the heartbeat key at task completion.

        Called atomically alongside SREM via the atomic_complete Lua script
        (see ``complete_task_atomic``). This bare method is provided for
        tests and for the reconciler's orphan cleanup path.
        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        await self.client.delete(self._heartbeat_key(task_id))

    async def heartbeat_exists(self, task_id: str) -> bool:
        """Check whether a heartbeat key is currently live."""
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        result = await self.client.exists(self._heartbeat_key(task_id))
        return bool(result)

    async def complete_task_atomic(self, queue: str, task_id: str) -> None:
        """Atomically SREM from running set AND DEL heartbeat key.

        Replaces the non-atomic sequence used by the worker's `finally`
        block. Lazily loads the Lua script on first call and caches the
        SHA for subsequent evalsha calls.

        Args:
            queue: Queue name.
            task_id: Task identifier.
        """
        await self._eval_cached_script(
            "_atomic_complete_sha",
            _ATOMIC_COMPLETE_SCRIPT,
            2,  # numkeys
            f"queue:{queue}:running",
            self._heartbeat_key(task_id),
            task_id,
        )

    # ------------------------------------------------------------------
    # Guarded-write / atomic-move primitives (CAS family, 2026-07-23)
    # ------------------------------------------------------------------

    async def _eval_cached_script(self, sha_attr: str, script: str, *args: Any) -> Any:
        """EVALSHA a lazily-cached Lua script, reloading once on NOSCRIPT.

        A Valkey server restart clears its script cache while this process's
        cached SHAs live on, so every guarded write would fail with NOSCRIPT
        until the worker itself restarted. Raw ``evalsha`` has no auto-reload
        (unlike the ``register_script`` wrapper) — recover here by re-running
        ``SCRIPT LOAD`` and retrying the call once.
        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        sha = getattr(self, sha_attr)
        if sha is None:
            sha = await self.client.script_load(script)
            setattr(self, sha_attr, sha)
        try:
            return await self.client.evalsha(sha, *args)
        except NoScriptError:
            logger.warning("queue_script_cache_lost_reloading", script=sha_attr)
            sha = await self.client.script_load(script)
            setattr(self, sha_attr, sha)
            return await self.client.evalsha(sha, *args)

    @staticmethod
    def _decode_script_result(raw: Any) -> str:
        """Decode a Lua string return to str (evalsha yields bytes)."""
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def guarded_status_write(
        self,
        task_id: str,
        *,
        new_status: str,
        allowed_from: tuple[str, ...],
        extra_fields: dict[str, str] | None = None,
        remove_from: str | None = None,
        removal: str = "none",
        expire_seconds: int | None = None,
    ) -> str:
        """Atomically write a task status only while it is still transitionable.

        The single-round-trip replacement for every read-then-write status
        transition (cancel paths, retry claim, reconciler terminal-fail).
        Optionally removes the task from a pending zset / running set and
        applies a TTL in the same atomic step.

        Args:
            task_id: Task identifier.
            new_status: Status to write.
            allowed_from: Current statuses the transition is valid from —
                any other observed status refuses the whole write.
            extra_fields: Additional hash fields written with the status.
            remove_from: Full set/zset key to remove ``task_id`` from.
            removal: ``"srem"`` | ``"zrem"`` | ``"none"``.
            expire_seconds: Optional TTL applied to the task hash (dead-letter
                retention on terminal failures).

        Returns:
            ``GUARDED_OK`` on success, ``GUARDED_MISSING`` when the hash does
            not exist, otherwise the task's current status (caller lost).
        """
        fields = {"status": new_status, **(extra_fields or {})}
        field_args: list[str] = []
        for key, value in fields.items():
            field_args.extend((key, value))
        raw = await self._eval_cached_script(
            "_guarded_status_write_sha",
            _GUARDED_STATUS_WRITE_SCRIPT,
            2,  # numkeys
            f"queue:task:{task_id}",
            remove_from or "",
            task_id,
            ",".join(allowed_from),
            removal,
            str(expire_seconds) if expire_seconds is not None else "",
            *field_args,
        )
        return self._decode_script_result(raw)

    async def guarded_delete(
        self,
        task_id: str,
        *,
        allowed_from: tuple[str, ...],
        remove_from: str | None = None,
        removal: str = "none",
    ) -> str:
        """Atomically delete a task hash only while its status allows it.

        ``cancel_by_metadata``'s queued-task cleanup: a task that raced to
        running keeps its live hash (the caller falls back to a guarded
        cancel instead of destroying an executing task's record).

        Returns:
            ``GUARDED_OK`` on success, ``GUARDED_MISSING`` when the hash does
            not exist, otherwise the task's current status (caller lost).
        """
        raw = await self._eval_cached_script(
            "_guarded_delete_sha",
            _GUARDED_DELETE_SCRIPT,
            2,  # numkeys
            f"queue:task:{task_id}",
            remove_from or "",
            task_id,
            ",".join(allowed_from),
            removal,
        )
        return self._decode_script_result(raw)

    async def requeue_task_atomic(self, queue: str, task_id: str, priority: float) -> str:
        """Atomically requeue an abandoned task (reset + pending + un-run).

        One Lua move for the reconciler's requeue branch: HSET queued-state,
        PERSIST (clear dead-letter TTL), ZADD pending, SREM running, HINCRBY
        attempts. Refuses when the task finished while being classified.

        Returns:
            ``GUARDED_OK`` on success, ``GUARDED_MISSING`` when the hash does
            not exist, otherwise the terminal status that blocked the move.
        """
        raw = await self._eval_cached_script(
            "_requeue_atomic_sha",
            _REQUEUE_ATOMIC_SCRIPT,
            3,  # numkeys
            f"queue:task:{task_id}",
            f"queue:{queue}:pending",
            f"queue:{queue}:running",
            task_id,
            str(priority),
        )
        return self._decode_script_result(raw)

    async def compare_and_swap_string(self, key: str, *, expected: str, new: str) -> bool:
        """Swap a plain string key's value only if it still equals ``expected``.

        Keeps the key's TTL (``SET ... KEEPTTL``). Returns True when the swap
        happened, False when another writer got there first.
        """
        raw = await self._eval_cached_script(
            "_string_cas_sha",
            _STRING_CAS_SCRIPT,
            1,  # numkeys
            key,
            expected,
            new,
        )
        return bool(int(raw or 0))

    @property
    def failed_result_ttl(self) -> int:
        """Dead-letter retention (seconds) applied to terminal-failed task hashes.

        Sourced from ``TimeoutSettings.failed_result_ttl`` on
        :meth:`connect`. Defaults to 14 days pre-connect so test
        harnesses and CLI paths still get a finite expiry.
        """
        return self._failed_result_ttl

    async def mark_task_failed_terminal(self, task_id: str, fields: dict[str, str]) -> None:
        """HSET terminal-failed fields AND apply dead-letter retention TTL.

        Centralises the pairing of ``status=failed`` writes with the
        long-lived EXPIRE on ``queue:task:{task_id}`` so the operator can
        investigate post-mortems within ``TimeoutSettings.failed_result_ttl``
        (default 14 days) without the hash auto-disappearing.

        Only call from terminal-fail sites — handler permanent failures, the
        no-handler bail-out, timeout exhaustion, and reconciler-abandoned
        tasks that are not eligible for retry. Transient failures that
        will be re-queued must NOT call this helper; their hashes need to
        stay un-expiring so the retry can reset ``status=queued``.

        Args:
            task_id: Task identifier (hash key suffix).
            fields: Hash fields to set (must include ``status="failed"`` and
                typically ``error``, ``error_type``, ``completed_at``).
        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        task_key = f"queue:task:{task_id}"
        await self.client.hset(task_key, mapping=fields)
        await self.client.expire(task_key, self._failed_result_ttl)

    # ------------------------------------------------------------------
    # Backpressure
    # ------------------------------------------------------------------
    async def _check_queue_depth(self, queue: str) -> None:
        """Raise QueueFullError if the pending set exceeds the configured limit.

        ADVISORY backpressure by decision (2026-07-23): the check is a
        read-then-compare, so concurrent enqueuers can each pass the same
        ZCARD snapshot and collectively overshoot the cap. That is
        accepted — the cap exists to shed load loudly under sustained
        pressure, not as a hard safety invariant; a hard cap would need
        an atomic check-and-push (Lua) and is deliberately not built.

        Args:
            queue: Queue name whose pending sorted set is checked.

        Raises:
            QueueFullError: When ``ZCARD queue:{queue}:pending`` reaches
                ``max_pending_queue_depth``.

        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")

        depth = await self.client.zcard(f"queue:{queue}:pending")
        if depth >= self._max_pending_queue_depth:
            logger.warning(
                "queue_depth_limit_reached",
                queue=queue,
                current_depth=depth,
                max_depth=self._max_pending_queue_depth,
            )
            raise QueueFullError(
                queue=queue,
                current_depth=depth,
                max_depth=self._max_pending_queue_depth,
            )

    # ------------------------------------------------------------------
    # Task helpers
    # ------------------------------------------------------------------
    async def enqueue(
        self,
        queue: str,
        operation: str,
        data: dict[str, Any],
        *,
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
        result_ttl: int | None = None,
    ) -> str:
        """Enqueue a task for background processing.

        This is the canonical name used by SourceRecovery and other
        core-layer callers. ``enqueue_task`` is an alias kept for
        backwards compatibility with existing cortex call sites.

        Raises:
            QueueUnavailableError: If the queue server is not connected.
            QueueFullError: If the pending queue has reached its depth limit.

        """
        self._require_connection()
        await self._check_queue_depth(queue)

        # Default result TTL based on queue type
        if result_ttl is None:
            result_ttl = (
                self._operations_result_ttl if queue == QUEUE_OPERATIONS else self._llm_result_ttl
            )

        task_id = generate_id()
        created_at = _iso_now()
        metadata = dict(metadata or {})

        # Propagate the Cortex request_id across the queue boundary so that
        # Neuron worker logs for this task can be correlated back to the
        # originating HTTP request.  Only injected when the caller has not
        # already supplied an explicit correlation_id (caller override wins).
        if "correlation_id" not in metadata:
            ctx = _get_contextvars()
            if "request_id" in ctx:
                metadata["correlation_id"] = ctx["request_id"]

        # Use pipeline for atomic metadata operations
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        pipeline = self.client.pipeline()

        # Store task metadata
        pipeline.hset(
            f"queue:task:{task_id}",
            mapping={
                "task_id": task_id,
                "queue": queue,
                "operation": operation,
                "status": "queued",
                "priority": str(priority),
                "created_at": created_at,
                "data": json.dumps(data),
                "metadata": json.dumps(metadata),
                "result_ttl": str(result_ttl),
                "attempts": "0",
                "payload_version": str(CURRENT_PAYLOAD_VERSION),
            },
        )

        # Add to pending queue (sole job queue — worker pops from here).
        # One extra round trip draws an exact, monotonic per-queue seq
        # before the pipeline so the score can be computed client-side;
        # see _PENDING_SCORE_SEQ_SCALE for why this replaced a
        # time.time()-derived fraction (it collided under ~142us).
        seq = await self.client.incr(f"queue:{queue}:seq")
        score = compose_pending_score(priority, seq)
        pipeline.zadd(f"queue:{queue}:pending", {task_id: score})

        # Add to recent lists
        pipeline.lpush("queue:recent", task_id)
        pipeline.ltrim("queue:recent", 0, 999)
        pipeline.lpush(f"queue:{queue}:recent", task_id)
        pipeline.ltrim(f"queue:{queue}:recent", 0, 999)

        # Execute all operations atomically
        await pipeline.execute()

        logger.debug(
            "task_queued",
            task_id=task_id,
            queue=queue,
            operation=operation,
            priority=priority,
        )
        return task_id

    # Alias kept for existing cortex call sites (queue_helpers, etc.)
    enqueue_task = enqueue

    async def enqueue_tasks_batch(
        self,
        queue: str,
        tasks: list[dict[str, Any]],
        *,
        result_ttl: int | None = None,
    ) -> list[str]:
        """Enqueue multiple tasks in a single pipeline.

        Each task dict must contain: operation, data, priority, metadata.
        Uses a single pipeline for all hset + zadd + lpush operations,
        significantly reducing round-trips compared to per-task enqueue.

        Args:
            queue: Queue name ("operations" or "llm").
            tasks: List of task specs, each with keys:
                - operation (str): Operation name.
                - data (dict): Task payload.
                - priority (int): Priority (0-100).
                - metadata (dict): Task metadata.
            result_ttl: Result TTL in seconds (None = use queue default).

        Returns:
            List of generated task IDs in the same order as input tasks.

        Raises:
            QueueUnavailableError: If the queue server is not connected.
            QueueFullError: If adding the batch would exceed the pending
                queue depth limit.

        """
        self._require_connection()

        if not tasks:
            return []

        # Check whether the batch would push the queue over its depth limit.
        # We check against (current_depth + batch_size) so that a single
        # large batch cannot silently blow past the cap.
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        current_depth = await self.client.zcard(f"queue:{queue}:pending")
        if current_depth + len(tasks) > self._max_pending_queue_depth:
            logger.warning(
                "queue_depth_limit_reached",
                queue=queue,
                current_depth=current_depth,
                batch_size=len(tasks),
                max_depth=self._max_pending_queue_depth,
            )
            raise QueueFullError(
                queue=queue,
                current_depth=current_depth,
                max_depth=self._max_pending_queue_depth,
            )

        if result_ttl is None:
            result_ttl = (
                self._operations_result_ttl if queue == QUEUE_OPERATIONS else self._llm_result_ttl
            )

        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")

        pipeline = self.client.pipeline()
        task_ids: list[str] = []
        created_at = _iso_now()

        # Read contextvars once for the whole batch (same HTTP request context).
        _ctx = _get_contextvars()
        _ctx_request_id: str | None = _ctx.get("request_id")

        # Reserve a contiguous block of len(tasks) sequence numbers in ONE
        # round trip (INCRBY returns the counter's value AFTER the
        # increment, so the block is [end - len(tasks) + 1, end]), then
        # hand out one distinct seq per task below in insertion order.
        # Without this, the old code computed a fresh time.time()-derived
        # score per loop iteration; successive calls inside a tight
        # synchronous loop routinely land under the ~142us collision
        # window (see _PENDING_SCORE_SEQ_SCALE), so the whole batch
        # shared one score and fell back to Valkey's lexicographic
        # task-id tiebreak instead of enqueue order.
        seq_block_end = await self.client.incrby(f"queue:{queue}:seq", len(tasks))
        seq_block_start = seq_block_end - len(tasks) + 1

        for batch_index, task_spec in enumerate(tasks):
            task_id = generate_id()
            task_ids.append(task_id)

            operation = task_spec["operation"]
            data = task_spec["data"]
            priority = task_spec["priority"]
            metadata = dict(task_spec.get("metadata", {}))

            # Propagate Cortex request_id as correlation_id when not already set.
            if "correlation_id" not in metadata and _ctx_request_id:
                metadata["correlation_id"] = _ctx_request_id

            # Store task metadata
            pipeline.hset(
                f"queue:task:{task_id}",
                mapping={
                    "task_id": task_id,
                    "queue": queue,
                    "operation": operation,
                    "status": "queued",
                    "priority": str(priority),
                    "created_at": created_at,
                    "data": json.dumps(data),
                    "metadata": json.dumps(metadata),
                    "result_ttl": str(result_ttl),
                    "attempts": "0",
                    "payload_version": str(CURRENT_PAYLOAD_VERSION),
                },
            )

            # Add to pending sorted set (ZPOPMAX convention — higher score
            # pops first). Each task draws its own seq from the block
            # reserved above, ascending in insertion order, so an earlier
            # task in the batch always scores higher (pops first) than a
            # later one at the same priority.
            seq = seq_block_start + batch_index
            score = compose_pending_score(priority, seq)
            pipeline.zadd(f"queue:{queue}:pending", {task_id: score})

            # Add to per-queue recent list
            pipeline.lpush(f"queue:{queue}:recent", task_id)

            # Add to global recent list
            pipeline.lpush("queue:recent", task_id)

        # Trim recent lists once (not per-task)
        pipeline.ltrim("queue:recent", 0, 999)
        pipeline.ltrim(f"queue:{queue}:recent", 0, 999)

        await pipeline.execute()

        logger.debug(
            "tasks_batch_queued",
            queue=queue,
            count=len(task_ids),
        )
        return task_ids

    # ------------------------------------------------------------------
    # Metadata utilities
    # ------------------------------------------------------------------
    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get task metadata by task ID."""
        if not self.client:
            return None
        hgetall_result = self.client.hgetall(f"queue:task:{task_id}")
        record = await hgetall_result if not isinstance(hgetall_result, dict) else hgetall_result
        if not record:
            return None
        return self._decode_record(record)

    async def get_recent_tasks(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        queues: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent tasks from specified queues.

        Multi-queue calls merge the per-queue recent lists into one
        globally recency-sorted window: each queue contributes its first
        ``offset + limit`` entries, the merged records are sorted by
        ``created_at`` descending, and the global ``[offset, offset+limit)``
        slice is returned — applying ``offset`` per queue and concatenating
        would return up to ``len(queues) x limit`` rows and skip/duplicate
        entries across pages.
        """
        if not self.client:
            return []

        task_ids_raw: list[bytes] = []
        if queues:
            for queue in queues:
                lrange_result = self.client.lrange(f"queue:{queue}:recent", 0, offset + limit - 1)
                range_data = (
                    await lrange_result if not isinstance(lrange_result, list) else lrange_result
                )
                task_ids_raw.extend(range_data)
        else:
            lrange_result = self.client.lrange("queue:recent", offset, offset + limit - 1)
            task_ids_raw = (
                await lrange_result if not isinstance(lrange_result, list) else lrange_result
            )

        task_ids = [_decode_bytes(task_id) for task_id in task_ids_raw]

        if not task_ids:
            return []

        pipeline = self.client.pipeline()
        for task_id in task_ids:
            pipeline.hgetall(f"queue:task:{task_id}")

        results = await pipeline.execute()
        records = [self._decode_record(record) for record in results if record]

        if queues:
            records.sort(key=lambda record: record["created_at"], reverse=True)
            return records[offset : offset + limit]
        return records

    async def get_recent_tasks_count(self, queues: list[str] | None = None) -> int:
        """Get total count of recent tasks (for pagination)."""
        if not self.client:
            return 0

        if queues:
            total = 0
            for queue in queues:
                llen_result = self.client.llen(f"queue:{queue}:recent")
                count = await llen_result if not isinstance(llen_result, int) else llen_result
                total += count
            return total
        llen_result = self.client.llen("queue:recent")
        return await llen_result if not isinstance(llen_result, int) else llen_result

    async def get_result(self, task_id: str) -> Any | None:
        """Get task result by task ID."""
        if not self.client:
            return None
        payload = await self.client.get(f"queue:result:{task_id}")
        if payload is None:
            return None
        return json.loads(payload)

    async def cancel_task(self, task_id: str) -> bool:  # noqa: PLR0911 — linear guard-return flow; each return is a distinct race outcome
        """Cancel a queued or running task.

        For queued tasks: removes from pending sorted set and marks cancelled.
        For running tasks: sets a cancellation flag that the worker checks
        between processing batches, and durably persists the cancellation
        timestamp to ``ChunkExtractionTask.cancelled_at`` in SQLite so that
        ``is_task_cancelled`` can detect it even after the Valkey key expires.

        Returns:
            True if task was successfully cancelled or was already terminal.
            False if task cannot be cancelled.

        """
        self._require_connection()

        task = await self.get_task(task_id)
        if not task:
            return False

        task_status = task.get("status")

        # Already in terminal state
        if task_status in {"completed", "failed", "cancelled"}:
            return True

        task_queue = task.get("queue", "")
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")

        if task_status == "queued":
            outcome = await self.guarded_status_write(
                task_id,
                new_status="cancelled",
                allowed_from=("queued",),
                extra_fields={"completed_at": _iso_now()},
                remove_from=f"queue:{task_queue}:pending",
                removal="zrem",
            )
            if outcome == GUARDED_OK:
                logger.info("task_cancelled", task_id=task_id, was_queued=True)
                return True
            if outcome == GUARDED_MISSING:
                return False
            if outcome in {"completed", "failed", "cancelled"}:
                return True
            task_status = outcome  # raced queued -> running: fall through

        if task_status == "running":
            # Cooperative flag + durable SQLite persist are safe regardless of
            # the race — handlers poll the flag between batches, and the
            # SQLite row lets is_task_cancelled outlive the Valkey key TTL.
            # The status write itself is guarded: a task that completed in
            # the window keeps status=completed and its result reachable
            # (cancel-vs-complete precedence: cancel loses; decided
            # 2026-07-23). The old orphaned-running SISMEMBER branch is
            # subsumed — SREM of a non-member is a no-op inside the script.
            await self.client.set(
                f"queue:cancel:{task_id}",
                "1",
                ex=self._cancel_ttl,
            )
            self._persist_cancellation_to_db(task_id, task)
            outcome = await self.guarded_status_write(
                task_id,
                new_status="cancelled",
                allowed_from=("queued", "running"),
                extra_fields={"completed_at": _iso_now()},
                remove_from=f"queue:{task_queue}:running",
                removal="srem",
            )
            if outcome == GUARDED_MISSING:
                return False
            logger.info("task_cancel_flag_set", task_id=task_id, status_write=outcome)
            return True

        # Unknown status — try to mark cancelled anyway
        hset_result = self.client.hset(
            f"queue:task:{task_id}",
            mapping={"status": "cancelled", "completed_at": _iso_now()},
        )
        if not isinstance(hset_result, int):
            await hset_result
        return True

    def _persist_cancellation_to_db(self, task_id: str, task: dict[str, Any]) -> None:
        """Write ChunkExtractionTask.cancelled_at to SQLite for durable cancellation.

        Called synchronously from cancel_task after the Valkey cancel flag
        is set.  Only acts when the task hash contains a database_name field
        (i.e. is a chunk-extraction task); all other task types are silently
        skipped.

        Any exception from the DB write is caught and logged as a warning so
        that the in-memory Valkey cancel flag still takes effect even if SQLite
        is temporarily unavailable.

        Args:
            task_id: Queue task ID (matches ChunkExtractionTask.queue_task_id).
            task: Decoded queue task hash dict (from get_task).

        """
        database_name: str | None = task.get("data", {}).get("database_name")
        if not database_name:
            # Not an extraction task — no ChunkExtractionTask row to update.
            return

        try:
            with _adapter_db_session(database_name) as session:
                stmt = (
                    select(ChunkExtractionTask)
                    .where(ChunkExtractionTask.queue_task_id == task_id)
                    .where(ChunkExtractionTask.database_name == database_name)
                )
                db_task = session.exec(stmt).first()
                if db_task is not None:
                    db_task.cancelled_at = datetime.now(UTC)
                    session.maybe_commit()
                    logger.info(
                        "task_cancellation_persisted",
                        task_id=task_id,
                        chunk_task_id=db_task.id,
                        database_name=database_name,
                    )
        except Exception:
            logger.warning(
                "task_cancellation_db_persist_failed",
                task_id=task_id,
                database_name=database_name,
                exc_info=True,
            )

    async def cancel_tasks_batch(self, task_ids: list[str]) -> dict[str, Any]:
        """Cancel multiple tasks by ID.

        Each task's cancellation is a per-task guarded (CAS) status write —
        not a single pipeline — so a task that completes mid-batch is not
        clobbered. After the guarded writes, each task is also persisted to SQLite via
        ``_persist_cancellation_to_db`` so that ``is_task_cancelled`` can
        detect the cancellation even after the Valkey key expires (TTL
        fallback).  DB write failures are caught and logged per-task — they
        do not prevent the Valkey cancellation from landing.

        Args:
            task_ids: List of task IDs to cancel

        Returns:
            Dict with 'cancelled' count and 'failed' list of {task_id, reason}

        """
        self._require_connection()

        if not task_ids:
            return {"cancelled": 0, "failed": []}

        tasks_to_cancel = []
        failed = []

        for task_id in task_ids:
            task = await self.get_task(task_id)
            if not task:
                failed.append({"task_id": task_id, "reason": "Task not found"})
                continue
            if task.get("status") in {"completed", "failed", "cancelled"}:
                continue
            tasks_to_cancel.append(task)

        if not tasks_to_cancel:
            return {"cancelled": 0, "failed": failed}

        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        completed_at = _iso_now()
        cancelled = 0

        # Per-task guarded writes instead of one pipeline: batch sizes here
        # are UI multi-selects, and the unguarded pipeline HSET could clobber
        # a task that completed (or raced queued->running) mid-batch. Same
        # semantics as cancel_task, including the cooperative cancel flag
        # for running tasks (previously never set on this path).
        for task in tasks_to_cancel:
            task_id = task["task_id"]
            task_queue = task["queue"]
            task_status = task.get("status")

            if task_status == "queued":
                outcome = await self.guarded_status_write(
                    task_id,
                    new_status="cancelled",
                    allowed_from=("queued",),
                    extra_fields={"completed_at": completed_at},
                    remove_from=f"queue:{task_queue}:pending",
                    removal="zrem",
                )
                if outcome == GUARDED_OK:
                    cancelled += 1
                    continue
                if outcome != "running":
                    failed.append({"task_id": task_id, "reason": f"status is {outcome}"})
                    continue
                # raced queued -> running: fall through to the running cancel

            await self.client.set(f"queue:cancel:{task_id}", "1", ex=self._cancel_ttl)
            self._persist_cancellation_to_db(task_id, task)
            outcome = await self.guarded_status_write(
                task_id,
                new_status="cancelled",
                allowed_from=("queued", "running"),
                extra_fields={"completed_at": completed_at},
                remove_from=f"queue:{task_queue}:running",
                removal="srem",
            )
            if outcome == GUARDED_OK:
                cancelled += 1
            else:
                failed.append({"task_id": task_id, "reason": f"status is {outcome}"})

        return {"cancelled": cancelled, "failed": failed}

    async def retry_task(self, task_id: str) -> str | None:
        """Retry a failed task by re-enqueueing it with the same parameters.

        Args:
            task_id: ID of the failed task to retry

        Returns:
            New task ID if successfully re-enqueued, None otherwise

        Raises:
            ValueError: If task is not in failed status

        """
        self._require_connection()

        task = await self.get_task(task_id)
        if not task:
            logger.warning("task_retry_failed", task_id=task_id, reason="task_not_found")
            return None

        if task.get("status") != "failed":
            msg = f"Cannot retry task {task_id}: status is '{task.get('status')}', must be 'failed'"
            raise ValueError(msg)

        # Claim the original atomically BEFORE enqueueing: the old
        # check-then-enqueue let two concurrent retries (double-click) both
        # pass the status check and enqueue duplicate work (duplicate LLM
        # cost). Only the caller whose failed->retried CAS wins proceeds;
        # the loser returns None. The pre-read above is for the enqueue
        # parameters and the fast ValueError — the CAS is the gate.
        outcome = await self.guarded_status_write(
            task_id,
            new_status="retried",
            allowed_from=("failed",),
            extra_fields={"retried_at": _iso_now()},
        )
        if outcome != GUARDED_OK:
            logger.info("task_retry_skipped", task_id=task_id, status=outcome)
            return None

        try:
            new_task_id = await self.enqueue_task(
                queue=task["queue"],
                operation=task["operation"],
                data=task.get("data", {}),
                priority=task["priority"],
                metadata={
                    **task.get("metadata", {}),
                    "retried_from": task_id,
                },
            )
        except Exception:
            # Best-effort revert so the original stays retryable after a
            # transient enqueue failure.
            await self.guarded_status_write(task_id, new_status="failed", allowed_from=("retried",))
            raise

        if self.client is not None:
            hset_result = self.client.hset(
                f"queue:task:{task_id}", mapping={"retried_to": new_task_id}
            )
            if not isinstance(hset_result, int):
                await hset_result

        logger.info(
            "task_retried",
            original_task_id=task_id,
            new_task_id=new_task_id,
            queue=task["queue"],
            operation=task["operation"],
        )
        return new_task_id

    async def cancel_by_metadata(self, metadata: dict[str, Any], queue: str | None = None) -> int:
        """Cancel all tasks matching the given metadata.

        Queued matches are removed via guarded (CAS) delete; running matches
        get the cooperative cancel flag plus a guarded status write — the
        same per-task primitives as ``cancel_task``/``cancel_tasks_batch``.
        """
        self._require_connection()

        logger.info("cancel_by_metadata_started", metadata=metadata, queue=queue)

        tasks_to_cancel = []
        checked = 0
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        async for key in self.client.scan_iter(match="queue:task:*"):
            key_str = _decode_bytes(key)
            task_id = key_str.split(":")[-1]
            task = await self.get_task(task_id)
            if not task:
                continue

            checked += 1
            task_status = task.get("status")
            task_queue = task.get("queue")
            task_metadata = task.get("metadata", {})

            if queue and task_queue != queue:
                continue
            if task_status not in {"queued", "running"}:
                continue
            if all(task_metadata.get(k) == v for k, v in metadata.items()):
                tasks_to_cancel.append(task)

        if not tasks_to_cancel:
            logger.info(
                "cancel_by_metadata_completed",
                tasks_checked=checked,
                tasks_cancelled=0,
                metadata=metadata,
            )
            return 0

        cancelled = 0

        # Per-task guarded operations instead of one pipeline: the old
        # pipeline.delete could destroy the live hash of a task that went
        # queued->running between scan_iter and execute, leaving a bare
        # partial record after complete_task_atomic. The guarded delete
        # refuses in that case and the task gets the running-path cancel
        # (cooperative flag + guarded status write) instead.
        for task in tasks_to_cancel:
            task_id = task["task_id"]
            task_queue = task["queue"]
            task_status = task.get("status")

            if task_status == "queued":
                outcome = await self.guarded_delete(
                    task_id,
                    allowed_from=("queued",),
                    remove_from=f"queue:{task_queue}:pending",
                    removal="zrem",
                )
                if outcome == GUARDED_OK:
                    self._persist_cancellation_to_db(task_id, task)
                    cancelled += 1
                    continue
                if outcome != "running":
                    # Finished or vanished in the window — nothing to cancel.
                    continue
                # raced queued -> running: fall through to the running cancel

            # Set cancellation flag — worker checks this between batches.
            # Use the same extended TTL as cancel_task() so the flag
            # outlives the handler's worst-case run time. The status write
            # is guarded: completion in the window wins (cancel loses).
            await self.client.set(f"queue:cancel:{task_id}", "1", ex=self._cancel_ttl)
            self._persist_cancellation_to_db(task_id, task)
            outcome = await self.guarded_status_write(
                task_id,
                new_status="cancelled",
                allowed_from=("queued", "running"),
                extra_fields={"completed_at": _iso_now()},
                remove_from=f"queue:{task_queue}:running",
                removal="srem",
            )
            if outcome == GUARDED_OK:
                cancelled += 1
            logger.info("task_cancel_flag_set", task_id=task_id)
        logger.info(
            "cancel_by_metadata_completed",
            tasks_checked=checked,
            tasks_cancelled=cancelled,
            metadata=metadata,
        )
        return cancelled

    async def in_flight_chunk_task_ids(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> set[str]:
        """Return chunk_task_ids with queued/running EXTRACT_CHUNK Valkey tasks.

        Used by ``SourceRecovery`` to filter the per-chunk recovery dispatch
        list: a chunk that already has a live Valkey task does not need to be
        re-dispatched on this reconcile pass. Without this filter, every
        reconcile tick that runs while a worker is mid-processing on a long
        chunk re-enqueues every pending chunk and bumps recovery_attempts on
        the source — driving a healthy long-running source toward the
        exhaustion cap.

        Performance: O(n) over all task hashes, mirroring
        ``task_exists_for_source``. Acceptable for the ~60s reconcile
        interval at current scale.

        DELIBERATE DIVERGENCE from ``in_flight_vision_page_ids`` (2026-08-15
        review): that sibling RE-RAISES scan errors instead of swallowing
        them, because its caller pays a billable LLM call before its CAS
        can reject a duplicate dispatch. This method keeps swallowing and
        returning an empty set on error, because the extract_chunk callers'
        dispatch-all-on-uncertainty fallback is idempotent-safe — chunk
        extraction has a documented pre-work DB short-circuit, so a
        redundant dispatch costs a wasted queue hop, not a billable
        duplicate. Do not change this method's error handling without
        re-auditing that safety argument.

        Args:
            source_id: Source whose chunks to enumerate.
            database_name: Database scope (multi-DB isolation).

        Returns:
            Set of chunk_task_id strings drawn from each EXTRACT_CHUNK
            task's ``data.chunk_task_id`` payload. Returns an empty set if
            the queue is unavailable or scanning fails — callers must treat
            an empty set as "no information," not "no tasks in flight."
        """
        if self.client is None:
            return set()

        in_flight: set[str] = set()
        try:
            async for key in self.client.scan_iter(match="queue:task:*"):
                key_str = _decode_bytes(key)
                task_id = key_str.split(":")[-1]
                task = await self.get_task(task_id)
                if not task:
                    continue
                if task.get("operation") != "extract_chunk":
                    continue
                if task.get("status") not in ("queued", "running"):
                    continue
                meta = task.get("metadata") or {}
                if meta.get("source_id") != source_id:
                    continue
                if meta.get("database_name") != database_name:
                    continue
                data = task.get("data") or {}
                chunk_task_id = data.get("chunk_task_id")
                if isinstance(chunk_task_id, str):
                    in_flight.add(chunk_task_id)
        except Exception as exc:
            logger.debug(
                "in_flight_chunk_task_ids_scan_failed",
                source_id=source_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return set()
        return in_flight

    async def in_flight_vision_page_ids(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> set[str]:
        """Return page_ids with queued/running OP_VISION_PAGE Valkey tasks.

        Used by ``SourceRecovery`` to filter the per-page vision recovery
        dispatch list: a page that already has a live Valkey task does not
        need to be re-dispatched on this reconcile pass. Mirrors
        ``in_flight_chunk_task_ids`` at the vision page granularity —
        ``vision_page`` tasks for a source are enqueued as one N-page batch
        and drained one at a time (QUEUE_LLM concurrency is 1), so a
        source-level "any task live" check would block recovery of a
        genuinely lost page (worker crash / Valkey eviction) for as long as
        ANY sibling page in the batch is still live — most of the job's
        duration in the common case. Filtering by page identity instead
        recovers only the pages that are actually missing.

        Performance: O(n) over all task hashes, mirroring
        ``in_flight_chunk_task_ids`` / ``task_exists_for_source``.
        Acceptable for the ~60s reconcile interval at current scale.

        DELIBERATE DIVERGENCE from ``in_flight_chunk_task_ids`` (2026-08-15
        review): that sibling swallows scan errors and returns an empty
        set, because its caller's dispatch-all-on-uncertainty fallback is
        idempotent-safe (extract_chunk has a documented pre-work DB
        short-circuit). This method instead RE-RAISES — a vision page pays
        a billable LLM call BEFORE its CAS rejects a duplicate, so
        dispatching on uncertainty here would reopen the exact
        duplicate-billing bug the filter exists to close. The caller,
        ``SourceRecovery._classify_vision_pending``, catches this and
        suppresses the WHOLE dispatch on any exception rather than falling
        back to "assume nothing is in flight" — that suppress branch is
        unreachable unless this method actually lets the failure through.

        Args:
            source_id: Source whose vision pages to enumerate.
            database_name: Database scope (multi-DB isolation).

        Returns:
            Set of page_id strings drawn from each ``vision_page`` task's
            ``data.page_id`` payload.

        Raises:
            QueueUnavailableError: The queue client is not connected.
            Exception: Any error encountered while scanning or decoding
                task hashes propagates unchanged (logged at warning first)
                so the caller's suppress-on-uncertainty handler can act on
                it — see the divergence note above.
        """
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")

        in_flight: set[str] = set()
        try:
            async for key in self.client.scan_iter(match="queue:task:*"):
                key_str = _decode_bytes(key)
                task_id = key_str.split(":")[-1]
                task = await self.get_task(task_id)
                if not task:
                    continue
                if task.get("operation") != "vision_page":
                    continue
                if task.get("status") not in ("queued", "running"):
                    continue
                meta = task.get("metadata") or {}
                if meta.get("source_id") != source_id:
                    continue
                if meta.get("database_name") != database_name:
                    continue
                data = task.get("data") or {}
                page_id = data.get("page_id")
                if isinstance(page_id, str):
                    in_flight.add(page_id)
        except Exception as exc:
            logger.warning(
                "in_flight_vision_page_ids_scan_failed",
                source_id=source_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        return in_flight

    async def task_exists_for_source(
        self,
        *,
        source_id: str,
        database_name: str,
        operations: list[str],
    ) -> bool:
        """Check whether any task for this source is currently queued or running.

        Scans the ``queue:task:*`` hash keyspace, loads each task via
        ``get_task``, and matches against ``metadata.source_id``,
        ``metadata.database_name``, and the given operations list.
        Used by the SourceRecovery reconciler as a debounce:
        if the queue still has a task for this source, there's no
        need to re-enqueue.

        Performance: O(n) over all task hashes. Acceptable for the
        ~60s reconciler interval; a future optimization could maintain
        a per-source secondary index, but not worth the complexity at
        the current scale.

        Args:
            source_id: Source to look up.
            database_name: Database scope (multi-DB isolation).
            operations: Operation names that count as "this source is
                in flight" (typically one or two specific operations
                relevant to the source's current status).

        Returns:
            True if a matching queued-or-running task exists, False
            otherwise. Returns False on any queue error so the
            reconciler errs on the side of re-dispatching.
        """
        if self.client is None:
            return False

        allowed_ops = set(operations)
        try:
            async for key in self.client.scan_iter(match="queue:task:*"):
                key_str = _decode_bytes(key)
                task_id = key_str.split(":")[-1]
                task = await self.get_task(task_id)
                if not task:
                    continue
                if task.get("operation") not in allowed_ops:
                    continue
                if task.get("status") not in ("queued", "running"):
                    continue
                meta = task.get("metadata") or {}
                if meta.get("source_id") != source_id:
                    continue
                # Defensive: missing database_name in metadata is NOT a match.
                # Previously treated as "match any DB"; now every enqueue helper
                # sets database_name explicitly (queue_utils._build_metadata),
                # so a missing value indicates a foreign/legacy task.
                if meta.get("database_name") != database_name:
                    continue
                return True
        except Exception as exc:
            # Skip-on-error policy (Decision: skip recovery on queue error).
            # Returning False here used to cause SourceRecovery to dispatch a
            # duplicate task whenever Valkey blipped during a scan. Re-raise so
            # the recovery wrapper's except->True arm fires and recovery skips
            # this pass instead of dispatching a duplicate. Audit fix #H6.
            logger.warning(
                "task_exists_for_source_scan_failed_reraising",
                source_id=source_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        return False

    async def task_exists_for_chat(
        self,
        *,
        chat_id: str,
        database_name: str,
    ) -> bool:
        """Check whether a live queue task backs this chat's in-flight turn.

        Scans the ``queue:task:*`` hash keyspace for an ``OP_CHAT_BACKGROUND``
        task that is still ``queued`` or ``running`` for this chat, matched
        via ``data.chat_id`` / ``data.database_name`` (chat-turn tasks don't
        put ``database_name`` in ``metadata`` the way source tasks do, so
        ``data`` — always present — is the reliable source for both fields).

        Used by ``reconcile_stuck_chats`` as the primary liveness signal: a
        chat's ``updated_at`` is never bumped mid-turn, so a stale timestamp
        alone can't distinguish a healthy long-running turn from a crashed
        one. A live matching task means the worker is still on it.

        Performance: O(n) over all task hashes, mirroring
        ``task_exists_for_source``. Acceptable at the sweeper's scan cadence
        and current scale.

        Args:
            chat_id: Chat whose in-flight turn to check.
            database_name: Database scope (multi-DB isolation).

        Returns:
            True if a matching queued-or-running task exists. False ONLY
            when the queue backend is connected, the scan completed, and
            no such task was found — a genuine miss.

        Raises:
            QueueUnavailableError: If the queue backend isn't connected
                (``self.client is None``). This is a "we don't know" state,
                not "no task": Cortex's ``connect_with_retry`` runs once at
                startup with ``required=False`` and there is no reconnect
                loop, so a Valkey outage at boot leaves ``self.client`` as
                ``None`` for the process's entire lifetime. Returning a
                plain ``False`` here would make every future sweep silently
                fall back to a stale-looking "no task" for chats that may
                well still be running — reviving the exact bug this method
                exists to fix. Raising (instead of swallowing to False)
                forces callers to apply their own fail-safe default, same
                as the scan-exception path below.
            Exception: Whatever the underlying scan raised, if a scan is in
                progress when the error occurs. Callers must treat this as
                "unknown" rather than "no task" — re-raising (instead of
                swallowing to False) lets the caller apply its own
                fail-safe default, mirroring ``task_exists_for_source``.
        """
        if self.client is None:
            raise QueueUnavailableError(
                "Queue server is not connected",
                details={"chat_id": chat_id, "database_name": database_name},
            )

        try:
            async for key in self.client.scan_iter(match="queue:task:*"):
                key_str = _decode_bytes(key)
                task_id = key_str.split(":")[-1]
                task = await self.get_task(task_id)
                if not task:
                    continue
                if task.get("operation") != OP_CHAT_BACKGROUND:
                    continue
                if task.get("status") not in ("queued", "running"):
                    continue
                data = task.get("data") or {}
                if data.get("chat_id") != chat_id:
                    continue
                if data.get("database_name") != database_name:
                    continue
                return True
        except Exception as exc:
            logger.warning(
                "task_exists_for_chat_scan_failed_reraising",
                chat_id=chat_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        return False

    async def cancel_all_tasks(self, queue: str | None = None) -> int:
        """Cancel all tasks in specified queue or all queues.

        Delegates to :meth:`cancel_tasks_batch` so running tasks get the
        same cooperative treatment as every other cancel path: the
        ``queue:cancel:{task_id}`` flag is set (so handlers polling
        ``is_task_cancelled`` actually stop), the DB fallback is persisted,
        and the status write is CAS-guarded — the previous unguarded
        pipeline HSET never set the flag, so a running handler finished
        unaware and its success path silently overwrote ``cancelled``
        with ``completed``.
        """
        self._require_connection()

        task_ids: list[str] = []
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        async for key in self.client.scan_iter(match="queue:task:*"):
            key_str = _decode_bytes(key)
            task_id = key_str.split(":")[-1]
            task = await self.get_task(task_id)
            if not task:
                continue
            if queue and task["queue"] != queue:
                continue
            if task.get("status") in {"completed", "failed", "cancelled"}:
                continue
            task_ids.append(task_id)

        if not task_ids:
            return 0

        logger.info(
            "bulk_cancel_started",
            task_count=len(task_ids),
            queue=queue,
        )

        result = await self.cancel_tasks_batch(task_ids)
        cancelled = int(result["cancelled"])
        logger.info(
            "bulk_cancel_completed",
            total_cancelled=cancelled,
            failed_count=len(result["failed"]),
            queue=queue,
        )
        return cancelled

    async def clear_old_completed_tasks(
        self, *, queue: str | None = None, older_than_hours: int = 0
    ) -> int:
        """Clear old completed/failed/cancelled tasks."""
        self._require_connection()

        threshold = None
        if older_than_hours > 0:
            threshold = datetime.now(UTC) - timedelta(hours=older_than_hours)

        removed = 0
        if self.client is None:
            raise QueueUnavailableError("Queue server is not connected")
        async for key in self.client.scan_iter(match="queue:task:*"):
            hgetall_result = self.client.hgetall(key)
            record = (
                await hgetall_result if not isinstance(hgetall_result, dict) else hgetall_result
            )
            if not record:
                continue
            decoded = self._decode_record(record)
            if queue and decoded.get("queue") != queue:
                continue
            status = decoded.get("status")
            if status not in {"completed", "failed", "cancelled"}:
                continue
            if threshold and decoded.get("completed_at"):
                completed = datetime.fromisoformat(decoded["completed_at"])
                if completed >= threshold:
                    continue
            await self.client.delete(key)
            await self.client.delete(f"queue:result:{decoded['task_id']}")
            removed += 1

        # Clean up stale task IDs from recent lists
        await self._cleanup_stale_recent_list("queue:recent")
        if queue:
            await self._cleanup_stale_recent_list(f"queue:{queue}:recent")
        else:
            async for key in self.client.scan_iter(match="queue:*:recent"):
                await self._cleanup_stale_recent_list(_decode_bytes(key))

        return removed

    async def _cleanup_stale_recent_list(self, list_key: str) -> None:
        """Remove task IDs from recent list if their task record no longer exists."""
        if self.client is None:
            return
        lrange_result = self.client.lrange(list_key, 0, -1)
        task_ids_raw = await lrange_result if not isinstance(lrange_result, list) else lrange_result
        if not task_ids_raw:
            return

        pipeline = self.client.pipeline()
        for task_id_raw in task_ids_raw:
            task_id = _decode_bytes(task_id_raw)
            pipeline.exists(f"queue:task:{task_id}")

        exists_results = await pipeline.execute()

        removed_count = 0
        for task_id_raw, exists in zip(task_ids_raw, exists_results, strict=False):
            if not exists:
                task_id = _decode_bytes(task_id_raw)
                lrem_result = self.client.lrem(list_key, 0, task_id)
                if not isinstance(lrem_result, int):
                    await lrem_result
                removed_count += 1

        if removed_count > 0:
            logger.debug("stale_task_ids_removed", list_key=list_key, removed_count=removed_count)

    async def clear_all_stats(self) -> None:
        """Clear all queue statistics."""
        self._require_connection()
        if self.client is None:
            return
        delete_result = self.client.delete("queue:recent")
        if not isinstance(delete_result, int):
            await delete_result
        async for key in self.client.scan_iter(match="queue:*:recent"):
            delete_result2 = self.client.delete(key)
            if not isinstance(delete_result2, int):
                await delete_result2

    async def get_queue_stats(self, queue: str) -> dict[str, Any]:
        """Get queue statistics (delegates to QueueMonitor)."""
        if self.monitor:
            return await self.monitor.get_queue_stats(queue)
        return {
            "queue": queue,
            "queued": 0,
            "running": 0,
            "completed_recent": 0,
            "failed_recent": 0,
        }

    async def track_tokens(
        self, queue: str, input_tokens: int, output_tokens: int, cost_usd: float = 0.0
    ) -> None:
        """Track token usage (delegates to QueueMonitor)."""
        if self.monitor:
            await self.monitor.track_tokens(queue, input_tokens, output_tokens, cost_usd)

    async def get_token_stats(
        self,
        queue: str,
        custom_input_cost: float = 0.0,
        custom_output_cost: float = 0.0,
    ) -> dict[str, Any]:
        """Get token statistics (delegates to QueueMonitor)."""
        if self.monitor:
            return await self.monitor.get_token_stats(queue, custom_input_cost, custom_output_cost)
        return {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }

    async def clear_token_stats(self, queue: str | None = None) -> None:
        """Clear token statistics (delegates to QueueMonitor)."""
        if self.monitor:
            await self.monitor.clear_token_stats(queue)

    async def get_all_stats(self) -> list[dict[str, Any]]:
        """Get all queue stats (delegates to QueueMonitor with auto-detection)."""
        if self.monitor:
            return await self.monitor.get_all_stats()
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _decode_record(self, record: dict[bytes | str, bytes | str]) -> dict[str, Any]:
        """Decode a queue hash record into a typed dict."""

        def get_field(key: str, default: str = "") -> str:
            value = record.get(key) or record.get(key.encode())
            return _decode_bytes(value) if value else default

        data = {
            "task_id": get_field("task_id"),
            "queue": get_field("queue"),
            "operation": get_field("operation"),
            "status": get_field("status"),
            "priority": int(get_field("priority", "0")),
            "created_at": get_field("created_at"),
            "metadata": json.loads(get_field("metadata", "{}")),
            "data": json.loads(get_field("data", "{}")),
            "attempts": int(get_field("attempts", "0")),
            "payload_version": int(get_field("payload_version", "1")),
        }

        started_at = get_field("started_at")
        if started_at:
            data["started_at"] = started_at

        completed_at = get_field("completed_at")
        if completed_at:
            data["completed_at"] = completed_at

        error = get_field("error")
        if error:
            data["error"] = "Task failed"

        error_type = get_field("error_type")
        if error_type:
            data["error_type"] = error_type

        return data

    async def is_task_cancelled(self, task_id: str, database_name: str | None = None) -> bool:
        """Check if a task has been cancelled via Valkey flag or SQLite record.

        Uses a two-tier check to handle Valkey TTL expiry:

        1. **Fast path** — Valkey ``queue:cancel:{task_id}`` key present → True.
        2. **DB fallback** — When the key is absent and ``database_name`` is
           provided, queries ``ChunkExtractionTask.cancelled_at`` in SQLite.
           Handlers running past the Valkey TTL (> ``llm_worker_default``
           seconds) will still see the cancellation via this path.

        Callers that do not have access to ``database_name`` (e.g. non-extraction
        handlers) can omit the argument; they will rely solely on the Valkey key.

        Args:
            task_id: Task identifier to check.
            database_name: Name of the SQLite database (e.g. ``"default"``).
                When provided, enables the DB fallback on Valkey TTL expiry.

        Returns:
            True if the task has been flagged for cancellation.

        """
        if not self.client:
            return False

        # Fast path: Valkey key is present — no DB round-trip needed.
        result = await self.client.exists(f"queue:cancel:{task_id}")
        if bool(result):
            return True

        # DB fallback: Valkey key expired but handler is still running.
        if database_name:
            try:
                with _adapter_db_session(database_name) as session:
                    stmt = (
                        select(ChunkExtractionTask)
                        .where(ChunkExtractionTask.queue_task_id == task_id)
                        .where(ChunkExtractionTask.database_name == database_name)
                    )
                    db_task = session.exec(stmt).first()
                    if db_task is not None and db_task.cancelled_at is not None:
                        return True
            except Exception:
                logger.warning(
                    "is_task_cancelled_db_check_failed",
                    task_id=task_id,
                    database_name=database_name,
                    exc_info=True,
                )

        return False

    def _require_connection(self) -> None:
        """Verify queue connection is available."""
        if not self.client or not self._connected:
            msg = "Queue client unavailable - ensure Valkey is running or disable queueing in settings."
            raise QueueUnavailableError(msg)

    @property
    def queues(self) -> set[str]:
        """Get the set of registered queue names."""
        return self._queues

    @property
    def handlers(self) -> dict[str, dict[str, TaskHandler]]:
        """Get registered task handlers keyed by queue then operation."""
        return self._handlers

    @property
    def is_enabled(self) -> bool:
        """Check if queue is enabled."""
        return self._enabled

    @property
    def is_available(self) -> bool:
        """Check if queue is enabled and connected."""
        return self._enabled and self._connected and self.client is not None


queue_client = QueueClient()
