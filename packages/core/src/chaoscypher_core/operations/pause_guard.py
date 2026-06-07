# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared pause guard for source-processing handlers.

Every source-processing handler calls `check_paused` at the top and
returns ``{"skipped": "paused"}`` if it reports paused. Paused is NOT
an error — it consumes no retry budget, does not feed into failure
metrics, and frees the worker immediately so the next queued task
can run.

A source is effectively paused iff:
  * `SourceRow.is_paused` is True (per-source pause), OR
  * `SystemState.processing_paused` is True (system-wide pause).

Per-source pause takes precedence in the returned `scope` field
because it's the more specific signal; the system scope is only
reported when there is no per-source pause.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


@dataclass
class PauseCheckResult:
    """Outcome of a pause check.

    Attributes:
        paused: True iff either source-level or system-level pause is active.
        scope: "source" or "system" when paused is True, else None.
        reason: The operator-supplied reason string, if any.
    """

    paused: bool
    scope: str | None = None
    reason: str | None = None


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter


def check_paused(
    *,
    source_id: str,
    database_name: str,
    adapter: SqliteAdapter,
) -> PauseCheckResult:
    """Check whether a source is effectively paused.

    Safe to call with a non-existent source: if `adapter.get_source`
    returns None, the guard falls through to the system-level check
    rather than raising — this avoids crashing handlers on a
    delete-race and keeps pause semantics uniform.

    Args:
        source_id: The source ID the handler is about to process.
        database_name: The database scope for the source lookup.
        adapter: A SqliteAdapter (or compatible) exposing both
            `get_source` and `get_system_state`.

    Returns:
        A PauseCheckResult: paused=False when neither scope is set;
        paused=True with scope="source" or "system" otherwise.
    """
    source = adapter.get_source(source_id=source_id, database_name=database_name)
    if source and source.get("is_paused"):
        return PauseCheckResult(
            paused=True,
            scope="source",
            reason=source.get("paused_reason"),
        )

    system = adapter.get_system_state()
    if system and system.get("processing_paused"):
        return PauseCheckResult(
            paused=True,
            scope="system",
            reason=system.get("processing_paused_reason"),
        )

    return PauseCheckResult(paused=False)
