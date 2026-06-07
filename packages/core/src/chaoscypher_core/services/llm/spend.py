# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM token-spend tracking with per-source and per-day caps.

The :class:`LLMSpendTracker` backs the spend-cap contract:

- ``check_and_raise(source_id, settings, adapter, database_name)`` is called
  BEFORE every LLM call. If either the per-source or per-day cap is reached, it
  raises :class:`LLMSpendCapExceededError` (a permanent, non-retryable error)
  so the queue does NOT retry and the source is marked failed.
- ``record(source_id, tokens, adapter, database_name)`` is called AFTER each
  LLM call with the combined input + output token count.

**Per-source** totals are process-local (in-memory): a source's extraction
runs within one worker lifetime, so resetting on restart only loosens a
finer-grained guard. The **daily** total is persisted per-database in the
``llm_daily_spend`` table (keyed by UTC date) so a worker crash-loop cannot
re-arm a *set* daily budget on restart — the cap holds across restarts and the
window rolls automatically at UTC midnight (a new date reads a fresh row).
Persistence is per-database; with one active database (the common case) that is
equivalent to a worker-wide daily budget, and it works anywhere a storage
adapter is available (worker, Cortex, CLI, MCP) without depending on the queue.

Both caps are opt-in (settings default ``None``). Storage access is
best-effort: a counter read/write failure is logged and the pipeline proceeds —
the billing backstop must never itself break an LLM operation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import LLMSpendCapExceededError


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


def _utc_today() -> str:
    """Return today's UTC date as an ISO ``YYYY-MM-DD`` string (the daily key)."""
    return datetime.now(UTC).date().isoformat()


class LLMSpendTracker:
    """Tracks LLM token consumption for the per-source and per-day caps.

    Per-source totals live in ``_source_tokens`` (in-memory, process-local).
    The daily total is read/written through the storage adapter
    (``llm_daily_spend``), so it is authoritative and survives worker restarts.

    The lock serialises the in-memory per-source map so two concurrent handlers
    cannot race past the per-source cap by interleaving check + record steps.
    """

    def __init__(self) -> None:
        """Initialize the tracker with an empty per-source map."""
        self._source_tokens: dict[str, int] = {}
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Read helpers (used by tests / observability)
    # ------------------------------------------------------------------
    def tokens_for_source(self, source_id: str) -> int:
        """Return tokens consumed by a given source in the lifetime of the worker."""
        with self._lock:
            return self._source_tokens.get(source_id, 0)

    def tokens_today(self, *, adapter: Any, database_name: str) -> int:
        """Return tokens consumed in the current UTC-day window (persisted)."""
        return self._daily_spent(adapter, database_name)

    def reset_source(self, source_id: str) -> None:
        """Forget the running count for a source (called on force re-extract)."""
        with self._lock:
            self._source_tokens.pop(source_id, None)

    # ------------------------------------------------------------------
    # Persistence (best-effort — never break the pipeline on a storage blip)
    # ------------------------------------------------------------------
    def _daily_spent(self, adapter: Any, database_name: str) -> int:
        """Read today's persisted daily total. Returns 0 on storage failure."""
        try:
            return int(
                adapter.get_daily_token_spend(database_name=database_name, spend_date=_utc_today())
            )
        except Exception:
            logger.warning(
                "llm_spend_daily_read_failed",
                database_name=database_name,
                exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------
    # Cap enforcement
    # ------------------------------------------------------------------
    def check_and_raise(
        self,
        source_id: str | None,
        settings: Settings | EngineSettings,
        *,
        adapter: Any,
        database_name: str,
    ) -> None:
        """Raise :class:`LLMSpendCapExceededError` when either cap is reached.

        Called BEFORE every LLM call. The per-source check is skipped when
        ``source_id`` is None (interactive flows not tied to a single source) —
        only the daily cap applies in that case.

        Args:
            source_id: Source identifier or None for non-source-scoped calls.
            settings: Application settings (reads ``llm.max_tokens_per_source``
                and ``llm.max_tokens_per_day``).
            adapter: Storage adapter exposing ``get_daily_token_spend``.
            database_name: Active database whose daily budget applies.

        Raises:
            LLMSpendCapExceededError: Either cap reached. Permanent (no retry).
        """
        per_source = settings.llm.max_tokens_per_source
        per_day = settings.llm.max_tokens_per_day

        if per_source is None and per_day is None:
            return

        if per_source is not None and source_id is not None:
            with self._lock:
                consumed = self._source_tokens.get(source_id, 0)
            if consumed >= per_source:
                logger.warning(
                    "llm_spend_cap_exceeded",
                    scope="source",
                    source_id=source_id,
                    consumed_tokens=consumed,
                    cap_tokens=per_source,
                )
                raise LLMSpendCapExceededError(
                    scope="source",
                    cap_tokens=per_source,
                    consumed_tokens=consumed,
                    source_id=source_id,
                )

        if per_day is not None:
            daily = self._daily_spent(adapter, database_name)
            if daily >= per_day:
                logger.warning(
                    "llm_spend_cap_exceeded",
                    scope="day",
                    consumed_tokens=daily,
                    cap_tokens=per_day,
                )
                raise LLMSpendCapExceededError(
                    scope="day",
                    cap_tokens=per_day,
                    consumed_tokens=daily,
                    source_id=source_id,
                )

    def record(
        self,
        source_id: str | None,
        tokens: int,
        *,
        adapter: Any,
        database_name: str,
    ) -> None:
        """Add ``tokens`` to the per-source (in-memory) and daily (persisted) totals.

        Called AFTER every LLM call with ``input_tokens + output_tokens``. No-op
        when ``tokens <= 0`` (providers occasionally return None / 0 on
        streaming failures). The persisted daily write is best-effort: a storage
        failure is logged but never fails the just-completed LLM operation.
        """
        if tokens <= 0:
            return
        with self._lock:
            if source_id is not None:
                self._source_tokens[source_id] = self._source_tokens.get(source_id, 0) + tokens
        try:
            adapter.add_daily_token_spend(
                database_name=database_name,
                spend_date=_utc_today(),
                tokens=tokens,
            )
        except Exception:
            logger.warning(
                "llm_spend_daily_write_failed",
                database_name=database_name,
                tokens=tokens,
                exc_info=True,
            )


# Process-wide singleton — workers see one tracker per process.
_TRACKER: LLMSpendTracker | None = None


def get_llm_spend_tracker() -> LLMSpendTracker:
    """Return the process-wide :class:`LLMSpendTracker` singleton."""
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = LLMSpendTracker()
    return _TRACKER


def _reset_tracker_for_tests() -> None:
    """Clear the singleton between tests. Test-only — never call in production."""
    global _TRACKER
    _TRACKER = None


__all__ = ["LLMSpendTracker", "_reset_tracker_for_tests", "get_llm_spend_tracker"]
