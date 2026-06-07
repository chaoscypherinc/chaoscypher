# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerStatsTracker."""

import pytest

from chaoscypher_core.services.workflows.triggers.management.stats_tracker import (
    TriggerStatsTracker,
)


@pytest.fixture
def tracker():
    """Create a TriggerStatsTracker with small history limit for testing."""
    return TriggerStatsTracker(history_limit=5)


def _record(tracker, trigger_id="t1", success=True, execution_time=1.0, error=None):
    """Helper to record an execution with sensible defaults."""
    tracker.record_execution(
        execution_id=f"exec_{id(tracker)}_{trigger_id}",
        trigger_id=trigger_id,
        trigger_name=f"Trigger {trigger_id}",
        workflow_id="w1",
        workflow_name="Workflow 1",
        event_source="node.create",
        success=success,
        execution_time=execution_time,
        error=error,
    )


# ============================================================================
# record_execution
# ============================================================================


class TestRecordExecution:
    """Tests for TriggerStatsTracker.record_execution."""

    def test_increments_total_on_success(self, tracker) -> None:
        _record(tracker, success=True)
        stats = tracker.trigger_stats["t1"]
        assert stats.total_executions == 1
        assert stats.successful == 1
        assert stats.failed == 0

    def test_increments_total_on_failure(self, tracker) -> None:
        _record(tracker, success=False, error="timeout")
        stats = tracker.trigger_stats["t1"]
        assert stats.total_executions == 1
        assert stats.successful == 0
        assert stats.failed == 1

    def test_adds_to_history(self, tracker) -> None:
        _record(tracker)
        assert len(tracker.trigger_history["t1"]) == 1

    def test_tracks_multiple_triggers_independently(self, tracker) -> None:
        _record(tracker, trigger_id="t1")
        _record(tracker, trigger_id="t2")
        _record(tracker, trigger_id="t1")
        assert tracker.trigger_stats["t1"].total_executions == 2
        assert tracker.trigger_stats["t2"].total_executions == 1


# ============================================================================
# Stats calculation
# ============================================================================


class TestStatsCalculation:
    """Tests for stats accuracy."""

    def test_success_rate(self, tracker) -> None:
        _record(tracker, success=True)
        _record(tracker, success=True)
        _record(tracker, success=False)
        stats = tracker.trigger_stats["t1"]
        assert stats.success_rate == pytest.approx(2 / 3)

    def test_avg_execution_time_from_successes_only(self, tracker) -> None:
        _record(tracker, success=True, execution_time=2.0)
        _record(tracker, success=True, execution_time=4.0)
        _record(tracker, success=False, execution_time=100.0)
        stats = tracker.trigger_stats["t1"]
        assert stats.avg_execution_time == pytest.approx(3.0)

    def test_zero_success_rate_when_all_fail(self, tracker) -> None:
        _record(tracker, success=False)
        _record(tracker, success=False)
        stats = tracker.trigger_stats["t1"]
        assert stats.success_rate == 0.0
        assert stats.avg_execution_time == 0.0


# ============================================================================
# History limit
# ============================================================================


class TestHistoryLimit:
    """Tests for circular buffer behavior."""

    def test_evicts_oldest_when_limit_reached(self, tracker) -> None:
        for _ in range(7):  # Limit is 5
            _record(tracker)
        assert len(tracker.trigger_history["t1"]) == 5

    def test_stats_count_beyond_history_limit(self, tracker) -> None:
        for _ in range(7):
            _record(tracker)
        assert tracker.trigger_stats["t1"].total_executions == 7


# ============================================================================
# get_all_stats
# ============================================================================


class TestGetAllStats:
    """Tests for TriggerStatsTracker.get_all_stats."""

    def test_returns_dict_of_all_triggers(self, tracker) -> None:
        _record(tracker, trigger_id="t1")
        _record(tracker, trigger_id="t2")
        all_stats = tracker.get_all_stats()
        assert "t1" in all_stats
        assert "t2" in all_stats
        assert all_stats["t1"]["total_executions"] == 1
        assert all_stats["t2"]["total_executions"] == 1
