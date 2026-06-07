# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: every SourceStatus value must validate through the API DTOs.

The Cortex sources feature historically maintained its own SourceStatus
enum next to the canonical one in chaoscypher_core.models. When VISION_PENDING
was added for the per-page vision pipeline, the cortex copy missed it and
GET /api/v1/sources returned 500 for any source mid-vision-processing
(blank Sources page in the UI until the source advanced past
``vision_pending``).

This test pins the invariant: for every member of the canonical
SourceStatus enum, both SourceSummaryResponse and SourceResponse must
accept the value without raising ValidationError. If a new status is
added to chaoscypher_core.models.SourceStatus, this test fails until
the DTOs (and the type annotations they share) catch up.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.models import (
    SourceResponse,
    SourceSummaryResponse,
)


_MINIMAL_REQUIRED: dict[str, object] = {
    "id": "src_test",
    "database_name": "default",
    "filename": "test.pdf",
    "created_at": datetime(2026, 5, 14, tzinfo=UTC),
    "updated_at": datetime(2026, 5, 14, tzinfo=UTC),
}


@pytest.mark.parametrize("status", list(SourceStatus))
def test_source_summary_response_accepts_every_status(status: SourceStatus) -> None:
    model = SourceSummaryResponse(**_MINIMAL_REQUIRED, status=status)
    assert model.status == status


@pytest.mark.parametrize("status", list(SourceStatus))
def test_source_response_accepts_every_status(status: SourceStatus) -> None:
    model = SourceResponse(**_MINIMAL_REQUIRED, status=status)
    assert model.status == status
