# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""POST /sources/{id}/confirmation: 202 on a recorded decision (parked CAS-win
or pre-gate), 409 on a past-gate / already-confirmed / errored source. A
concurrent CAS-loss (the parked row was already flipped by another confirm) is
a benign no-op returning ``False`` from Core; sequentially re-confirming an
already-confirmed source instead raises ``ConflictError``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.extraction_api import (
    ConfirmExtractionRequest,
    confirm_extraction,
)
from chaoscypher_cortex.features.sources.service import SourceService


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.priorities.background = 50
    settings.pagination.default_page_size = 50
    return settings


def _make_service(engine: MagicMock, adapter: MagicMock | None = None) -> SourceService:
    return SourceService(
        engine_service=engine,
        database_name="default",
        settings=_settings(),
        storage_adapter=adapter or MagicMock(),
    )


def test_confirm_request_mirrors_trigger() -> None:
    """ConfirmExtractionRequest carries the same override fields as TriggerExtractionRequest."""
    req = ConfirmExtractionRequest(domain="medical", filtering_mode="strict")
    assert req.domain == "medical"
    assert req.analysis_depth == "full"
    # content_filtering is tri-state: None (default) = leave the persisted
    # upload-time value as-is; True/False are explicit overrides.
    assert req.content_filtering is None


def test_confirm_request_rejects_nonpositive_degree() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ConfirmExtractionRequest(max_entity_degree_override=0)


@pytest.mark.asyncio
async def test_confirm_endpoint_delegates_and_returns_envelope() -> None:
    service = AsyncMock()
    service.confirm_extraction = AsyncMock(return_value={"source_id": "src_1", "status": "indexed"})
    body = ConfirmExtractionRequest(domain="medical")

    result = await confirm_extraction(_="user", source_id="src_1", service=service, request=body)

    assert result == {"source_id": "src_1", "status": "indexed"}
    service.confirm_extraction.assert_awaited_once()
    kwargs = service.confirm_extraction.call_args.kwargs
    assert kwargs["source_id"] == "src_1"
    assert kwargs["domain"] == "medical"


@pytest.mark.asyncio
async def test_service_confirm_wins_cas_and_queues() -> None:
    """confirm_extraction delegates to core confirm and returns the indexed envelope."""
    engine = MagicMock()
    engine.get_source.return_value = {"id": "src_1", "status": "awaiting_confirmation"}
    adapter = MagicMock()
    service = _make_service(engine, adapter)

    with patch(
        "chaoscypher_cortex.features.sources.service.confirm_extraction_gate",
        new=AsyncMock(return_value=True),
    ) as mock_confirm:
        result = await service.confirm_extraction(source_id="src_1", domain="medical")

    assert result == {"source_id": "src_1", "status": "indexed"}
    mock_confirm.assert_awaited_once()
    call = mock_confirm.call_args.kwargs
    assert call["file_id"] == "src_1"
    assert call["chosen_domain"] == "medical"


@pytest.mark.asyncio
async def test_service_confirm_past_gate_propagates_core_conflict() -> None:
    """State-aware: the service is a thin pass-through; the past-gate 409 now
    originates in the Core gate primitive (ConflictError) and propagates.
    """
    from chaoscypher_core.exceptions import ConflictError

    engine = MagicMock()
    engine.get_source.return_value = {"id": "src_1", "status": "extracting"}
    service = _make_service(engine)

    with patch(
        "chaoscypher_cortex.features.sources.service.confirm_extraction_gate",
        new=AsyncMock(side_effect=ConflictError("too late")),
    ):
        with pytest.raises(ConflictError):
            await service.confirm_extraction(source_id="src_1", domain="medical")


@pytest.mark.asyncio
async def test_service_confirm_pre_gate_returns_actual_status() -> None:
    """Pre-gate confirm (source still ``indexed``) records the decision without
    changing status; the envelope reports the unchanged status, not a forced
    INDEXED flip (here they coincide — use ``indexing`` to prove the difference).
    """
    engine = MagicMock()
    engine.get_source.return_value = {"id": "src_1", "status": "indexing"}
    service = _make_service(engine)

    with patch(
        "chaoscypher_cortex.features.sources.service.confirm_extraction_gate",
        new=AsyncMock(return_value=True),
    ) as mock_confirm:
        result = await service.confirm_extraction(source_id="src_1", domain="medical")

    # Status is NOT forced to INDEXED — the pre-gate branch left it as-is.
    assert result == {"source_id": "src_1", "status": "indexing"}
    mock_confirm.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_confirm_concurrent_cas_loss_is_benign_noop() -> None:
    """A parked source whose CAS lost to a concurrent confirm is a benign no-op.

    Core returns ``False`` (the parked row was already flipped by another
    confirm), so the service returns the indexed envelope without raising — the
    row is already heading to extraction. A *sequential* re-confirm of an
    already-confirmed source instead raises ``ConflictError`` (see the past-gate
    test).
    """
    engine = MagicMock()
    engine.get_source.return_value = {"id": "src_1", "status": "awaiting_confirmation"}
    service = _make_service(engine)

    # core confirm returns False = CAS lost (someone else already flipped it)
    with patch(
        "chaoscypher_cortex.features.sources.service.confirm_extraction_gate",
        new=AsyncMock(return_value=False),
    ):
        result = await service.confirm_extraction(source_id="src_1", domain="medical")

    assert result == {"source_id": "src_1", "status": "indexed"}


@pytest.mark.asyncio
async def test_service_confirm_not_found_raises() -> None:
    from chaoscypher_core.exceptions import NotFoundError

    engine = MagicMock()
    engine.get_source.return_value = None
    service = _make_service(engine)

    with pytest.raises(NotFoundError):
        await service.confirm_extraction(source_id="nope", domain="medical")
