# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression guard: SourceResponse must NOT contain any legacy
extraction_chunks_* field. Catches accidental re-introduction.
"""

from chaoscypher_cortex.features.sources.models import (
    SourceResponse,
    SourceSummaryResponse,
)


_LEGACY_FIELDS = (
    "extraction_chunks_submitted",
    "extraction_chunks_total",
    "extraction_chunk_indices",
    "extraction_last_activity",
    "extraction_entities_preview",
    "extraction_relationships_preview",
)


def test_no_legacy_fields_on_source_response() -> None:
    fields = set(SourceResponse.model_fields)
    for legacy in _LEGACY_FIELDS:
        assert legacy not in fields, f"{legacy!r} resurrected on SourceResponse"


def test_no_legacy_fields_on_source_summary_response() -> None:
    fields = set(SourceSummaryResponse.model_fields)
    for legacy in _LEGACY_FIELDS:
        assert legacy not in fields, f"{legacy!r} resurrected on SourceSummaryResponse"
