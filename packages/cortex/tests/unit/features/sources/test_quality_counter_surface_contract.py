# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin the contract between ``QualityCounter`` and ``QualityMetrics``.

A quality counter is only useful if the UI can see it.  Today the path
is:

    QualityCounter (enum) -> sources.<col> (SQLite) -> SourceResponse
    (Cortex) -> QualityMetrics (Cortex Pydantic) -> Data Quality tab.

If a new ``QualityCounter`` member ships without a matching field on
``QualityMetrics``, the column increments silently for weeks and the
tile reads ``undefined``.  That is exactly how
``QualityCounter.VISION_PAGES_TRUNCATED`` shipped on 2026-05-13 and
went unread until 2026-05-19.

These tests pin both halves of the contract so the same regression
cannot recur:

* ``test_every_quality_counter_is_surfaced_in_quality_metrics`` — for
  every ``QualityCounter`` member, assert the string value is a field
  on ``QualityMetrics``.  Failure names the missing field so the next
  maintainer knows exactly what to add.
* ``test_every_quality_metrics_counter_field_is_populated_by_mapper``
  — for every ``QualityMetrics`` field, assert the
  ``SourceResponse._derive_computed_fields`` mapper actually populates
  it (catches the "field declared but never assigned" case).

**If you add a QualityCounter, this test forces you to also expose it
on QualityMetrics and wire it through the SourceResponse mapper, or
the test fails.**
"""

from __future__ import annotations

from datetime import UTC, datetime

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_cortex.features.sources.models import (
    QualityMetrics,
    SourceResponse,
)


# Fields on ``QualityMetrics`` that are populated by helpers OTHER than
# ``increment_quality_counter`` — ``set_loader_encoding`` records the
# loader's chosen encoding, and the four ``mark_search_indexing_*``
# helpers transition vector-search status.  These are observability
# fields, not counters, so they do not need a matching ``QualityCounter``
# member.  Keep this list short and explicit; if it grows, audit before
# adding.
_NON_COUNTER_QUALITY_METRICS_FIELDS: frozenset[str] = frozenset(
    {
        "loader_encoding_used",
        "vector_indexed_at",
        "vector_indexing_status",
    }
)


def test_every_quality_counter_is_surfaced_in_quality_metrics() -> None:
    """Every ``QualityCounter`` enum value must be a field on ``QualityMetrics``.

    Regression guard: ``QualityCounter.VISION_PAGES_TRUNCATED`` was
    incrementing the SQLite column for weeks while the UI tile read
    ``undefined`` because the field was missing from
    ``QualityMetrics``.  If you add a new counter, add the matching
    field here too.
    """
    counter_values = {c.value for c in QualityCounter}
    qm_fields = set(QualityMetrics.model_fields.keys())

    missing = sorted(counter_values - qm_fields)

    assert not missing, (
        "QualityCounter members without a matching field on "
        "QualityMetrics — these increments will never reach the Data "
        "Quality UI tab. Add the following field(s) to "
        "chaoscypher_cortex.features.sources.models.QualityMetrics "
        "(and wire them through SourceResponse._derive_computed_fields): "
        f"{missing}"
    )


def test_quality_metrics_extra_fields_are_explicitly_allowlisted() -> None:
    """``QualityMetrics`` may carry observability fields that are not counters.

    But every such field must be in the explicit allowlist above so a
    typo or accidentally-added field is caught.  ``loader_encoding_used``
    (set by ``set_loader_encoding``) and ``vector_indexed_at`` /
    ``vector_indexing_status`` (set by ``mark_search_indexing_*``) are
    the only legitimate non-counter fields today.
    """
    counter_values = {c.value for c in QualityCounter}
    qm_fields = set(QualityMetrics.model_fields.keys())

    extras = qm_fields - counter_values
    unexpected = sorted(extras - _NON_COUNTER_QUALITY_METRICS_FIELDS)

    assert not unexpected, (
        "QualityMetrics has fields that are neither a QualityCounter "
        "value nor in the _NON_COUNTER_QUALITY_METRICS_FIELDS "
        "allowlist. Either add a matching QualityCounter member, or "
        "extend the allowlist with a justification comment: "
        f"{unexpected}"
    )


def _minimal_row_with_all_counters_set() -> dict[str, object]:
    """Source-row dict with every ``QualityMetrics`` field set to a sentinel.

    Numeric counters get a unique positive int (the enum's hash mod a
    large prime is fine — we only need "not zero, not the default" so
    the mapper test can prove the value flowed through).  JSON-shaped
    fields get a one-key dict.  Datetime / string status fields get
    fixed sentinels.
    """
    now = datetime.now(UTC)
    row: dict[str, object] = {
        # Required SourceResponse fields.
        "id": "src_contract",
        "database_name": "default",
        "filename": "doc.txt",
        "status": SourceStatus.INDEXED,
        "created_at": now,
        "updated_at": now,
        # Non-counter observability fields.
        "loader_encoding_used": "utf-8",
        "vector_indexed_at": now,
        "vector_indexing_status": "indexed",
        # JSON-shaped counters (dict[str, int]).
        "loader_html_dropped_tags": {"script": 1},
        "loader_pptx_shapes_skipped": {"picture": 1},
    }

    # Every numeric counter gets a unique non-zero int.  Using a stable
    # mapping (index + 1) keeps the diff readable if the test ever
    # fails — the value at slot N is N+1.
    numeric_counters = [
        c
        for c in QualityCounter
        if c.value not in {"loader_html_dropped_tags", "loader_pptx_shapes_skipped"}
    ]
    for idx, counter in enumerate(numeric_counters):
        row[counter.value] = idx + 1

    return row


def test_every_quality_metrics_counter_field_is_populated_by_mapper() -> None:
    """``SourceResponse._derive_computed_fields`` must wire every QM field.

    Bonus contract: a field can be declared on ``QualityMetrics`` and
    never assigned by the mapper, in which case it always reports its
    default (typically 0).  That is the second silent-gap mode the
    VISION_PAGES_TRUNCATED bug could have taken.  This test sets every
    row-level counter to a unique non-default value and asserts the
    nested ``quality_metrics`` carries those values through — proving
    the mapper actually reads the row column.
    """
    row = _minimal_row_with_all_counters_set()

    response = SourceResponse(**row)
    assert response.quality_metrics is not None
    metrics = response.quality_metrics

    unmapped: list[str] = []
    for field_name in QualityMetrics.model_fields:
        expected = row.get(field_name)
        actual = getattr(metrics, field_name)
        if expected != actual:
            unmapped.append(f"  - {field_name}: row={expected!r} -> metrics={actual!r}")

    assert not unmapped, (
        "QualityMetrics fields not populated by "
        "SourceResponse._derive_computed_fields. Add the missing "
        "`<field>=self.<field>,` line to the QualityMetrics(...) "
        "constructor in models.py around line 551:\n" + "\n".join(unmapped)
    )
