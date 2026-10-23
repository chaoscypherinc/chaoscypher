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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "forced_packs", "forced_domain"),
    [
        ({"packs": ["literary", "historical"]}, ["literary", "historical"], "literary"),
        ({"packs": []}, [], "generic"),  # the floor alone, chosen
        ({"domain": "medical"}, ["medical"], "medical"),  # the single-select UI's form
        ({"domain": "__auto__"}, None, None),  # detect
    ],
)
async def test_manual_trigger_queues_the_forced_packs(
    kwargs: dict[str, Any], forced_packs: list[str] | None, forced_domain: str | None
) -> None:
    """The queue payload carries the forced packs and their first as the old ``forced_domain``."""
    engine = MagicMock()
    engine.get_source.return_value = _indexed_source()
    adapter = MagicMock()
    service = _make_service(engine, adapter)
    seen: dict[str, Any] = {}

    def _gate(source: dict[str, Any]) -> str:
        seen["gate_source"] = source
        return "proceed"

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch("chaoscypher_cortex.features.sources.service.gate_decision", side_effect=_gate),
        patch(
            "chaoscypher_core.llm_queue.get_provider_factory",
            new=MagicMock(return_value=MagicMock(get_chat_provider=MagicMock())),
        ),
    ):
        mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
        await service.trigger_extraction(source_id="src_1", **kwargs)

    file_info = mock_queue.queue_import_analysis.call_args.kwargs["file_info"]
    assert file_info["forced_packs"] == forced_packs
    assert file_info["forced_domain"] == forced_domain
    if forced_packs is not None:  # the gate sees the forced choice and proceeds
        assert seen["gate_source"]["forced_packs"] == forced_packs


@pytest.mark.asyncio
async def test_manual_trigger_naming_the_packs_twice_is_refused_before_anything_runs() -> None:
    from chaoscypher_core.exceptions import ValidationError

    engine = MagicMock()
    adapter = MagicMock()
    service = _make_service(engine, adapter)
    with pytest.raises(ValidationError, match="not both"):
        await service.trigger_extraction(source_id="src_1", domain="legal", packs=["medical"])
    engine.get_source.assert_not_called()


@pytest.mark.asyncio
async def test_reclassify_forwards_the_packs() -> None:
    """Reclassify is trigger_extraction with force=True and the packs forced."""
    engine = MagicMock()
    engine.get_source.return_value = _indexed_source()
    service = _make_service(engine, MagicMock())
    service.trigger_extraction = AsyncMock(return_value={"status": "extracting"})  # type: ignore[method-assign]

    await service.reclassify_source(source_id="src_1", packs=["legal", "medical"])

    call = service.trigger_extraction.call_args.kwargs
    assert call["packs"] == ["legal", "medical"]
    assert call["force"] is True


def test_the_request_models_read_packs_and_domain_alike() -> None:
    """``packs`` is the list; ``domain`` one element; reclassify needs one of them."""
    from pydantic import ValidationError as PydanticValidationError

    from chaoscypher_cortex.features.sources.extraction_api import (
        ConfirmExtractionRequest,
        ReclassifyRequest,
        TriggerExtractionRequest,
    )
    from chaoscypher_cortex.features.sources.models import UrlImportRequest

    assert TriggerExtractionRequest(domain="legal").forced_packs() == ("legal",)
    assert TriggerExtractionRequest(packs=["b", "a"]).forced_packs() == ("b", "a")  # given order
    assert TriggerExtractionRequest().forced_packs() is None
    assert ConfirmExtractionRequest(packs=[]).forced_packs() == ()
    assert ReclassifyRequest(domain="generic").forced_packs() == ()
    assert UrlImportRequest(url="https://x.test", packs=["news"]).forced_packs() == ("news",)
    with pytest.raises(PydanticValidationError, match="needs 'packs'"):
        ReclassifyRequest()
