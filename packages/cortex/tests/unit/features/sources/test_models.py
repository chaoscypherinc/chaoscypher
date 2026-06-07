# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for Sources API Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chaoscypher_cortex.features.sources.models import UrlImportRequest


@pytest.mark.parametrize("alias", ["standard", "narrative", "precise", "permissive", "raw"])
def test_legacy_filtering_mode_aliases_rejected(alias: str) -> None:
    """Legacy aliases must be rejected at the API edge.

    Pre-2026-05-08 they were accepted at the API but rejected at the engine,
    causing a runtime crash inside the extraction worker. Phase 1 of the
    import-pipeline data-quality hardening removes the API-side accept.
    """
    with pytest.raises(ValidationError):
        UrlImportRequest(url="https://example.com", filtering_mode=alias)


def test_canonical_filtering_modes_still_accepted() -> None:
    """Regression guard: the six canonical modes are still valid."""
    for canonical in ("maximum", "strict", "balanced", "lenient", "minimal", "unfiltered"):
        # Should not raise.
        UrlImportRequest(url="https://example.com", filtering_mode=canonical)
