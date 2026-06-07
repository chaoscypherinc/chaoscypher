# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: invalid filtering_mode must 400 at the API (audit fix M6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chaoscypher_cortex.features.sources.extraction_api import TriggerExtractionRequest
from chaoscypher_cortex.features.sources.models import UrlImportRequest


def test_url_import_request_rejects_unknown_filtering_mode() -> None:
    with pytest.raises(ValidationError):
        UrlImportRequest(
            url="https://example.com/",
            filtering_mode="garbage_mode",
        )


def test_url_import_request_accepts_known_filtering_mode() -> None:
    # Should not raise.
    req = UrlImportRequest(
        url="https://example.com/",
        filtering_mode="balanced",
    )
    assert req.filtering_mode == "balanced"


def test_url_import_request_accepts_none() -> None:
    req = UrlImportRequest(url="https://example.com/")
    assert req.filtering_mode is None


def test_trigger_extraction_rejects_unknown_filtering_mode() -> None:
    with pytest.raises(ValidationError):
        TriggerExtractionRequest(filtering_mode="garbage_mode")


def test_trigger_extraction_accepts_canonical_modes_only() -> None:
    """Canonical filtering modes validate on TriggerExtractionRequest."""
    for canonical in ("maximum", "strict", "balanced", "lenient", "minimal", "unfiltered"):
        req = TriggerExtractionRequest(filtering_mode=canonical)
        assert req.filtering_mode == canonical


@pytest.mark.parametrize("alias", ["standard", "narrative", "precise", "permissive", "raw"])
def test_trigger_extraction_rejects_legacy_aliases(alias: str) -> None:
    """Legacy aliases are rejected at the TriggerExtractionRequest edge.

    Counterpart to the UrlImportRequest coverage in test_models.py — keeps
    the rejection contract explicit on both API surfaces that thread
    filtering_mode through.
    """
    with pytest.raises(ValidationError):
        TriggerExtractionRequest(filtering_mode=alias)


def test_trigger_extraction_accepts_none() -> None:
    req = TriggerExtractionRequest()
    assert req.filtering_mode is None
