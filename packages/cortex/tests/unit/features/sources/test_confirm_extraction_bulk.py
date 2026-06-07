# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""POST /sources/confirmation (bulk): per-item {source_id, ok, error}
envelope; one failing item does not abort the rest.

Test structure:
- Service-level tests: prove the per-item loop, no-abort-on-first, and error-cap
  by mocking ``SourceService.confirm_extraction`` directly.
- Route-level test: prove the route delegates to the service method and returns
  its result (thin delegation test — no loop logic here).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.extraction_api import (
    BulkConfirmExtractionRequest,
    BulkConfirmExtractionResponse,
    BulkConfirmItem,
    confirm_extraction_bulk,
)


# ---------------------------------------------------------------------------
# Service-level: per-item loop + no-abort-on-first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_bulk_confirm_mixed_results_per_item() -> None:
    """Valid + conflict + missing batch: valid confirmed, others recorded, loop continues."""
    from chaoscypher_core.exceptions import ConflictError
    from chaoscypher_cortex.features.sources.service import SourceService

    # Build a minimal SourceService with only the dependencies the bulk method uses.
    svc = object.__new__(SourceService)
    settings_mock = MagicMock()
    settings_mock.logs.error_message_preview_chars = 200
    svc.settings = settings_mock  # type: ignore[attr-defined]

    async def _confirm(*, source_id: str, **_kwargs):
        if source_id == "bad":
            raise ConflictError("Source status is 'extracting', confirm requires ...")
        return {"source_id": source_id, "status": "indexed"}

    with patch.object(svc, "confirm_extraction", new=AsyncMock(side_effect=_confirm)):
        result = await svc.confirm_extraction_bulk(source_ids=["ok1", "bad", "ok2"])

    assert isinstance(result, BulkConfirmExtractionResponse)
    by_id = {item.source_id: item for item in result.results}

    # ok1 and ok2 succeed; loop does NOT abort after "bad" fails
    assert by_id["ok1"].ok is True and by_id["ok1"].error is None
    assert by_id["ok2"].ok is True and by_id["ok2"].error is None
    assert by_id["bad"].ok is False and by_id["bad"].error is not None

    assert result.confirmed == 2
    assert result.failed == 1


@pytest.mark.asyncio
async def test_service_bulk_confirm_error_string_is_capped() -> None:
    """Error strings are truncated to settings.logs.error_message_preview_chars."""
    from chaoscypher_cortex.features.sources.service import SourceService

    svc = object.__new__(SourceService)
    settings_mock = MagicMock()
    settings_mock.logs.error_message_preview_chars = 10
    svc.settings = settings_mock  # type: ignore[attr-defined]

    long_error = "x" * 500

    async def _fail(*, source_id: str, **_kwargs):
        raise RuntimeError(long_error)

    with patch.object(svc, "confirm_extraction", new=AsyncMock(side_effect=_fail)):
        result = await svc.confirm_extraction_bulk(source_ids=["s1"])

    assert result.results[0].ok is False
    assert result.results[0].error is not None
    assert len(result.results[0].error) <= 10


@pytest.mark.asyncio
async def test_service_bulk_confirm_already_confirmed_is_success() -> None:
    """A benign no-op confirm (no raise) counts as ok."""
    from chaoscypher_cortex.features.sources.service import SourceService

    svc = object.__new__(SourceService)
    settings_mock = MagicMock()
    settings_mock.logs.error_message_preview_chars = 200
    svc.settings = settings_mock  # type: ignore[attr-defined]

    with patch.object(
        svc,
        "confirm_extraction",
        new=AsyncMock(return_value={"source_id": "x", "status": "indexed"}),
    ):
        result = await svc.confirm_extraction_bulk(source_ids=["x"])

    assert result.results[0].ok is True
    assert result.confirmed == 1


# ---------------------------------------------------------------------------
# Route-level: thin delegation test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_delegates_to_service_method() -> None:
    """The route calls service.confirm_extraction_bulk and returns its result."""
    expected = BulkConfirmExtractionResponse(
        confirmed=1,
        failed=0,
        results=[BulkConfirmItem(source_id="s1", ok=True)],
    )
    service = AsyncMock()
    service.confirm_extraction_bulk = AsyncMock(return_value=expected)

    body = BulkConfirmExtractionRequest(source_ids=["s1"])
    result = await confirm_extraction_bulk(_="user", service=service, request=body)

    service.confirm_extraction_bulk.assert_awaited_once_with(source_ids=["s1"])
    assert result is expected
