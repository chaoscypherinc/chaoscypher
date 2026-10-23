# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared confirmation-gate primitives.

One "brain" so no surface (worker / CLI / MCP / API) re-implements the
domain-confirmation gating rule. All three primitives read or write only
persisted ``SourceRow`` state so a fresh dispatch, a recovery re-dispatch,
and a manual trigger all evaluate the gate identically.

- ``gate_decision``  — pure read over persisted fields → 'proceed' | 'park'.
- ``park_for_confirmation`` — single atomic SourceRow write parking the source.
- ``confirm_extraction`` — state-aware: CAS awaiting_confirmation → indexed then
  re-queue (parked), record the forced packs + extraction_confirmed_at without a
  status change (pre-gate), or reject (past-gate / already-confirmed / errored).

A source carries a list of packs (spec 2026-10-07 § 5): the proposal stores
``packs`` beside ``detected_domain`` (their first, or ``generic``), and a
confirm forces a list (``forced_packs``, with ``forced_domain`` written from
it by ``pack_columns``). ``[]`` forced is the floor alone, a choice; None is
"not chosen".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.queue_utils import queue_import_analysis
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    first_domain_name,
    forced_packs_of,
    pack_columns,
    packs_of_name,
    proposal_packs,
)


logger = structlog.get_logger(__name__)


def _claim_pre_gate_confirmation(adapter: Any, file_id: str, values: dict[str, Any]) -> bool:
    """Atomically claim the write-once pre-gate confirmation timestamp.

    A rowcount-checked ``UPDATE … WHERE extraction_confirmed_at IS NULL``
    — the SQL-level CAS for the pre-gate bucket. The old guard was
    check-then-write on the ORM row, so two concurrent confirms carrying
    different domains (cortex + MCP, separate sessions) could both
    observe NULL and both write — a silent lost update of the first
    caller's choice. With the claim, exactly one caller's UPDATE matches
    the NULL predicate; the loser falls through to the already-confirmed
    conflict. This is the timestamp-column analogue of
    ``transition_source_status`` (which can only CAS the status column).

    ``values`` (the forced packs + override columns) rides in the SAME
    statement and commit as the timestamp: a consumer that snapshots the
    row after the claim commit is visible must already see the user's
    domain choice — a second decision-commit would open a window where
    ``gate_decision`` proceeds on ``extraction_confirmed_at`` alone and
    extraction runs under the auto-detected domain.

    Args:
        adapter: SqliteAdapter (source repository).
        file_id: Source ID to claim.
        values: Decision columns to persist atomically with the claim.

    Returns:
        True when this caller claimed the timestamp; False when a rival
        already had (or the row vanished).
    """
    from sqlalchemy import update

    result = adapter.session.execute(
        update(SourceRow)
        .where(SourceRow.id == file_id)  # type: ignore[arg-type]
        .where(SourceRow.extraction_confirmed_at.is_(None))  # type: ignore[union-attr]
        .values(extraction_confirmed_at=datetime.now(UTC), **values)
    )
    adapter._maybe_commit()  # noqa: SLF001 - gate primitives compose the same session path as the adapter
    return getattr(result, "rowcount", 0) == 1


def proposal_from_detection(result: dict[str, Any]) -> dict[str, Any]:
    """Build the persisted detection_proposal blob from a detect_extraction_domain result.

    ``packs`` is the proposed list (``[]`` the floor alone);
    ``detected_domain`` stays as its first, or ``generic``, for readers of
    the one-name blob.
    """
    packs = proposal_packs(result) or []
    return {
        "ranking": result.get("ranking", []),
        "confidence": result.get("confidence"),
        "packs": packs,
        "detected_domain": result.get("detected_domain") or first_domain_name(packs),
        "low_confidence": result.get("low_confidence", False),
        "reason": result.get("reason"),
    }


def awaits_detection(source: dict[str, Any] | None) -> bool:
    """Whether a source is gate-eligible: it asked for confirmation and forces no packs."""
    return bool(source and source.get("confirmation_required") and forced_packs_of(source) is None)


# Statuses strictly past INDEXED in the lifecycle: a re-dispatch of a source
# already at/after these has either started or finished extraction, so the
# gate must short-circuit to proceed and never re-park it.
_PAST_INDEXED: frozenset[str] = frozenset(
    {
        SourceStatus.EXTRACTING,
        SourceStatus.MCP_EXTRACTING,
        SourceStatus.EXTRACTED,
        SourceStatus.COMMITTING,
        SourceStatus.COMMITTED,
    }
)

# Statuses BEFORE the analysis-stage gate runs: a wizard confirm can legitimately
# arrive while the source is still indexing/embedding (the user reviews the eager
# detection_proposal in parallel — wizard §3.2 "confirm-vs-gate race"). For these
# we record the human's domain decision WITHOUT flipping status or re-queueing;
# the analysis stage runs on its own and ``gate_decision`` then PROCEEDS because
# forced_domain + extraction_confirmed_at are set. VISION_PENDING is included: an
# image-only no-text doc parks the user on a "pick a domain" review before vision
# resumes it to INDEXED (wizard no-text short-circuit). ERROR is deliberately
# excluded — an errored source is not confirmable (→ ConflictError).
_PRE_GATE: frozenset[str] = frozenset(
    {
        SourceStatus.PENDING,
        SourceStatus.INDEXING,
        SourceStatus.VISION_PENDING,
        SourceStatus.INDEXED,
    }
)


def gate_decision(source: dict[str, Any], *, bypass: bool = False) -> str:
    """Decide whether extraction may proceed or must park for confirmation.

    Reads ONLY persisted fields from a ``get_source``-shaped dict so the
    worker, the recovery reconciler, and trigger_next_waiting all evaluate it
    identically. ``bypass`` is the per-request escape hatch for in-process
    callers (CLI ``--no-confirm``); the worker/recovery paths never pass it
    (the persisted ``confirmation_required`` column already encodes the
    upload-time ``auto_confirm`` choice).

    Args:
        source: Source dict (forced_packs / forced_domain, confirmation_required,
            extraction_confirmed_at, status).
        bypass: Live per-request bypass; True => proceed unconditionally.

    Returns:
        ``"proceed"`` or ``"park"``.
    """
    if bypass:
        return "proceed"
    # Already confirmed (write-once) or already past the gate => never re-park.
    if source.get("extraction_confirmed_at"):
        return "proceed"
    if source.get("status") in _PAST_INDEXED:
        return "proceed"
    # Forced packs (the floor alone included) are an explicit human choice => proceed.
    if forced_packs_of(source) is not None:
        return "proceed"
    # Auto domain: park only when the upload requested confirmation.
    if source.get("confirmation_required"):
        return "park"
    return "proceed"


def park_for_confirmation(
    adapter: Any,
    file_id: str,
    proposal: dict[str, Any],
) -> None:
    """Park a source awaiting human domain confirmation — one atomic write.

    Flips ``status`` to ``AWAITING_CONFIRMATION`` and persists the detection
    ``proposal`` blob and ``confirmation_required`` on the same SourceRow
    instance, committed in a single UPDATE. Modeled on
    ``mark_extraction_waiting`` (source_files_indexing.py:999-1040): SQLite
    commits the row atomically, so a crash mid-commit rolls the whole row
    back (recovery then re-detects + re-parks, idempotently).

    No slot is claimed and ``extraction_started_at`` is never touched.

    Args:
        adapter: SqliteAdapter (source repository).
        file_id: Source ID to park.
        proposal: Detection proposal blob
            {ranking, confidence, detected_domain, low_confidence}.
    """
    from sqlmodel import select

    adapter._ensure_connected()  # noqa: SLF001 - gate primitives compose the same session path as the adapter
    row = adapter.session.exec(select(SourceRow).where(SourceRow.id == file_id)).first()
    if row is None:
        logger.warning("park_for_confirmation_source_not_found", source_id=file_id)
        return

    row.status = SourceStatus.AWAITING_CONFIRMATION
    row.detection_proposal = proposal
    row.confirmation_required = True
    adapter.session.add(row)
    adapter._maybe_commit()  # noqa: SLF001 - gate primitives compose the same session path as the adapter

    logger.info(
        "source_parked_for_confirmation",
        source_id=file_id,
        detected_domain=proposal.get("detected_domain"),
        low_confidence=proposal.get("low_confidence"),
    )


def write_detection_proposal(
    adapter: Any,
    file_id: str,
    proposal: dict[str, Any],
) -> None:
    """Persist the detection proposal onto the SourceRow WITHOUT changing status.

    Called by the indexing handler immediately after ``store_chunks`` for
    gate-eligible sources (wizard §3.1 eager-detection step). Status stays
    ``INDEXING`` so embedding proceeds normally; the proposal is available
    for the wizard's poll-until-populated frontend predicate.

    Idempotent: a retry/re-dispatch of ``handle_index_document`` re-chunks
    (already idempotent) and re-writes the same proposal — harmless.

    Args:
        adapter: SqliteAdapter (source repository).
        file_id: Source ID to update.
        proposal: Detection proposal blob
            {ranking, confidence, detected_domain, low_confidence}.
    """
    from sqlmodel import select

    adapter._ensure_connected()  # noqa: SLF001 - gate primitives compose the same session path as the adapter
    row = adapter.session.exec(select(SourceRow).where(SourceRow.id == file_id)).first()
    if row is None:
        logger.warning("write_detection_proposal_source_not_found", source_id=file_id)
        return

    row.detection_proposal = proposal
    adapter.session.add(row)
    adapter._maybe_commit()  # noqa: SLF001 - gate primitives compose the same session path as the adapter

    logger.info(
        "detection_proposal_written_eagerly",
        source_id=file_id,
        detected_domain=proposal.get("detected_domain"),
        low_confidence=proposal.get("low_confidence"),
    )


# Canonical override columns (mirror TriggerExtractionRequest fields).
_OVERRIDE_COLUMNS: tuple[str, ...] = (
    "filtering_mode",
    "content_filtering",
    "enable_direction_correction",
    "protect_orphans",
    "enable_inverse_relationships",
    "max_entity_degree_override",
)


def _decision_columns(row: SourceRow, chosen_packs: Sequence[str] | str | None) -> dict[str, Any]:
    """The forced-pack columns of a confirm: the chosen packs, else the proposal's.

    A string is the one-name form (a pack's name or the packs' label), never
    a sequence of one-letter names.
    """
    if isinstance(chosen_packs, str):
        chosen_packs = packs_of_name(chosen_packs)
    packs = (
        list(chosen_packs) if chosen_packs is not None else proposal_packs(row.detection_proposal)
    )
    return pack_columns({"forced_packs": packs})


def _apply_decision(
    row: SourceRow,
    chosen_packs: Sequence[str] | str | None,
    overrides: dict[str, Any],
) -> list[str] | None:
    """Persist the human's pack choice + non-None overrides onto the row.

    Used by the parked (CAS-win) confirm branch; the pre-gate branch writes
    the same decision inside its atomic claim UPDATE instead. Sets
    ``forced_packs`` (chosen, or the proposal's packs as fallback) and the old
    ``forced_domain`` from it, applies only present non-None overrides, and
    stamps ``extraction_confirmed_at`` write-once. Does NOT change ``status``
    and does NOT commit — the caller owns the commit + (for the parked branch
    only) the re-queue.

    Returns the resolved forced packs.
    """
    columns = _decision_columns(row, chosen_packs)
    row.forced_packs = columns["forced_packs"]
    row.forced_domain = columns["forced_domain"]

    for key in _OVERRIDE_COLUMNS:
        # A None value means "leave the column as-is / use the persisted
        # upload-time value": skip it so a default confirm cannot NULL a NOT
        # NULL column (filtering_mode/content_filtering) or silently clobber an
        # upload-time choice. Callers supplying an explicit override pass a
        # non-None value (the present-keys-only Cortex/MCP pattern drops Nones
        # entirely; this guard also defends in-process callers).
        if key in overrides and overrides[key] is not None:
            setattr(row, key, overrides[key])

    # Write-once: never re-stamp a source that was already confirmed.
    if row.extraction_confirmed_at is None:
        row.extraction_confirmed_at = datetime.now(UTC)

    forced: list[str] | None = columns["forced_packs"]
    return forced


async def confirm_extraction(
    adapter: Any,
    file_id: str,
    chosen_packs: Sequence[str] | str | None,
    overrides: dict[str, Any],
) -> bool:
    """Record a confirm decision, branching on where the source is in the pipeline.

    State-aware (wizard §3.2 "confirm-vs-gate race"): in the wizard the user
    reviews the eager ``detection_proposal`` WHILE embedding runs, so a confirm
    can land either before OR after the analysis-stage gate parks the source.
    Three buckets:

    - **``AWAITING_CONFIRMATION``** (gate already parked it): CAS
      ``awaiting_confirmation → indexed``, persist the forced packs + overrides
      + write-once ``extraction_confirmed_at``, then ``queue_import_analysis``
      (queue_utils.py:109) AFTER the status is INDEXED so the busy-slot waiting
      requeue (``get_oldest_waiting_extraction``, source_files_indexing.py:1048,
      matches only INDEXED) can find it. A lost CAS (concurrent confirm) is a
      benign no-op → False.
    - **Pre-gate** (``pending`` / ``indexing`` / ``vision_pending`` / ``indexed``,
      not yet confirmed): record the decision WITHOUT changing status and WITHOUT
      re-queueing. The analysis stage runs on its own; ``gate_decision`` then
      PROCEEDS because the forced packs + extraction_confirmed_at are now set (no
      park). → True.
    - **Past the gate** (``extracting`` / ``mcp_extracting`` / ``extracted`` /
      ``committing`` / ``committed``), already confirmed, errored, or missing:
      too late to change the domain → ``ConflictError`` (HTTP 409).

    Args:
        adapter: SqliteAdapter (source repository).
        file_id: Source ID to confirm.
        chosen_packs: The packs to force (the confirmed/overridden choice;
            ``[]`` is the floor alone). When None, the proposal's packs are
            the forced choice. A string is the one-name form (a pack's
            name, ``generic`` for the floor alone). A surface reads its
            ``packs`` / ``domain`` field with ``requested_packs``.
        overrides: Extraction-option overrides mirroring TriggerExtractionRequest
            (analysis_depth, filtering_mode, content_filtering,
            enable_direction_correction, protect_orphans,
            enable_inverse_relationships, max_entity_degree_override).

    Returns:
        True on a recorded decision (parked CAS-win or pre-gate); False only on
        a lost CAS for the parked branch.

    Raises:
        ConflictError: Source is past the gate, already confirmed, errored, or
            missing — too late to change the domain.
    """
    from sqlmodel import select

    from chaoscypher_core.exceptions import ConflictError

    database_name = getattr(adapter, "database_name", "")

    adapter._ensure_connected()  # noqa: SLF001 - gate primitives compose the same session path as the adapter
    row = adapter.session.exec(select(SourceRow).where(SourceRow.id == file_id)).first()
    if row is None:
        logger.warning("confirm_extraction_source_not_found", source_id=file_id)
        msg = f"Source '{file_id}' not found; cannot confirm"
        raise ConflictError(msg)

    status = row.status

    # --- Bucket 1: parked (gate ran first) — CAS + re-queue (existing path). ---
    if status == SourceStatus.AWAITING_CONFIRMATION:
        # CAS: only one caller flips awaiting_confirmation -> indexed.
        won = adapter.transition_source_status(
            file_id,
            SourceStatus.AWAITING_CONFIRMATION,
            SourceStatus.INDEXED,
            database_name=database_name,
        )
        if not won:
            logger.info("confirm_extraction_lost_cas", source_id=file_id)
            return False

        # Re-read on the live session so the override write commits the CAS'd row.
        row = adapter.session.exec(select(SourceRow).where(SourceRow.id == file_id)).first()
        if row is None:  # pragma: no cover — CAS just matched this row
            logger.warning("confirm_extraction_row_vanished", source_id=file_id)
            return False

        forced = _apply_decision(row, chosen_packs, overrides)
        adapter.session.add(row)
        adapter._maybe_commit()  # noqa: SLF001 - gate primitives compose the same session path as the adapter

        analysis_depth = overrides.get("analysis_depth") or row.extraction_depth or "full"
        file_info: dict[str, Any] = {
            "filename": row.filename,
            "filepath": row.filepath,
            **pack_columns({"forced_packs": forced}),
            "metadata": {},
        }
        await queue_import_analysis(
            file_id,
            file_info,
            analysis_depth,
            database_name=database_name,
        )
        logger.info(
            "source_confirmed_for_extraction",
            source_id=file_id,
            forced_packs=forced,
            analysis_depth=analysis_depth,
        )
        return True

    # --- Bucket 2: pre-gate — record decision, no status change, no re-queue. ---
    # The analysis stage runs on its own; gate_decision then PROCEEDS because the
    # forced/confirmed fields are set. Write-once is enforced by an atomic SQL
    # claim on the timestamp column (see _claim_pre_gate_confirmation) — the ORM
    # row in hand may be a stale read when two confirms race, so it must never
    # be the guard. The decision (forced packs + non-None overrides) rides in
    # the SAME claim statement: gate_decision short-circuits on the timestamp
    # alone, so a two-commit write let an OP_IMPORT_ANALYSIS snapshot between
    # the commits extract under the auto-detected domain instead of the user's.
    # A lost claim falls through to bucket 3 (already confirmed).
    if status in _PRE_GATE and row.extraction_confirmed_at is None:
        claim_values: dict[str, Any] = _decision_columns(row, chosen_packs)
        for key in _OVERRIDE_COLUMNS:
            # None means "leave the column as-is" — same guard as _apply_decision.
            if key in overrides and overrides[key] is not None:
                claim_values[key] = overrides[key]
        if _claim_pre_gate_confirmation(adapter, file_id, claim_values):
            # The raw UPDATE bypassed the ORM identity map; drop stale state.
            adapter.session.expire_all()
            logger.info(
                "source_confirmed_pre_gate",
                source_id=file_id,
                status=status,
                forced_packs=claim_values["forced_packs"],
            )
            return True
        logger.info("confirm_extraction_lost_pre_gate_claim", source_id=file_id)

    # --- Bucket 3: past the gate / already confirmed / errored — too late. ---
    logger.info(
        "confirm_extraction_too_late",
        source_id=file_id,
        status=status,
        already_confirmed=row.extraction_confirmed_at is not None,
    )
    msg = (
        f"Source '{file_id}' status is '{status}'"
        f"{' and already confirmed' if row.extraction_confirmed_at is not None else ''}; "
        "too late to change the extraction packs"
    )
    raise ConflictError(msg)
