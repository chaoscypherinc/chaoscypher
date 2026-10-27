# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The relink: link the memories not linked yet, oldest first (spec 2026-10-24 § 4.3, D21).

Two callers run :func:`relink_pending`: a write-mode server, once, in the background after
the MCP handshake (``MemoryLinker.start_sweep``, unless ``mcp.memory.relink_on_start`` is
off), and ``chaoscypher memory relink`` in the foreground. Each memory goes through
:func:`~chaoscypher_core.mcp.memory_link.link_memory`, one at a time, so its claim keeps
two sweeps from linking the same memory twice.

The start-up sweep honours each failed memory's backoff (:func:`backoff_seconds`) and does
nothing at all while no extraction model is configured (a ``no_model`` memory is never an
attempt); the command ignores the backoff. A ``linked_empty`` memory is retried only on
request (``include_empty``). The report holds counts only, never a memory's text.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.mcp.memory_link import (
    BACKOFF_CAP_SECONDS,
    LinkOutcome,
    LinkView,
    extraction_model_configured,
    link_memory,
    link_repository,
    link_view,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos.memory_link import LinkRow
    from chaoscypher_core.bootstrap import Engine

logger = structlog.get_logger(__name__)

BACKOFF_BASE_SECONDS = 60
"""The backoff's unit: a memory that failed ``n`` times waits ``2**n`` of these."""

_LINKED_VIEWS = (LinkView.LINKED, LinkView.LINKED_EMPTY)


def backoff_seconds(attempts: int) -> float:
    """How long a memory whose link failed ``attempts`` times waits before the sweep tries again."""
    return float(min(BACKOFF_BASE_SECONDS * 2**attempts, BACKOFF_CAP_SECONDS))


def due(
    row: LinkRow, view: LinkView, now: datetime, *, honour_backoff: bool, include_empty: bool
) -> bool:
    """Whether a sweep links ``row`` now.

    Pending, no-model and interrupted memories always; a failed one once its backoff has
    passed (or always, when the backoff is not honoured); a ``linked_empty`` one only with
    ``include_empty``; a live ``linking`` or a ``linked`` one never.
    """
    if view in (LinkView.PENDING, LinkView.NO_MODEL, LinkView.INTERRUPTED):
        return True
    if view is LinkView.FAILED:
        if not honour_backoff or row.last_activity_at is None:
            return True
        waited = (now - row.last_activity_at).total_seconds()
        return waited >= backoff_seconds(row.recovery_attempts)
    return view is LinkView.LINKED_EMPTY and include_empty


@dataclass
class RelinkReport:
    """What one sweep did, in counts: never a memory's text."""

    linked: int = 0
    linked_empty: int = 0
    failed: int = 0
    no_model: int = 0
    elsewhere: int = 0
    """Memories another link held or finished while this sweep reached them."""
    gone: int = 0
    left: int = 0
    """Memories still not linked when the sweep ended (every state but the two linked ones)."""

    def line(self) -> str:
        """The command's one-line summary."""
        return (
            f"Linked {self.linked}, linked empty {self.linked_empty}, "
            f"still pending {self.failed} (model failed), no model {self.no_model}, "
            f"linking elsewhere {self.elsewhere}."
        )

    def count(self, outcome: LinkOutcome) -> None:
        """Add one link's outcome."""
        field = {
            LinkOutcome.LINKED: "linked",
            LinkOutcome.LINKED_EMPTY: "linked_empty",
            LinkOutcome.FAILED: "failed",
            LinkOutcome.NO_MODEL: "no_model",
            LinkOutcome.ELSEWHERE: "elsewhere",
            LinkOutcome.ALREADY_LINKED: "elsewhere",
            LinkOutcome.GONE: "gone",
        }[outcome]
        setattr(self, field, getattr(self, field) + 1)


def counts_line(views: Iterable[LinkView]) -> str:
    """The dry run's line: how many memories are not linked yet, one count per state."""
    counts = Counter(views)
    waiting = sum(n for view, n in counts.items() if view not in _LINKED_VIEWS)
    return (
        f"Not linked yet: {waiting} ({counts[LinkView.PENDING]} pending, "
        f"{counts[LinkView.LINKING]} linking now, {counts[LinkView.INTERRUPTED]} interrupted, "
        f"{counts[LinkView.NO_MODEL]} no model, {counts[LinkView.FAILED]} failed). "
        f"Linked empty: {counts[LinkView.LINKED_EMPTY]}."
    )


def memory_views(engine: Engine) -> list[tuple[LinkRow, LinkView]]:
    """Every memory of the engine's database with its link view now, oldest first."""
    now = datetime.now(UTC)
    rows = link_repository(engine).link_rows(engine.database_name)
    return [(row, link_view(row, now)) for row in rows]


async def relink_pending(
    engine: Engine,
    *,
    honour_backoff: bool,
    include_empty: bool = False,
    limit: int | None = None,
) -> RelinkReport:
    """Link the memories not linked yet, oldest first, one at a time.

    Args:
        engine: The engine of the memories' database.
        honour_backoff: The start-up sweep's rules: a failed memory waits out its backoff,
            and with no extraction model configured nothing is linked (not an attempt).
        include_empty: Also link again the memories that linked with no facts.
        limit: Link at most this many memories (None: every one that is due).

    Returns:
        The counts of what the sweep did, and how many memories are left not linked.
    """
    report = RelinkReport()
    model = extraction_model_configured(engine.settings)
    if not honour_backoff or model:
        snapshot = await asyncio.to_thread(memory_views, engine)
        now = datetime.now(UTC)
        # a live claim: another link has it now (counted over the whole snapshot, limit or not)
        report.elsewhere = sum(1 for _, view in snapshot if view is LinkView.LINKING)
        # with no model a linked_empty memory would only become no_model: it waits for one
        retry_empty = include_empty and model
        rows = [
            row
            for row, view in snapshot
            if due(row, view, now, honour_backoff=honour_backoff, include_empty=retry_empty)
        ]
        for row in rows if limit is None else rows[:limit]:
            report.count(await link_memory(engine, row.id, include_empty=retry_empty))
    after = await asyncio.to_thread(memory_views, engine)
    report.left = sum(1 for _, view in after if view not in _LINKED_VIEWS)
    logger.info(
        "memory_relink_done",
        honour_backoff=honour_backoff,
        include_empty=include_empty,
        linked=report.linked,
        linked_empty=report.linked_empty,
        failed=report.failed,
        no_model=report.no_model,
        elsewhere=report.elsewhere,
        gone=report.gone,
        left=report.left,
    )
    return report


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "RelinkReport",
    "backoff_seconds",
    "counts_line",
    "due",
    "memory_views",
    "relink_pending",
]
