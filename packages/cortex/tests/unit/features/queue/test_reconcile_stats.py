# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ReconcileStats dataclass."""

from chaoscypher_core.queue.reconciler import ReconcileStats


def test_defaults_are_zero() -> None:
    stats = ReconcileStats()
    assert stats.recovered_orphans == 0
    assert stats.recovered_crashed == 0
    assert stats.failed_unrecoverable == 0
    assert stats.total() == 0


def test_total_sums_all_categories() -> None:
    stats = ReconcileStats(
        recovered_orphans=3,
        recovered_crashed=2,
        failed_unrecoverable=1,
    )
    assert stats.total() == 6


def test_merge_accumulates() -> None:
    a = ReconcileStats(recovered_orphans=1, recovered_crashed=2)
    b = ReconcileStats(recovered_orphans=4, failed_unrecoverable=1)
    a.merge(b)
    assert a.recovered_orphans == 5
    assert a.recovered_crashed == 2
    assert a.failed_unrecoverable == 1


def test_to_dict_for_api_response() -> None:
    stats = ReconcileStats(recovered_orphans=1, recovered_crashed=2, failed_unrecoverable=3)
    assert stats.to_dict() == {
        "recovered_orphans": 1,
        "recovered_crashed": 2,
        "failed_unrecoverable": 3,
    }
