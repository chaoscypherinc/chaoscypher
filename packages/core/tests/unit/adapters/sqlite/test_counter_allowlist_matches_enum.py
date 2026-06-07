# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Drift guard: SourceLifecycleMixin._COUNTER_COLUMN_ALLOWLIST tracks QualityCounter.

Before 2026-05-09 the SQLite adapter carried a hand-maintained allowlist
of integer counter columns parallel to the ``QualityCounter`` enum.  When
new counters were added — Phase 2 (evidence_*), Phase 7 (embedding_*,
ocr_cleaner_skipped_by_predicate, etc.) — the allowlist was forgotten.
``increment_quality_counter`` swallows the resulting ``ValueError`` so
silent-drop sites silently *failed to log* their drops, defeating the
whole point of the counter pipeline.

The allowlist is now derived from the enum minus an explicit set of
JSON-shaped counters.  This test pins the relationship so any future
counter addition that breaks the contract — typically by forgetting to
declare a JSON exclusion or by introducing a non-counter enum member —
fails CI rather than rotting silently in production.
"""

from __future__ import annotations

from chaoscypher_core.adapters.sqlite.mixins.source_files import SourceLifecycleMixin
from chaoscypher_core.services.quality.counters import QualityCounter


def test_allowlist_equals_enum_minus_json_exclusions() -> None:
    """The integer allowlist is exactly the enum minus the declared JSON columns.

    Adding a new ``QualityCounter`` member without declaring it in
    ``_NON_INTEGER_QUALITY_COUNTERS`` automatically extends the allowlist
    — that's the intended single-source-of-truth.  Adding a JSON-shaped
    counter without listing it in the exclusion set will fail this test
    by leaving the JSON column on the integer-increment path.
    """
    enum_values = {c.value for c in QualityCounter}
    expected = enum_values - SourceLifecycleMixin._NON_INTEGER_QUALITY_COUNTERS

    assert expected == SourceLifecycleMixin._COUNTER_COLUMN_ALLOWLIST


def test_json_exclusions_are_real_enum_members() -> None:
    """Every member of ``_NON_INTEGER_QUALITY_COUNTERS`` is a known counter.

    Stale exclusion entries (typos, removed counters) silently widen the
    allowlist.  Pinning membership keeps the exclusion set honest.
    """
    enum_values = {c.value for c in QualityCounter}
    for excluded in SourceLifecycleMixin._NON_INTEGER_QUALITY_COUNTERS:
        assert excluded in enum_values, (
            f"{excluded!r} is in _NON_INTEGER_QUALITY_COUNTERS but is not a "
            f"QualityCounter member — remove it or fix the typo."
        )


def test_every_integer_counter_can_be_incremented() -> None:
    """Regression: every QualityCounter not declared JSON is increment-eligible.

    Reproduces the original 2026-05-09 bug class.  If a future refactor
    accidentally drops a counter from the allowlist (e.g., by hand-coding
    the set again), this assertion fails immediately.
    """
    json_excluded = SourceLifecycleMixin._NON_INTEGER_QUALITY_COUNTERS
    for counter in QualityCounter:
        if counter.value in json_excluded:
            continue
        assert counter.value in SourceLifecycleMixin._COUNTER_COLUMN_ALLOWLIST, (
            f"QualityCounter.{counter.name} ({counter.value!r}) is not in the "
            f"adapter's integer-increment allowlist. Either add it to "
            f"_NON_INTEGER_QUALITY_COUNTERS (if it is a JSON column) or "
            f"verify the allowlist derivation in source_files.py."
        )
