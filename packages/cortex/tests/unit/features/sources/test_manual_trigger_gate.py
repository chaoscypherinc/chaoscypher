# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Manual trigger POST /sources/{id}/extraction applies the unified gate:
unforced domain + no bypass -> park; forced domain -> proceed (queue).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.service import SourceService


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.priorities.background = 50
    return settings


def _make_service(engine: MagicMock, adapter: MagicMock) -> SourceService:
    return SourceService(
        engine_service=engine,
        database_name="default",
        settings=_settings(),
        storage_adapter=adapter,
    )


def _indexed_source(**extra: Any) -> dict[str, Any]:
    return {
        "id": "src_1",
        "status": "indexed",
        "filepath": "/tmp/doc.pdf",
        "file_type": "pdf",
        "filename": "doc.pdf",
        **extra,
    }


@pytest.mark.asyncio
async def test_manual_trigger_forced_domain_proceeds_to_queue() -> None:
    """A manual trigger with an explicit domain bypasses the gate and queues extraction."""
    engine = MagicMock()
    engine.get_source.return_value = _indexed_source()
    adapter = MagicMock()
    service = _make_service(engine, adapter)

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch(
            "chaoscypher_cortex.features.sources.service.gate_decision",
            return_value="proceed",
        ),
        patch(
            "chaoscypher_core.llm_queue.get_provider_factory",
            new=MagicMock(return_value=MagicMock(get_chat_provider=MagicMock())),
        ),
    ):
        mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
        result = await service.trigger_extraction(source_id="src_1", domain="medical")

    mock_queue.queue_import_analysis.assert_awaited_once()
    assert result["status"] == "extracting"


@pytest.mark.asyncio
async def test_manual_trigger_unforced_parks_instead_of_queue() -> None:
    """An unforced manual trigger on a gate-eligible source parks instead of queuing."""
    engine = MagicMock()
    engine.get_source.return_value = _indexed_source(confirmation_required=True)
    adapter = MagicMock()
    adapter.get_chunks_for_extraction.return_value = []
    service = _make_service(engine, adapter)

    _fake_domain_result = {
        "ranking": [{"domain": "general", "score": 0.4}],
        "confidence": 0.4,
        "detected_domain": "general",
        "low_confidence": True,
    }

    fake_engine_settings = MagicMock()

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch(
            "chaoscypher_cortex.features.sources.service.gate_decision",
            return_value="park",
        ),
        patch(
            "chaoscypher_cortex.features.sources.service.park_for_confirmation",
        ) as mock_park,
        patch(
            "chaoscypher_core.llm_queue.get_provider_factory",
            new=MagicMock(return_value=MagicMock(get_chat_provider=MagicMock())),
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=fake_engine_settings,
        ) as mock_build,
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ) as mock_registry,
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.create_domain_sample_text",
            return_value="sample text",
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=_fake_domain_result,
        ),
    ):
        mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
        result = await service.trigger_extraction(source_id="src_1", domain=None)

    mock_queue.queue_import_analysis.assert_not_awaited()
    mock_park.assert_called_once()
    assert result["status"] == "awaiting_confirmation"
    # Proposal-fidelity: registry must be built with the real (converted) engine
    # settings, not None. This guarantees user custom domain plugins are included,
    # matching the worker path in import_service.py.
    mock_build.assert_called_once()
    registry_call_kwargs = mock_registry.call_args
    assert registry_call_kwargs is not None, "get_domain_registry was not called"
    # First positional arg must be the converted engine settings (not None).
    passed_settings = (
        registry_call_kwargs.args[0]
        if registry_call_kwargs.args
        else registry_call_kwargs.kwargs.get("settings")
    )
    assert passed_settings is fake_engine_settings, (
        "get_domain_registry received None (built-ins only); expected full engine settings "
        "so that user custom domain plugins are included in the proposal"
    )
