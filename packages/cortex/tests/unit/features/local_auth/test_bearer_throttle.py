# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the per-client bearer failure throttle.

Second half of the bcrypt CPU-exhaustion fix: the selector index makes an
unknown key free once every record is migrated, and this throttle caps the
damage an attacker can do through the legacy loop in the meantime.
"""

from __future__ import annotations

from chaoscypher_cortex.features.local_auth.bearer_throttle import (
    BEARER_BLOCK_SECONDS,
    BEARER_FAILURE_LIMIT,
    BEARER_FAILURE_WINDOW_SECONDS,
    BearerFailureThrottle,
)


class _Clock:
    """Monotonic clock stub."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _throttle(clock: _Clock) -> BearerFailureThrottle:
    return BearerFailureThrottle(clock=clock)


def test_constants_match_the_agreed_policy() -> None:
    """5 failures / 60 s window / 60 s block, as named constants."""
    assert BEARER_FAILURE_LIMIT == 5
    assert BEARER_FAILURE_WINDOW_SECONDS == 60
    assert BEARER_BLOCK_SECONDS == 60


def test_fresh_client_is_not_blocked() -> None:
    assert _throttle(_Clock()).is_blocked("10.0.0.5") is False


def test_failures_under_the_limit_do_not_block() -> None:
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT - 1):
        throttle.record_failure("10.0.0.5")
    assert throttle.is_blocked("10.0.0.5") is False


def test_sixth_attempt_short_circuits_after_five_failures() -> None:
    """The 6th bearer attempt from a client is refused without bcrypt."""
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT):
        assert throttle.is_blocked("10.0.0.5") is False
        throttle.record_failure("10.0.0.5")
    assert throttle.is_blocked("10.0.0.5") is True


def test_block_is_per_client() -> None:
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT):
        throttle.record_failure("10.0.0.5")
    assert throttle.is_blocked("10.0.0.5") is True
    assert throttle.is_blocked("10.0.0.6") is False


def test_window_expiry_resets_the_failure_count() -> None:
    """Failures spread wider than the window never accumulate to a block."""
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT * 3):
        throttle.record_failure("10.0.0.5")
        clock.advance(BEARER_FAILURE_WINDOW_SECONDS + 1)
    assert throttle.is_blocked("10.0.0.5") is False


def test_block_expires_after_the_block_window() -> None:
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT):
        throttle.record_failure("10.0.0.5")
    assert throttle.is_blocked("10.0.0.5") is True

    clock.advance(BEARER_BLOCK_SECONDS - 1)
    assert throttle.is_blocked("10.0.0.5") is True

    clock.advance(2)
    assert throttle.is_blocked("10.0.0.5") is False


def test_failure_count_restarts_after_a_block_expires() -> None:
    """A released client gets a fresh budget, not an instant re-block."""
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT):
        throttle.record_failure("10.0.0.5")
    clock.advance(BEARER_BLOCK_SECONDS + 1)
    assert throttle.is_blocked("10.0.0.5") is False

    throttle.record_failure("10.0.0.5")
    assert throttle.is_blocked("10.0.0.5") is False


def test_success_clears_accumulated_failures() -> None:
    """A valid key resets the client's budget."""
    clock = _Clock()
    throttle = _throttle(clock)
    for _ in range(BEARER_FAILURE_LIMIT - 1):
        throttle.record_failure("10.0.0.5")
    throttle.record_success("10.0.0.5")
    for _ in range(BEARER_FAILURE_LIMIT - 1):
        throttle.record_failure("10.0.0.5")
    assert throttle.is_blocked("10.0.0.5") is False


def test_idle_clients_are_pruned_so_memory_stays_bounded() -> None:
    """An address-rotating attacker cannot grow the maps without bound.

    2000 distinct addresses, one failure each, one second apart. The sweep
    that runs on the 2000th failure drops every entry whose last failure
    predates the window, so only the addresses seen inside the trailing
    ``BEARER_FAILURE_WINDOW_SECONDS`` (one per second, plus the current one)
    may survive — a bound derived from the constants, not a round number.
    """
    clock = _Clock()
    throttle = _throttle(clock)
    for i in range(2000):
        throttle.record_failure(f"10.0.{i // 256}.{i % 256}")
        clock.advance(1)
    assert throttle.tracked_clients() <= BEARER_FAILURE_WINDOW_SECONDS + 1


def test_record_failure_reports_only_the_edge_that_latches_the_block() -> None:
    """The return value is the log trigger: True exactly once per block."""
    clock = _Clock()
    throttle = _throttle(clock)
    results = [throttle.record_failure("10.0.0.5") for _ in range(BEARER_FAILURE_LIMIT)]
    assert results[:-1] == [False] * (BEARER_FAILURE_LIMIT - 1)
    assert results[-1] is True

    # Further failures while blocked must not re-report — otherwise an
    # attacker sets the log volume.
    assert throttle.record_failure("10.0.0.5") is False
