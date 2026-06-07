# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source-level recovery / reconciliation for resumability.

Scans non-terminal SourceRow states and re-dispatches missing queue
work so a source that crashed mid-processing can resume where it left
off instead of sitting stuck in the UI. Complements per-handler
idempotency guarantees and queue-level self-healing.

See the source resumability design
for the full recovery matrix and architectural rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.operations import queue_utils
from chaoscypher_core.operations.extraction.extraction_finalizer import TERMINAL_TASK_STATES
from chaoscypher_core.services.events.bus import event_bus
from chaoscypher_core.vision.states import VisionPageStatus


if TYPE_CHECKING:
    from chaoscypher_core.ports.source_recovery import SourceRecoveryPorts

logger = structlog.get_logger(__name__)


# Default limits for the max-recovery-attempts guard (BE-1).
# Duplicated in SourceRecoverySettings in chaoscypher_cortex so callers
# can wire the configured values in; these defaults ensure the guard
# works even when SourceRecovery is constructed without settings (e.g.
# tests, CLI usage).
DEFAULT_MAX_RECOVERY_ATTEMPTS: int = 10
DEFAULT_RECOVERY_WARN_THRESHOLD: int = 5

# Queue + operation identifiers. Kept as module constants for historical
# reasons — pre-fold, these could not be imported from the runtime layer.
# Post-fold, they could be routed through chaoscypher_core.constants; left
# inline for now to minimise merge surface in the fold refactor.
_QUEUE_OPERATIONS = "operations"
_QUEUE_LLM = "llm"
_OP_INDEX_DOCUMENT = "index_document"
_OP_IMPORT_ANALYSIS = "import_analysis"
_OP_IMPORT_COMMIT = "import_commit"
_OP_FINALIZE_EXTRACTION = "finalize_extraction"
_OP_EXTRACT_CHUNK = "extract_chunk"
_OP_VISION_PAGE = "vision_page"
_OP_VISION_FINALIZE = "vision_finalize"


def _build_file_info_from_source(source: dict[str, Any]) -> dict[str, Any]:
    """Build file_info dict from a source record for commit re-dispatch.

    The commit handler needs file metadata (filename, filepath, etc.)
    which lives on the source record itself — NOT in user_metadata.
    """
    return {
        "id": source.get("id"),
        "filename": source.get("filename"),
        "filepath": source.get("filepath"),
        "file_type": source.get("file_type"),
    }


@dataclass
class RecoveryStats:
    """Counters produced by a single source-recovery pass.

    Each reconcile_database call returns one of these so the caller
    (the neuron worker's periodic poller, a health check, or a manual
    admin trigger) can report how many sources were touched and how
    many were left alone.
    """

    recovered: int = 0
    skipped_paused: int = 0
    skipped_healthy: int = 0
    skipped_exhausted: int = 0
    total_scanned: int = 0

    def to_dict(self) -> dict[str, int]:
        """Flat dict form for structured logging and API responses."""
        return {
            "recovered": self.recovered,
            "skipped_paused": self.skipped_paused,
            "skipped_healthy": self.skipped_healthy,
            "skipped_exhausted": self.skipped_exhausted,
            "total_scanned": self.total_scanned,
        }


class SourceRecovery:
    """Scan sources and re-dispatch missing work.

    The reconciler holds only a storage adapter and a queue client —
    no heavy dependencies — so it can run cheaply from the neuron
    worker startup hook or from a periodic scheduler. Two entry
    points: ``reconcile_database`` for bulk scans and ``recover_source``
    for the single-source case that the manual resume endpoint
    calls into.
    """

    #: Source statuses that are not terminal and therefore eligible
    #: for recovery. Anything not in this set (``committed`` / error
    #: states) is left alone — recovery's job is to move in-flight
    #: sources forward, not to retry terminal failures.
    NON_TERMINAL_STATUSES: tuple[str, ...] = (
        "pending",
        "indexing",
        "indexed",
        "extracting",
        "extracted",
        "committing",
        "vision_pending",
    )

    #: Default stall threshold (seconds) above which a source is
    #: considered "no longer making forward progress" and eligible for
    #: re-dispatch by the bulk reconciler. Sized for the canonical
    #: self-hosted deployment: a long literary chunk on a local Ollama
    #: model routinely takes 60-300s in pass 1 + pass 2 of extraction,
    #: so anything under ~10 minutes risks tripping on healthy work.
    #: Override at construction from
    #: ``SourceRecoverySettings.stalled_threshold_seconds`` for cloud-LLM
    #: deployments where a tighter threshold makes sense.
    DEFAULT_STALLED_THRESHOLD_SECONDS: int = 600

    def __init__(
        self,
        *,
        adapter: SourceRecoveryPorts,
        queue_client: Any,
        stalled_threshold_seconds: int | None = None,
        max_recovery_attempts: int | None = None,
        recovery_warn_threshold: int | None = None,
    ) -> None:
        """Initialize the reconciler.

        Args:
            adapter: Any object satisfying ``SourceRecoveryPorts`` —
                the composite Protocol bundling the 9 storage methods
                this reconciler needs (``get_source``,
                ``list_sources_by_statuses``, ``get_system_state``,
                ``mark_source_exhausted``, ``get_active_extraction_job``,
                ``list_extraction_tasks_by_status``,
                ``increment_source_recovery_attempts``,
                ``update_source_last_activity``,
                ``list_source_entities`` /
                ``list_source_relationships`` /
                ``get_source_commit_payload``). ``SqliteAdapter``
                satisfies this structurally.
            queue_client: A queue client exposing
                ``enqueue(queue, operation, data, metadata, priority)``
                and, optionally, ``task_exists_for_source`` for the
                "already-queued" debounce.
            stalled_threshold_seconds: Age of ``last_activity_at`` (in
                seconds) above which the bulk reconciler may dispatch
                recovery work. Sources whose handlers heartbeated more
                recently than this are treated as healthy and skipped.
                Defaults to ``DEFAULT_STALLED_THRESHOLD_SECONDS``. The
                manual-resume entry point (``recover_source``) ignores
                this and always proceeds.
            max_recovery_attempts: Maximum number of reconciler-driven
                recovery dispatches before the source is transitioned to
                ``status=error`` with ``error_stage='recovery_exhausted'``.
                Defaults to ``DEFAULT_MAX_RECOVERY_ATTEMPTS``.
            recovery_warn_threshold: Log a WARNING when a source's
                ``recovery_attempts`` first reaches this value, giving
                operators early signal before exhaustion. Defaults to
                ``DEFAULT_RECOVERY_WARN_THRESHOLD``.
        """
        self.adapter = adapter
        self.queue_client = queue_client
        self.stalled_threshold_seconds = (
            stalled_threshold_seconds
            if stalled_threshold_seconds is not None
            else self.DEFAULT_STALLED_THRESHOLD_SECONDS
        )
        self.max_recovery_attempts = (
            max_recovery_attempts
            if max_recovery_attempts is not None
            else DEFAULT_MAX_RECOVERY_ATTEMPTS
        )
        self.recovery_warn_threshold = (
            recovery_warn_threshold
            if recovery_warn_threshold is not None
            else DEFAULT_RECOVERY_WARN_THRESHOLD
        )

    async def reconcile_database(self, database_name: str) -> RecoveryStats:
        """Scan all non-terminal sources in a database and recover them.

        Each source is processed in isolation — a failure on one source
        is logged and the scan continues. Callers can use the returned
        RecoveryStats to drive monitoring or surfaces.
        """
        stats = RecoveryStats()
        sources = self.adapter.list_sources_by_statuses(
            statuses=list(self.NON_TERMINAL_STATUSES),
            database_name=database_name,
        )
        stats.total_scanned = len(sources)

        for source in sources:
            try:
                await self._recover_one(source, database_name, stats, respect_stall_threshold=True)
            except Exception as exc:
                logger.exception(
                    "source_recovery_error",
                    source_id=source.get("id"),
                    database_name=database_name,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        return stats

    def count_awaiting_confirmation(self, database_name: str) -> int:
        """Count sources parked at ``awaiting_confirmation``.

        Pure observability — never dispatches. Surfaces partial-write-stuck
        and long-parked sources so a health check / dashboard can report
        "N sources awaiting your confirmation". Issues a single
        ``SELECT COUNT(*)`` via ``count_sources_by_statuses`` so no rows
        are materialized into Python objects — efficient at the
        "thousands of parked sources" scale the badge exists to surface.
        """
        return self.adapter.count_sources_by_statuses(
            statuses=["awaiting_confirmation"],
            database_name=database_name,
        )

    async def recover_source(self, *, source_id: str, database_name: str) -> bool:
        """Immediate single-source recovery.

        Used by the manual-resume endpoint (and future admin
        tooling). Looks up the source row, runs the same classify +
        dispatch path as the bulk scan, and returns whether a
        recovery action was actually taken. The stall-threshold
        debounce is bypassed here — when the user explicitly clicks
        "resume," we honor that intent immediately even if a handler
        heartbeated seconds ago.
        """
        source = self.adapter.get_source(source_id, database_name)
        if source is None:
            return False

        # Manual resume is an explicit user override (Decision 5). Reset
        # the recovery counter so the exhaustion guard in _recover_one
        # doesn't immediately re-mark the source exhausted, and so a
        # subsequent automatic reconcile starts the budget fresh. Audit fix #H3.
        self.adapter.reset_source_recovery_attempts(
            source_id=source_id,
            database_name=database_name,
        )
        source["recovery_attempts"] = 0

        stats = RecoveryStats(total_scanned=1)
        await self._recover_one(source, database_name, stats, respect_stall_threshold=False)
        return stats.recovered > 0

    def _is_recently_active(self, source: dict[str, Any]) -> bool:
        """Whether the source heartbeated within the stall threshold.

        Used to debounce automatic recovery: a handler that updated
        ``last_activity_at`` within the last
        ``stalled_threshold_seconds`` is considered healthy and the
        reconciler skips re-dispatch. Without this guard, a handler
        whose queue task entry is briefly invisible (claimed but
        between heartbeats, or just-completed but the source status
        hasn't transitioned yet) would be racing with a duplicate
        dispatch — the failure mode that surfaced as 421 stale-chunk
        embedding warnings during indexing.

        Sources with no ``last_activity_at`` (never touched, or the
        column is NULL) are treated as stale so the reconciler will
        attempt to dispatch them.
        """
        last_activity = source.get("last_activity_at")
        if last_activity is None:
            return False
        # Storage adapters may return datetime fields either as
        # ``datetime`` objects or ISO-8601 strings (the SQLite adapter
        # stringifies them in ``_entity_to_dict``). Normalize both.
        if isinstance(last_activity, str):
            try:
                last_activity = datetime.fromisoformat(last_activity)
            except ValueError:
                # Unparsable timestamp — treat as stale so the
                # reconciler can self-heal a corrupted row.
                return False
        # Tolerate naive datetimes from older rows / SQLite's loose
        # storage by assuming UTC when tzinfo is absent.
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - last_activity).total_seconds()
        return age_seconds < self.stalled_threshold_seconds

    async def _recover_one(
        self,
        source: dict[str, Any],
        database_name: str,
        stats: RecoveryStats,
        *,
        respect_stall_threshold: bool,
    ) -> None:
        """Process a single source row through the recovery pipeline.

        The pause guards run first: a paused source is never recovered
        automatically — the user has explicitly said "don't touch this
        one." Per-source pause and system-wide pause are both honored
        here, and both feed into
        `stats.skipped_paused`. Only ``_classify`` decides whether
        work is actually missing; when it returns None, the source is
        considered healthy (either making forward progress or
        genuinely in a state the recovery matrix treats as OK).
        """
        # Per-source pause
        if source.get("is_paused"):
            stats.skipped_paused += 1
            return

        # System-wide pause. Intentionally checked per-source
        # rather than once per reconcile pass so the single-source
        # recover_source entry point (used by the manual-resume
        # endpoint) honors the same semantics without needing a
        # separate code path. The system_state table is one row, so
        # the extra SELECT per source is negligible.
        system_state = self.adapter.get_system_state()
        if system_state and system_state.get("processing_paused"):
            stats.skipped_paused += 1
            return

        # Max-recovery-attempts guard (BE-1). A source that has been
        # dispatched N times without successfully reaching a terminal
        # state is stuck in a crash-loop. Transitioning it to
        # ``error`` stops the loop and surfaces the problem to the
        # operator. The manual-retry endpoint (Cluster D) will reset
        # ``recovery_attempts`` so the source can be retried after the
        # root cause is fixed.
        attempts = source.get("recovery_attempts", 0) or 0
        # Bulk reconcile (respect_stall_threshold=True) honors the
        # exhaustion guard. Manual resume (respect_stall_threshold=False)
        # bypasses it — the user has explicitly said "try this one again."
        # Decision 5 / Audit fix #H3.
        if respect_stall_threshold and attempts >= self.max_recovery_attempts:
            self.adapter.mark_source_exhausted(
                source_id=source["id"],
                database_name=database_name,
                error_message=(
                    f"Recovery exceeded {self.max_recovery_attempts} attempts. "
                    f"Last status: {source.get('status')}. "
                    f"Manual retry required."
                ),
            )
            stats.skipped_exhausted += 1
            event_bus.emit(
                "recovery_exhausted",
                action=(f"Source {source['id']} exhausted after {attempts} recovery attempts"),
                source="recovery",
                database_name=database_name,
                details={
                    "source_id": source["id"],
                    "last_status": source.get("status"),
                    "attempts": attempts,
                },
            )
            logger.error(
                "source_recovery_exhausted",
                source_id=source["id"],
                database_name=database_name,
                last_status=source.get("status"),
                attempts=attempts,
            )
            return

        if attempts == self.recovery_warn_threshold:
            logger.warning(
                "source_recovery_approaching_limit",
                source_id=source["id"],
                attempts=attempts,
                max=self.max_recovery_attempts,
            )

        # Stall-threshold debounce — automatic recovery only. When a
        # handler heartbeated recently (per ``stalled_threshold_seconds``),
        # we trust it is making forward progress and skip the queue
        # check entirely. The queue-check alone is not sufficient: a
        # task that was claimed by a worker briefly appears absent from
        # the queue scan (``queued|running`` filter), and a task that
        # just completed leaves the source in a non-terminal state for
        # the few hundred milliseconds before the status transition
        # commits. Both cases used to trigger spurious re-dispatch.
        # Manual resume (``recover_source``) bypasses this guard.
        if respect_stall_threshold and self._is_recently_active(source):
            stats.skipped_healthy += 1
            return

        source_status = source.get("status")
        action = await self._classify(source, database_name)
        if action is None:
            stats.skipped_healthy += 1
            return

        # Bump the recovery counter on the source row BEFORE dispatch so the
        # counter reflects the attempt regardless of whether dispatch lands.
        # If dispatch fails, the source stays in ERROR (it never moved
        # forward) and on the next reconcile pass the counter will already
        # show this attempt — preventing infinite retries on a queue
        # that's permanently broken. Worst case: counter is one too high
        # if dispatch fails, which is safer than counter never moving.
        # Audit fix #H7: counter MUST bump before any queue interaction,
        # including the commit-dispatch path. _classify only describes
        # the work to do — it never calls _dispatch_commit directly.
        self.adapter.increment_source_recovery_attempts(
            source_id=source["id"],
            database_name=database_name,
        )
        if action.get("dispatch_kind") == "commit":
            await self._dispatch_commit(
                source=source,
                database_name=database_name,
                commit_data=action["commit_data"],
            )
        else:
            await self._dispatch(action, source, database_name)

        # Touch last_activity so the next reconcile pass sees this
        # source as moving forward rather than stalled.
        self.adapter.update_source_last_activity(
            source_id=source["id"],
            database_name=database_name,
            at_time=datetime.now(UTC),
        )
        stats.recovered += 1

        action_taken = action.get("operation") or "compound"
        # Compound dispatches enqueue len(sub-tasks); single dispatches enqueue 1.
        enqueued_count = len(action.get("compound") or []) if "compound" in action else 1
        record_event = getattr(self.adapter, "record_recovery_event", None)
        if record_event is not None:
            try:
                record_event(
                    source_id=source["id"],
                    database_name=database_name,
                    from_status=source_status or "unknown",
                    action_taken=action_taken,
                    reason="compound" if "compound" in action else "stalled",
                    enqueued_count=enqueued_count,
                )
            except Exception as exc:  # audit is best-effort — do not bubble
                logger.warning(
                    "record_recovery_event_failed_in_recover_one",
                    source_id=source["id"],
                    database_name=database_name,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

        event_bus.emit(
            "recovery",
            action=f"Source recovered from '{source_status}' state",
            source="reconciler",
            reason=action_taken,
            database_name=database_name,
        )
        logger.warning(
            "source_recovered",
            source_id=source["id"],
            database_name=database_name,
            status=source_status,
            action=action_taken,
        )

    # Suppression rationale: dispatcher returns one of seven branches;
    # flattening the if-ladder into a mapping would obscure the per-status
    # preconditions.
    async def _classify(  # noqa: PLR0911
        self,
        source: dict[str, Any],
        database_name: str,
    ) -> dict[str, Any] | None:
        """Classify a source into a recovery action.

        Returns None if the source is healthy (no action needed) or
        a dict describing the dispatch operation otherwise. The
        action dict has one of three shapes:

        Single:
            ``{"queue": str, "operation": str, "data": dict,
               "priority": int}``

        Compound (several tasks enqueued back-to-back):
            ``{"compound": [<single>, <single>, ...]}``

        Commit (routes to ``_dispatch_commit`` via ``_recover_one`` so
        the recovery counter is bumped before any queue interaction —
        audit fix #H7):
            ``{"dispatch_kind": "commit", "operation": str,
               "commit_data": dict}``

        _classify NEVER dispatches directly. It is ``_recover_one``'s
        responsibility to increment the counter and then call the
        appropriate dispatcher based on the returned descriptor.

        Filled in incrementally across Tasks 15-18:
        - Task 15: ``pending`` and ``indexing`` → dispatch index_document
        - Task 16: ``indexed`` and ``extracted`` → dispatch next stage
        - Task 17: ``extracting`` → three sub-cases (no job, partial, stalled)
        - Task 18: ``committing`` → dispatch import_commit
        - vision_pending → compound dispatch OP_VISION_PAGE for each
          stalled PENDING vision_page_descriptions row; or dispatch
          OP_VISION_FINALIZE when the job is already terminal.
        """
        status = source.get("status")
        source_id = source["id"]

        if status in ("pending", "indexing"):
            return await self._classify_pending_or_indexing(
                source=source,
                source_id=source_id,
                database_name=database_name,
                status=status,
            )
        if status == "indexed":
            return await self._classify_indexed(
                source=source,
                source_id=source_id,
                database_name=database_name,
            )
        if status == "extracting":
            return await self._classify_extracting(
                source=source,
                source_id=source_id,
                database_name=database_name,
            )
        if status == "extracted":
            return await self._classify_extracted(
                source=source,
                source_id=source_id,
                database_name=database_name,
            )
        if status == "committing":
            return await self._classify_committing(
                source=source,
                source_id=source_id,
                database_name=database_name,
            )
        if status == "vision_pending":
            return await self._classify_vision_pending(
                source=source,
                source_id=source_id,
                database_name=database_name,
            )
        if status == "awaiting_confirmation":
            # EXPLICIT NO-OP — DO NOT auto-dispatch. A source parked at
            # awaiting_confirmation is waiting on a human to confirm/override
            # the auto-detected extraction domain (the confirmation gate,
            # 2026-05-28). Re-dispatching it would bypass the gate and burn an
            # extraction in a possibly-wrong domain. It is also deliberately
            # absent from NON_TERMINAL_STATUSES so the bulk scan never reaches
            # here; this branch exists so a future scan-widening can't silently
            # turn the implicit fall-through into auto-proceeding. Observability
            # for stuck parked rows lives in ``count_awaiting_confirmation``.
            logger.debug(
                "recovery_skip_awaiting_confirmation",
                source_id=source_id,
                database_name=database_name,
            )
            return None
        return None

    async def _classify_pending_or_indexing(
        self,
        *,
        source: dict[str, Any],
        source_id: str,
        database_name: str,
        status: str | None,
    ) -> dict[str, Any] | None:
        """Classify a source in ``pending`` or ``indexing`` status."""
        # --- pending / indexing ---------------------------------------
        # Both statuses need the same re-dispatch: INDEX_DOCUMENT on
        # the operations queue. ``pending`` means the upload landed
        # but no queue task ever fired; ``indexing`` means a worker
        # picked it up and crashed. Either way, the per-handler
        # idempotency from Task 5 ensures a re-dispatch resumes from
        # the first unembedded chunk instead of starting over.
        # If indexing already completed, the status just hasn't
        # transitioned yet — don't re-dispatch.
        if source.get("indexing_complete"):
            return None

        # Vision-handoff guard. PR 2's ``vision_finalizer`` performs
        # a CAS from VISION_PENDING -> INDEXING and then enqueues the
        # resume task. A worker crash between the CAS and the enqueue
        # leaves the source in INDEXING with a vision_job row but no
        # live queue task. Re-dispatching OP_INDEX_DOCUMENT here would
        # run the indexing handler from scratch — ``start_indexing``
        # resets timestamps, loader rollups double-count every quality
        # counter, and ``_apply_vision_processing`` creates a fresh
        # ``vision_jobs`` row, orphaning the old one. Route to
        # OP_VISION_FINALIZE instead; its idempotent status-check + CAS
        # handles every sub-case (VISION_PENDING -> finish the work;
        # INDEXING already -> re-emit the resume task; already
        # advanced -> skip cleanly).
        vision_job = self.adapter.get_vision_job_by_source(source_id)
        if vision_job is not None:
            logger.info(
                "recovery_indexing_with_vision_job_routing_to_finalize",
                source_id=source_id,
                job_id=vision_job["id"],
                job_completed=vision_job.get("completed"),
                job_failed=vision_job.get("failed"),
                job_total=vision_job.get("total_pages"),
            )
            return await self._build_vision_finalize_descriptor(
                source_id=source_id,
                job=vision_job,
                database_name=database_name,
            )

        if await self._queue_has_task_for(
            source_id=source_id,
            database_name=database_name,
            operations=(_OP_INDEX_DOCUMENT,),
        ):
            return None
        file_info = _build_file_info_from_source(source)
        if not file_info.get("filepath"):
            logger.warning(
                "recovery_skipped_missing_filepath",
                source_id=source_id,
                status=status,
            )
            return None
        return {
            "queue": _QUEUE_OPERATIONS,
            "operation": _OP_INDEX_DOCUMENT,
            "data": {
                "file_id": source_id,
                "file_info": file_info,
            },
            "priority": 0,
        }

    async def _classify_indexed(
        self,
        *,
        source: dict[str, Any],
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Classify a source in ``indexed`` status."""
        # --- indexed -------------------------------------------------
        # If the user chose NOT to auto-analyze, the source is healthy
        # in 'indexed' state — this is the explicit manual-control
        # case and the reconciler must not override it. If auto_analyze
        # is on, a previous indexing pass tried to queue analysis but
        # the worker crashed before the enqueue landed.
        # extraction_complete=True means extraction already finished;
        # the status field just hasn't been re-synced yet. Matches
        # the guard pattern on every other branch — skip dispatch
        # rather than re-run work that's already done.
        if source.get("extraction_complete"):
            return None
        if not source.get("auto_analyze", True):
            return None
        if await self._queue_has_task_for(
            source_id=source_id,
            database_name=database_name,
            operations=(_OP_IMPORT_ANALYSIS,),
        ):
            return None
        return {
            "queue": _QUEUE_OPERATIONS,
            "operation": _OP_IMPORT_ANALYSIS,
            "data": {
                "file_id": source_id,
                "file_info": _build_file_info_from_source(source),
                "analysis_depth": source.get("extraction_depth", "full"),
            },
            "priority": 0,
        }

    # Suppression rationale: three distinct sub-cases (no-job, all-terminal,
    # mid-flight) plus stall-age + Valkey in-flight filtering inherently
    # branch and return early at each guard.
    async def _classify_extracting(  # noqa: PLR0911, C901
        self,
        *,
        source: dict[str, Any],
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Classify a source in ``extracting`` status (three sub-cases)."""
        # --- extracting (three sub-cases) ----------------------------
        # 1. No active job row → analysis handler crashed before the
        #    create_extraction_job call committed. Re-dispatch
        #    import_analysis and let Task 6's load-or-create helper
        #    reuse the row if the crash happened just after create.
        # 2. All tasks are in a TERMINAL_TASK_STATES status (completed,
        #    failed, cancelled, orphaned) → chunks are done but
        #    finalize_extraction never fired.
        #    Enqueue FINALIZE_EXTRACTION on the LLM queue.
        # 3. Job is mid-flight with non-terminal tasks → compound
        #    dispatch: enqueue EXTRACT_CHUNK for each pending/failed
        #    task row. The chunk handler's Task 7 DB short-circuit
        #    makes re-dispatch safe.
        if source.get("extraction_complete"):
            return None
        job = self.adapter.get_active_extraction_job(
            source_id=source_id, database_name=database_name
        )
        if job is None:
            if await self._queue_has_task_for(
                source_id=source_id,
                database_name=database_name,
                operations=(_OP_IMPORT_ANALYSIS,),
            ):
                return None
            return {
                "queue": _QUEUE_OPERATIONS,
                "operation": _OP_IMPORT_ANALYSIS,
                "data": {
                    "file_id": source_id,
                    "file_info": _build_file_info_from_source(source),
                    "analysis_depth": source.get("extraction_depth", "full"),
                },
                "priority": 0,
            }

        total = job.get("total_chunks", 0) or 0

        # Gate: all tasks settled in a terminal state (completed, failed,
        # cancelled, orphaned) → finalize_extraction must be dispatched.
        # The old gate used ``completed + failed >= total`` which ignored
        # ``cancelled`` and ``orphaned`` — both legitimate terminal states
        # after Task 4 cascades and the existing fail_extraction_job path.
        # Use TERMINAL_TASK_STATES as the single source of truth.
        if total > 0:
            non_terminal_statuses = [
                s for s in ("pending", "queued", "running") if s not in TERMINAL_TASK_STATES
            ]
            non_terminal_tasks = self.adapter.list_extraction_tasks_by_status(
                job_id=job["id"],
                statuses=non_terminal_statuses,
                database_name=database_name,
            )
            if not non_terminal_tasks:
                if await self._queue_has_task_for(
                    source_id=source_id,
                    database_name=database_name,
                    operations=(_OP_FINALIZE_EXTRACTION,),
                ):
                    return None
                return {
                    "queue": _QUEUE_LLM,
                    "operation": _OP_FINALIZE_EXTRACTION,
                    "data": {
                        "source_id": source_id,
                        "job_id": job["id"],
                        "database_name": database_name,
                        "generate_embeddings": job.get("generate_embeddings", True),
                    },
                    "priority": 0,
                }

        # 'queued' is included alongside 'pending'/'failed': a chunk handler
        # that returned {"skipped": "paused"} acks the Valkey task without
        # transitioning the row off 'queued', so the row is orphaned —
        # there is no live queue task and no later state will move it.
        # 'running' is included to catch zombie chunks left behind by a
        # worker crash / container rebuild mid-extraction: the row was
        # claimed (status='running', started_at set) but the Valkey task
        # is gone and no terminal transition will ever fire. Without this,
        # the source stalls in 'extracting' forever because the FINALIZE
        # gate above sees a non-terminal task and refuses to dispatch.
        # The bulk reconciler and manual-resume both need to recover these.
        pending_tasks = self.adapter.list_extraction_tasks_by_status(
            job_id=job["id"],
            statuses=["pending", "queued", "failed", "running"],
            database_name=database_name,
        )
        # Age-filter the 'queued' and 'running' subsets: a chunk whose
        # claim/queue timestamp is fresher than the stall threshold is
        # treated as in-flight, not orphan. This is the DB-side fallback
        # for cases the Valkey in-flight check (Slice 2) cannot see —
        # e.g., Valkey eviction, paused-skip orphans, or running zombies
        # whose worker died after claiming. Pending and failed chunks
        # bypass the filter because they're not in any in-flight ambiguity.
        stall_cutoff = datetime.now(UTC) - timedelta(seconds=self.stalled_threshold_seconds)

        def _is_recoverable_chunk(t: dict[str, Any]) -> bool:
            """Return True when a chunk's claim/queue timestamp is older than the stall cutoff."""
            status = t.get("status")
            if status == "queued":
                ts = t.get("queued_at")
            elif status == "running":
                # 'started_at' is set when the worker claims the task; fall
                # back to 'queued_at' if for some reason started_at is null.
                ts = t.get("started_at") or t.get("queued_at")
            else:
                # pending / failed: always recoverable, no age check.
                return True
            if ts is None:
                # No timestamp → can't tell freshness. Default to
                # recoverable so paused-skip orphans (which may not
                # bump queued_at) still get rescued.
                return True
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    return True
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return ts < stall_cutoff

        pending_tasks = [t for t in pending_tasks if _is_recoverable_chunk(t)]
        if not pending_tasks:
            # No non-terminal tasks but counters are not yet
            # terminal — probably a transient DB-flush ordering
            # artifact. Treat as healthy; next reconcile pass
            # will re-check.
            return None

        # Filter out chunks that already have a live Valkey queue task —
        # those are in-flight, not stalled, and re-dispatching them would
        # be a no-op that still bumps recovery_attempts on the source.
        # Older queue clients without the helper degrade to legacy behavior
        # (dispatch all pending tasks) so this filter never breaks
        # recovery.
        in_flight_method = getattr(self.queue_client, "in_flight_chunk_task_ids", None)
        if in_flight_method is not None:
            try:
                in_flight: set[str] = await in_flight_method(
                    source_id=source_id,
                    database_name=database_name,
                )
            except Exception as exc:
                logger.warning(
                    "in_flight_chunk_check_failed_assuming_none",
                    source_id=source_id,
                    database_name=database_name,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                in_flight = set()
            if in_flight:
                pending_tasks = [t for t in pending_tasks if t["id"] not in in_flight]
                if not pending_tasks:
                    # Every pending chunk is already in flight on Valkey.
                    # Source is healthy; the worker just hasn't claimed
                    # the tasks yet. Returning None here means
                    # _recover_one counts this as skipped_healthy and does
                    # not bump recovery_attempts.
                    return None

        return {
            "compound": [
                {
                    "queue": _QUEUE_LLM,
                    "operation": _OP_EXTRACT_CHUNK,
                    "data": {
                        "chunk_task_id": t["id"],
                        "job_id": job["id"],
                        "database_name": database_name,
                        "chunk_content": "",
                        "chunk_index": t.get("chunk_index", 0),
                        "hierarchical_group_id": t.get("hierarchical_group_id"),
                        "small_chunk_ids": t.get("small_chunk_ids"),
                    },
                    "priority": 0,
                }
                for t in pending_tasks
            ],
        }

    async def _classify_extracted(
        self,
        *,
        source: dict[str, Any],
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Classify a source in ``extracted`` status."""
        # --- extracted -----------------------------------------------
        # Extracted means "all chunks processed, ready for commit" —
        # commit is the deterministic finalization step, so if the
        # source is sitting in extracted with no queue task, we
        # auto-dispatch commit to drive it to the terminal state.
        # commit_complete=True means the commit already ran to
        # completion; the status field just hasn't been re-synced yet.
        # Matches the guard on the ``committing`` branch below and
        # prevents the recovery loop where the commit fast-path keeps
        # short-circuiting without updating the status.
        if source.get("commit_complete"):
            return None
        if await self._queue_has_task_for(
            source_id=source_id,
            database_name=database_name,
            operations=(_OP_IMPORT_COMMIT,),
        ):
            return None
        # Rebuild the commit-data dict from the persisted commit_payload
        # (set by the extraction finalizer's ``_queue_commit_phase``)
        # when present, otherwise fall back to the per-source entity /
        # relationship tables. ``list_sources_by_statuses`` deliberately
        # omits the heavy commit_payload column so we re-fetch on
        # demand for the commit-dispatch path.
        commit_data = self._load_commit_data(source_id=source_id, database_name=database_name)
        # Return a descriptor instead of dispatching here — _recover_one
        # must increment the counter BEFORE any queue interaction
        # (audit fix #H7). dispatch_kind="commit" tells _recover_one to
        # call _dispatch_commit after the counter bump.
        return {
            "dispatch_kind": "commit",
            "operation": _OP_IMPORT_COMMIT,
            "commit_data": commit_data,
        }

    async def _classify_committing(
        self,
        *,
        source: dict[str, Any],
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Classify a source in ``committing`` status."""
        # --- committing ----------------------------------------------
        # A source stuck in 'committing' means the commit handler
        # picked it up but crashed before marking commit_complete.
        # Re-dispatch import_commit — BUT only when the commit
        # genuinely didn't finish. The commit_complete guard prevents
        # false positives: if the commit already ran to completion but
        # the status hasn't transitioned yet (race between the graph
        # session and source session), don't re-dispatch.
        #
        # Note: the empty-entities guard has been removed. commit() now
        # detects commit_complete=True and skips (Cluster A safety), while
        # empty-entity payloads route to _commit_empty instead of deleting
        # prior graph data.
        if source.get("commit_complete"):
            return None
        if await self._queue_has_task_for(
            source_id=source_id,
            database_name=database_name,
            operations=(_OP_IMPORT_COMMIT,),
        ):
            return None
        # Same on-demand load as the extracted branch — the
        # bulk-scan listing skips the heavy commit_payload column.
        commit_data = self._load_commit_data(source_id=source_id, database_name=database_name)
        # Return a descriptor instead of dispatching here — _recover_one
        # must increment the counter BEFORE any queue interaction
        # (audit fix #H7). dispatch_kind="commit" tells _recover_one to
        # call _dispatch_commit after the counter bump.
        return {
            "dispatch_kind": "commit",
            "operation": _OP_IMPORT_COMMIT,
            "commit_data": commit_data,
        }

    async def _classify_vision_pending(
        self,
        *,
        source: dict[str, Any],
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Classify a source in ``vision_pending`` status."""
        # --- vision_pending -------------------------------------------
        # A source in vision_pending has a vision_job and N
        # vision_page_descriptions rows. Recovery handles two sub-cases:
        #
        # 1. Job is already terminal (completed + failed >= total_pages):
        #    the per-page handlers all finished but the finalizer never
        #    fired. Re-enqueue OP_VISION_FINALIZE on QUEUE_OPERATIONS.
        #
        # 2. Job is still mid-flight: some PENDING rows never got a
        #    queue task (worker crashed before the enqueue loop finished,
        #    or Valkey evicted the tasks). Compound-dispatch OP_VISION_PAGE
        #    for each PENDING row. The ``WHERE status='pending'`` guard in
        #    ``update_vision_page_description`` makes double-enqueue
        #    correctness-safe — a re-delivered task that loses the race
        #    simply skips with rows=0 and increments no counters.
        job = self.adapter.get_vision_job_by_source(source_id)
        if job is None:
            # No job row yet — the indexing handler crashed before it
            # committed the vision_jobs row. Nothing to recover; the
            # indexing_handler recovery branch above (pending/indexing)
            # will re-dispatch OP_INDEX_DOCUMENT to start over.
            logger.warning(
                "vision_pending_recovery_skipped_no_job",
                source_id=source_id,
            )
            return None

        total = job.get("total_pages", 0) or 0
        completed = job.get("completed", 0) or 0
        failed = job.get("failed", 0) or 0
        counter_terminal = total > 0 and completed + failed >= total

        # Counter-vs-row-states reconciliation. ``_handle_vision_page``
        # commits the page-row UPDATE and the ``vision_jobs`` counter
        # increment in two transactions with an ``await`` between
        # them. A worker crash between commits leaves the counter
        # stale: queue re-delivery sees a non-PENDING row and skips
        # (``skipped_stale``), so the counter never catches up. The
        # mid-flight sub-branch below would then find zero PENDING
        # rows and return None, stalling the source forever.
        #
        # Re-derive the actual outcome from the row states. When
        # rows say terminal but the counter does not, dispatch
        # OP_VISION_FINALIZE just like the counter-terminal branch.
        if total > 0 and not counter_terminal:
            rows = self.adapter.list_vision_page_descriptions(source_id)
            actual_completed = sum(
                1
                for r in rows
                if r["status"]
                in (
                    VisionPageStatus.SUCCEEDED.value,
                    VisionPageStatus.TRUNCATED.value,
                )
            )
            actual_failed = sum(1 for r in rows if r["status"] == VisionPageStatus.FAILED.value)
            if actual_completed + actual_failed >= total:
                logger.info(
                    "recovery_vision_pending_counter_stale_reconciling",
                    source_id=source_id,
                    counter_completed=completed,
                    counter_failed=failed,
                    actual_completed=actual_completed,
                    actual_failed=actual_failed,
                    total=total,
                )
                return await self._build_vision_finalize_descriptor(
                    source_id=source_id,
                    job=job,
                    database_name=database_name,
                )

        if counter_terminal:
            # All pages settled — finalize must have been missed.
            return await self._build_vision_finalize_descriptor(
                source_id=source_id,
                job=job,
                database_name=database_name,
            )

        # Mid-flight: re-enqueue PENDING page rows as a compound dispatch.
        pending_pages = self.adapter.list_vision_page_descriptions(
            source_id, statuses=[VisionPageStatus.PENDING]
        )
        if not pending_pages:
            # No pending rows but counters haven't reached terminal —
            # probably a transient ordering artifact. Treat as healthy.
            return None

        return {
            "compound": [
                {
                    "queue": _QUEUE_LLM,
                    "operation": _OP_VISION_PAGE,
                    "data": {
                        "page_id": row["id"],
                        "job_id": job["id"],
                        "source_id": source_id,
                    },
                    "priority": 0,
                }
                for row in pending_pages
            ],
        }

    async def _build_vision_finalize_descriptor(
        self,
        *,
        source_id: str,
        job: dict[str, Any],
        database_name: str,
    ) -> dict[str, Any] | None:
        """Build the OP_VISION_FINALIZE dispatch descriptor (debounced).

        Used by both the counter-terminal sub-branch and the
        counter-vs-row-states reconciliation pre-pass. Returns the
        single-dispatch action dict or ``None`` if a finalize task is
        already live on the queue for this source.
        """
        if await self._queue_has_task_for(
            source_id=source_id,
            database_name=database_name,
            operations=(_OP_VISION_FINALIZE,),
        ):
            return None
        return {
            "queue": _QUEUE_OPERATIONS,
            "operation": _OP_VISION_FINALIZE,
            "data": {
                "source_id": source_id,
                "job_id": job["id"],
                "database_name": database_name,
            },
            "priority": 0,
        }

    def _load_commit_data(self, *, source_id: str, database_name: str) -> dict[str, Any]:
        """Load the commit-data dict for the commit-dispatch path.

        Prefers the persisted ``commit_payload`` (set by the extraction
        finalizer's ``_queue_commit_phase`` and carrying the canonical
        commit input — entities, relationships, suggested templates,
        inverse relationships) and falls back to rebuilding a minimal
        dict from the per-source entity / relationship tables when the
        payload is missing (e.g. partial-failure mid-reset that wiped
        it).

        Returns an empty dict when neither source is available; the
        commit handler routes empty dicts through ``_commit_empty``
        (zero-graph commit) rather than skipping, so callers no longer
        guard on this.
        """
        try:
            payload = self.adapter.get_source_commit_payload(
                source_id=source_id,
                database_name=database_name,
            )
        except Exception as exc:
            logger.warning(
                "commit_payload_load_failed",
                source_id=source_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            payload = None
        if payload:
            return payload

        try:
            entities = self.adapter.list_source_entities(
                source_id=source_id, database_name=database_name
            )
            relationships = self.adapter.list_source_relationships(
                source_id=source_id, database_name=database_name
            )
        except Exception as exc:
            logger.warning(
                "source_extraction_rows_load_failed",
                source_id=source_id,
                database_name=database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return {}

        if not entities and not relationships:
            return {}
        return {
            "entities": entities,
            "relationships": relationships,
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "inverse_relationships": {},
        }

    async def _queue_has_task_for(
        self,
        *,
        source_id: str,
        database_name: str,
        operations: tuple[str, ...],
    ) -> bool:
        """Check whether a queue task for this source is already in flight.

        Used by ``_classify`` to debounce — if the queue still holds a
        task for this source, recovery doesn't need to re-enqueue it.
        Returns True on any queue client error so recovery skips the
        source rather than risking a duplicate dispatch. The next
        scan (60s later) will retry when the queue is available.
        """
        try:
            exists: bool = await self.queue_client.task_exists_for_source(
                source_id=source_id,
                database_name=database_name,
                operations=list(operations),
            )
            return exists
        except Exception as exc:
            logger.warning(
                "queue_check_failed_assuming_in_flight",
                source_id=source_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return True

    async def _dispatch(
        self,
        action: dict[str, Any],
        source: dict[str, Any],
        database_name: str,
    ) -> None:
        """Enqueue task(s) for the recovery action.

        Handles both the single-task and compound-task shapes returned
        by ``_classify``. Metadata is filled in from the source row so
        the queue can associate the recovered work with the right
        source/database context for status tracking.
        """
        metadata = {
            "source_id": source["id"],
            "database_name": database_name,
        }
        if "compound" in action:
            for sub in action["compound"]:
                await self.queue_client.enqueue(
                    queue=sub["queue"],
                    operation=sub["operation"],
                    data=sub["data"],
                    metadata=metadata,
                    priority=sub.get("priority", 0),
                )
            return
        await self.queue_client.enqueue(
            queue=action["queue"],
            operation=action["operation"],
            data=action["data"],
            metadata=metadata,
            priority=action.get("priority", 0),
        )

    async def _dispatch_commit(
        self,
        *,
        source: dict[str, Any],
        database_name: str,
        commit_data: dict[str, Any],
    ) -> None:
        """Dispatch IMPORT_COMMIT for a recovery action.

        Routes through ``queue_utils.queue_import_commit`` (the canonical
        enqueue path) so ``adapter.set_source_commit_payload`` runs before
        the queue message lands AND the task metadata carries both
        ``file_id`` and ``source_id`` keys — required by ``abort_processing``'s
        cancel-by-metadata path. Without this, the commit handler reads
        ``commit_payload`` from the row, finds it empty, and skips with
        ``commit_payload_not_found``; or worse, an abort on a recovery-
        enqueued commit task can't find it. Audit fix #C4.
        """
        file_info = _build_file_info_from_source(source)
        payload = {
            "entities": commit_data.get("entities", []),
            "relationships": commit_data.get("relationships", []),
            "suggested_templates": commit_data.get("suggested_templates", []),
            "suggested_edge_templates": commit_data.get("suggested_edge_templates", []),
            "inverse_relationships": commit_data.get("inverse_relationships", {}),
            "create_templates": True,
            "auto_enable": True,
        }
        await queue_utils.queue_import_commit(
            file_id=source["id"],
            commit_data=payload,
            file_info=file_info,
            adapter=self.adapter,  # type: ignore[arg-type]  # SourceRecoveryPorts structurally satisfies set_source_commit_payload; queue_import_commit only uses that one method
            database_name=database_name,
            priority=0,
            extra_metadata={"triggered_by": "recovery"},
        )
