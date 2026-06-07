# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for LLMSpendTracker.

Covers:
- record() accumulates per-source (in-memory) and daily (persisted) totals.
- check_and_raise() no-ops when both caps are None.
- check_and_raise() raises LLMSpendCapExceededError when per-source cap reached.
- check_and_raise() raises LLMSpendCapExceededError when daily cap reached.
- LLMSpendCapExceededError is permanent (is_retryable=False) — queue won't retry.
- reset_source() clears the per-source counter.
- get_llm_spend_tracker() returns a singleton.
- record() ignores non-positive token counts.
- The daily total is read from the storage adapter, so it survives a fresh
  tracker instance (worker restart) and storage failures degrade gracefully.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import structlog

from chaoscypher_core.exceptions import LLMSpendCapExceededError
from chaoscypher_core.services.llm.spend import (
    LLMSpendTracker,
    _reset_tracker_for_tests,
    _utc_today,
    get_llm_spend_tracker,
)


class _FakeSpendStore:
    """In-memory stand-in for the SqliteAdapter daily-spend persistence.

    Mirrors the ``get_daily_token_spend`` / ``add_daily_token_spend`` contract
    so tracker logic can be tested without a real database. Keyed by
    ``(database_name, spend_date)`` exactly like ``llm_daily_spend``.
    """

    def __init__(self) -> None:
        self._totals: dict[tuple[str, str], int] = {}

    def get_daily_token_spend(self, *, database_name: str, spend_date: str) -> int:
        return self._totals.get((database_name, spend_date), 0)

    def add_daily_token_spend(self, *, database_name: str, spend_date: str, tokens: int) -> None:
        if tokens <= 0:
            return
        key = (database_name, spend_date)
        self._totals[key] = self._totals.get(key, 0) + tokens


class _BrokenSpendStore:
    """Store whose persistence calls always raise — exercises best-effort degrade."""

    def get_daily_token_spend(self, *, database_name: str, spend_date: str) -> int:
        raise RuntimeError("db down")

    def add_daily_token_spend(self, *, database_name: str, spend_date: str, tokens: int) -> None:
        raise RuntimeError("db down")


def _settings(
    *,
    per_source: int | None = None,
    per_day: int | None = None,
) -> SimpleNamespace:
    """Build a minimal stub matching the settings.llm.* contract used by check_and_raise."""
    return SimpleNamespace(
        llm=SimpleNamespace(
            max_tokens_per_source=per_source,
            max_tokens_per_day=per_day,
        )
    )


def _ctx() -> dict[str, object]:
    """Adapter + database_name kwargs shared by record/check_and_raise calls."""
    return {"adapter": _FakeSpendStore(), "database_name": "default"}


def test_record_accumulates_per_source_and_daily_totals() -> None:
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record("src-A", 100, **ctx)
    tracker.record("src-A", 250, **ctx)
    tracker.record("src-B", 75, **ctx)

    assert tracker.tokens_for_source("src-A") == 350
    assert tracker.tokens_for_source("src-B") == 75
    assert tracker.tokens_for_source("unknown") == 0
    assert tracker.tokens_today(adapter=store, database_name="default") == 425


def test_record_ignores_non_positive_tokens() -> None:
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record("src", 0, **ctx)
    tracker.record("src", -5, **ctx)
    assert tracker.tokens_for_source("src") == 0
    assert tracker.tokens_today(adapter=store, database_name="default") == 0


def test_check_no_op_when_both_caps_disabled() -> None:
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    tracker.record("src", 10_000_000, adapter=store, database_name="default")
    # No caps → no raise even with huge accumulated total.
    tracker.check_and_raise("src", _settings(), adapter=store, database_name="default")


def test_check_raises_when_per_source_cap_reached() -> None:
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record("src-A", 1000, **ctx)
    tracker.check_and_raise("src-A", _settings(per_source=1001), **ctx)

    tracker.record("src-A", 1, **ctx)  # now 1001
    with pytest.raises(LLMSpendCapExceededError) as exc:
        tracker.check_and_raise("src-A", _settings(per_source=1001), **ctx)
    assert exc.value.scope == "source"
    assert exc.value.cap_tokens == 1001
    assert exc.value.consumed_tokens == 1001
    assert exc.value.source_id == "src-A"
    assert exc.value.is_retryable is False
    assert exc.value.code == "LLM_SPEND_CAP_EXCEEDED"


def test_check_raises_when_daily_cap_reached() -> None:
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record("src-A", 500, **ctx)
    tracker.record("src-B", 500, **ctx)
    with pytest.raises(LLMSpendCapExceededError) as exc:
        tracker.check_and_raise("src-A", _settings(per_day=1000), **ctx)
    assert exc.value.scope == "day"
    assert exc.value.consumed_tokens == 1000
    assert exc.value.cap_tokens == 1000


def test_check_per_source_skipped_when_source_id_is_none() -> None:
    """Non-source-scoped calls (interactive) only consult the daily cap."""
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record(None, 500, **ctx)  # daily-only accumulation
    # Per-source cap of 1 would have fired if source_id were given,
    # but with None it's skipped and daily cap of 1000 still has headroom.
    tracker.check_and_raise(None, _settings(per_source=1, per_day=1000), **ctx)


def test_reset_source_clears_per_source_counter() -> None:
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record("src", 5000, **ctx)
    assert tracker.tokens_for_source("src") == 5000
    tracker.reset_source("src")
    assert tracker.tokens_for_source("src") == 0
    # Daily counter is NOT reset (the spend already happened today).
    assert tracker.tokens_today(adapter=store, database_name="default") == 5000


def test_get_llm_spend_tracker_returns_singleton() -> None:
    _reset_tracker_for_tests()
    a = get_llm_spend_tracker()
    b = get_llm_spend_tracker()
    assert a is b
    _reset_tracker_for_tests()


def test_per_source_cap_does_not_fire_for_other_sources() -> None:
    """Cap on src-A doesn't affect src-B even if global tokens are high."""
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    ctx = {"adapter": store, "database_name": "default"}
    tracker.record("src-A", 2000, **ctx)
    tracker.record("src-B", 50, **ctx)
    # src-A is over its per-source cap, but src-B is well under.
    tracker.check_and_raise("src-B", _settings(per_source=1000), **ctx)
    with pytest.raises(LLMSpendCapExceededError):
        tracker.check_and_raise("src-A", _settings(per_source=1000), **ctx)


# ---------------------------------------------------------------------------
# Persistence behaviour (the daily counter lives in storage, not the process)
# ---------------------------------------------------------------------------


def test_daily_total_survives_fresh_tracker_instance() -> None:
    """A new tracker (simulating a worker restart) sees the persisted daily
    total — the crash-loop re-arm the fix exists to prevent.
    """
    store = _FakeSpendStore()
    first = LLMSpendTracker()
    first.record("src-A", 900, adapter=store, database_name="default")

    # Worker restarts: brand-new tracker, same persisted store.
    second = LLMSpendTracker()
    assert second.tokens_today(adapter=store, database_name="default") == 900
    with pytest.raises(LLMSpendCapExceededError) as exc:
        second.check_and_raise(
            "src-A", _settings(per_day=900), adapter=store, database_name="default"
        )
    assert exc.value.scope == "day"


def test_daily_total_is_per_database() -> None:
    """The daily budget is scoped per database (each app.db is one database)."""
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    tracker.record("src-A", 1000, adapter=store, database_name="default")
    # A different database starts fresh.
    assert tracker.tokens_today(adapter=store, database_name="other") == 0
    tracker.check_and_raise(None, _settings(per_day=1000), adapter=store, database_name="other")


def test_record_keys_daily_total_by_utc_date() -> None:
    """record() writes the daily total under today's UTC date key."""
    tracker = LLMSpendTracker()
    store = _FakeSpendStore()
    tracker.record("src-A", 42, adapter=store, database_name="default")
    assert store.get_daily_token_spend(database_name="default", spend_date=_utc_today()) == 42


def test_daily_read_failure_degrades_open() -> None:
    """A storage read failure during the cap check must not block the LLM call."""
    tracker = LLMSpendTracker()
    broken = _BrokenSpendStore()
    # Daily cap set, but the store read raises → treated as 0 → no raise.
    tracker.check_and_raise("src-A", _settings(per_day=1), adapter=broken, database_name="default")


def test_daily_write_failure_does_not_raise() -> None:
    """A storage write failure during record() must not fail the LLM operation."""
    tracker = LLMSpendTracker()
    broken = _BrokenSpendStore()
    # Must NOT raise even though the persisted write fails. Per-source still tracked.
    tracker.record("src-A", 100, adapter=broken, database_name="default")
    assert tracker.tokens_for_source("src-A") == 100


# ---------------------------------------------------------------------------
# Mutation-coverage tests (mutmut) — pin invariants that survived mutation.
# ---------------------------------------------------------------------------


def test_unrecorded_source_does_not_trip_per_source_cap() -> None:
    """Per-source default for unseen src must be 0 (not None, not 1).

    Kills mutants that change ``_source_tokens.get(source_id, 0)`` to
    ``get(..., None)`` (TypeError on ``None >= int``) or ``get(..., 1)``.
    """
    tracker = LLMSpendTracker()
    tracker.check_and_raise("never-seen-src", _settings(per_source=1), **_ctx())


def test_per_source_log_includes_all_canonical_fields() -> None:
    """The per-source cap warning must log the exact event and kwargs."""
    tracker = LLMSpendTracker()
    ctx = _ctx()
    tracker.record("src-A", 100, **ctx)
    with structlog.testing.capture_logs() as logs, pytest.raises(LLMSpendCapExceededError):
        tracker.check_and_raise("src-A", _settings(per_source=100), **ctx)

    warnings = [r for r in logs if r.get("log_level") == "warning"]
    assert len(warnings) == 1
    rec = warnings[0]
    assert rec["event"] == "llm_spend_cap_exceeded"
    assert rec["scope"] == "source"
    assert rec["source_id"] == "src-A"
    assert rec["consumed_tokens"] == 100
    assert rec["cap_tokens"] == 100


def test_daily_log_includes_all_canonical_fields() -> None:
    """The daily cap warning must log the exact event and kwargs."""
    tracker = LLMSpendTracker()
    ctx = _ctx()
    tracker.record("src-A", 600, **ctx)
    tracker.record("src-B", 400, **ctx)
    with structlog.testing.capture_logs() as logs, pytest.raises(LLMSpendCapExceededError):
        tracker.check_and_raise("src-A", _settings(per_day=1000), **ctx)

    warnings = [r for r in logs if r.get("log_level") == "warning"]
    assert len(warnings) == 1
    rec = warnings[0]
    assert rec["event"] == "llm_spend_cap_exceeded"
    assert rec["scope"] == "day"
    assert rec["consumed_tokens"] == 1000
    assert rec["cap_tokens"] == 1000


def test_daily_cap_exception_preserves_source_id() -> None:
    """Day-scope cap must still carry the triggering source_id."""
    tracker = LLMSpendTracker()
    ctx = _ctx()
    tracker.record("src-A", 1000, **ctx)
    with pytest.raises(LLMSpendCapExceededError) as exc:
        tracker.check_and_raise("src-A", _settings(per_day=1000), **ctx)
    assert exc.value.scope == "day"
    assert exc.value.source_id == "src-A"
    assert exc.value.details["source_id"] == "src-A"


def test_reset_source_is_idempotent_for_unknown_source() -> None:
    """reset_source('unknown') must not raise — uses ``pop(key, None)``."""
    tracker = LLMSpendTracker()
    tracker.reset_source("ghost")


def test_record_zero_tokens_does_not_register_source() -> None:
    """``record(src, 0)`` must short-circuit before touching the dict."""
    tracker = LLMSpendTracker()
    tracker.record("src-A", 0, **_ctx())
    assert "src-A" not in tracker._source_tokens


def test_get_llm_spend_tracker_returns_a_real_instance() -> None:
    """The factory must materialise an ``LLMSpendTracker`` on first call."""
    _reset_tracker_for_tests()
    try:
        tracker = get_llm_spend_tracker()
        assert tracker is not None
        assert isinstance(tracker, LLMSpendTracker)
    finally:
        _reset_tracker_for_tests()


def test_reset_tracker_for_tests_clears_to_none() -> None:
    """After ``_reset_tracker_for_tests``, the next ``get_*`` builds a fresh instance."""
    _ = get_llm_spend_tracker()
    _reset_tracker_for_tests()
    tracker = get_llm_spend_tracker()
    assert isinstance(tracker, LLMSpendTracker)
    _reset_tracker_for_tests()
