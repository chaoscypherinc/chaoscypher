# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Public 5-phase SourceProgress model.

Maps the internal SourceStatus pipeline into 5 user-facing phases plus
an ``is_searchable`` flag for the UI to use without exposing internal
state-machine complexity.

Chosen mapping rationale
------------------------

    waiting_to_index  — pending, error
        Nothing useful exists yet; the source is either queued or has
        failed and needs user attention before it can proceed.

    indexing          — indexing, vision_pending
        Chunking and embedding are in progress; not yet searchable.
        ``vision_pending`` is the transient sub-state where the indexer
        has paused to let per-page vision captions finish; from the
        user's perspective it's still "indexing."

    awaiting_input    — awaiting_confirmation
        Indexing is done and the source is searchable, but the
        auto-detected extraction domain needs human verification before
        the expensive extraction runs. The UI renders an actionable
        "Confirm domain" chip here, not a spinning progress bar.

    extracting        — indexed, extracting, mcp_extracting, extracted, committing
        The source is searchable (RAG) from ``indexed`` onward; this
        phase covers the entire extraction arc including the automatic
        commit. From a user perspective, "things are happening / just
        wait" is the right mental model for all of these.

    ready             — committed
        The source is fully integrated into the knowledge graph.

``is_searchable`` is True for any status where the source can be used
for RAG search (all statuses >= indexed except error):
    {indexed, awaiting_confirmation, extracting, mcp_extracting, extracted, committing, committed}

The mapping is enum-keyed and an import-time assertion guarantees every
SourceStatus member has a phase. Adding a new status to the canonical
enum forces a mapping decision instead of silently falling back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from chaoscypher_core.models import SourceStatus


Phase = Literal["waiting_to_index", "indexing", "awaiting_input", "extracting", "ready"]
"""Five user-facing phases that collapse the internal SourceStatus pipeline."""

_SEARCHABLE_INTERNAL: frozenset[SourceStatus] = frozenset(
    {
        SourceStatus.INDEXED,
        SourceStatus.AWAITING_CONFIRMATION,
        SourceStatus.EXTRACTING,
        SourceStatus.MCP_EXTRACTING,
        SourceStatus.EXTRACTED,
        SourceStatus.COMMITTING,
        SourceStatus.COMMITTED,
    }
)

_STATUS_TO_PHASE: dict[SourceStatus, Phase] = {
    SourceStatus.PENDING: "waiting_to_index",
    SourceStatus.INDEXING: "indexing",
    SourceStatus.VISION_PENDING: "indexing",
    SourceStatus.INDEXED: "extracting",
    SourceStatus.AWAITING_CONFIRMATION: "awaiting_input",
    SourceStatus.EXTRACTING: "extracting",
    SourceStatus.MCP_EXTRACTING: "extracting",
    SourceStatus.EXTRACTED: "extracting",
    SourceStatus.COMMITTING: "extracting",
    SourceStatus.COMMITTED: "ready",
    SourceStatus.ERROR: "waiting_to_index",
}

# Exhaustiveness guard: any new SourceStatus member must get an explicit
# phase here. Failing at import time is the whole point — a silent
# fallback is how vision_pending shipped without UI support in the first
# place.
_missing = set(SourceStatus) - set(_STATUS_TO_PHASE)
if _missing:  # pragma: no cover - enforced at import time
    msg = (
        f"SourceStatus members missing from _STATUS_TO_PHASE: {sorted(s.value for s in _missing)}. "
        f"Every status must have an explicit phase mapping."
    )
    raise RuntimeError(msg)


class SourceProgress(BaseModel):
    """Public 5-phase progress model surfaced on ``SourceResponse``.

    The ``phase`` field uses plain English phases that the UI can display
    directly without needing to understand the internal SourceStatus pipeline.

    Attributes:
        phase: One of ``waiting_to_index`` | ``indexing`` | ``awaiting_input`` |
            ``extracting`` | ``ready``.
        is_searchable: ``True`` when the source can be queried via RAG search
            (semantic similarity, full-text search). Becomes ``True`` once
            indexing completes (``indexed`` status) and remains ``True``
            through the extraction and commit stages.
    """

    phase: Phase
    is_searchable: bool


def map_status_to_progress(status: str) -> SourceProgress:
    """Derive a :class:`SourceProgress` from an internal SourceStatus string.

    Args:
        status: Internal source status string (e.g. ``"indexed"``,
            ``"mcp_extracting"``). Must be a valid :class:`SourceStatus`
            value.

    Returns:
        A :class:`SourceProgress` with the matching public phase and
        searchability flag.

    Raises:
        ValueError: ``status`` is not a known :class:`SourceStatus` value.
            Raised loudly rather than falling back to a default phase so
            unknown values surface as bugs at the call site instead of
            silently mis-labelling a source as ``waiting_to_index``.
    """
    enum_status = SourceStatus(status)
    return SourceProgress(
        phase=_STATUS_TO_PHASE[enum_status],
        is_searchable=enum_status in _SEARCHABLE_INTERNAL,
    )


__all__ = ["Phase", "SourceProgress", "map_status_to_progress"]
