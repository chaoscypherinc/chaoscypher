# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reclassify action: change a source's domain after upload."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.extraction_api import ReclassifyRequest
from chaoscypher_cortex.features.sources.service import SourceService


def test_reclassify_request_model() -> None:
    """ReclassifyRequest is a Pydantic model with a domain field."""
    req = ReclassifyRequest(domain="research")
    assert req.domain == "research"


def test_reclassify_request_rejects_missing_domain() -> None:
    """ReclassifyRequest requires domain."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReclassifyRequest()  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_reclassify_indexed_source_queues_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reclassify on an indexed source queues extraction with the new domain."""
    enqueued: list[dict[str, Any]] = []

    async def fake_queue_import_analysis(**kwargs: Any) -> str:
        enqueued.append(kwargs)
        return "tsk_reclassify"

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_analysis",
        fake_queue_import_analysis,
    )

    monkeypatch.setattr(
        "chaoscypher_core.llm_queue.get_provider_factory",
        lambda: MagicMock(get_chat_provider=MagicMock()),
    )

    settings = MagicMock()
    settings.priorities.background = 50

    storage_adapter = MagicMock()
    graph_repository = MagicMock()
    engine_service = MagicMock()
    engine_service.get_source.return_value = {
        "id": "src_rc",
        "status": "indexed",
        "filepath": "/tmp/doc.pdf",
        "file_type": "pdf",
        "filename": "doc.pdf",
    }

    service = SourceService.__new__(SourceService)
    service.engine_service = engine_service
    service.database_name = "default"
    service.settings = settings
    service.storage_adapter = storage_adapter
    service.graph_repository = graph_repository
    service.search_repository = None

    result = await service.reclassify_source(
        source_id="src_rc",
        domain="medical",
    )

    assert len(enqueued) == 1
    assert result["source_id"] == "src_rc"
    # The domain must be passed through to the queue payload
    queued_file_info = enqueued[0]["file_info"]
    assert queued_file_info["forced_domain"] == "medical"


@pytest.mark.asyncio
async def test_reclassify_committed_source_resets_and_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reclassify on a committed source resets graph artifacts and re-queues."""
    enqueued: list[dict[str, Any]] = []

    async def fake_queue_import_analysis(**kwargs: Any) -> str:
        enqueued.append(kwargs)
        return "tsk_reclassify_committed"

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_analysis",
        fake_queue_import_analysis,
    )

    monkeypatch.setattr(
        "chaoscypher_core.llm_queue.get_provider_factory",
        lambda: MagicMock(get_chat_provider=MagicMock()),
    )

    settings = MagicMock()
    settings.priorities.background = 50

    storage_adapter = MagicMock()
    graph_repository = MagicMock()
    graph_repository.delete_source_artifacts.return_value = {
        "nodes_deleted": 5,
        "edges_deleted": 3,
        "templates_deleted": 1,
    }
    engine_service = MagicMock()
    engine_service.get_source.return_value = {
        "id": "src_rc2",
        "status": "committed",
        "filepath": "/tmp/doc.pdf",
        "file_type": "pdf",
        "filename": "doc.pdf",
    }

    service = SourceService.__new__(SourceService)
    service.engine_service = engine_service
    service.database_name = "default"
    service.settings = settings
    service.storage_adapter = storage_adapter
    service.graph_repository = graph_repository
    service.search_repository = None

    result = await service.reclassify_source(
        source_id="src_rc2",
        domain="legal",
    )

    # Graph artifacts must be deleted before re-queuing
    graph_repository.delete_source_artifacts.assert_called_once_with(
        "src_rc2", session=storage_adapter.session
    )
    storage_adapter.reset_for_re_extraction.assert_called_once_with(
        source_id="src_rc2",
        database_name="default",
    )
    assert len(enqueued) == 1
    assert result["source_id"] == "src_rc2"
    queued_file_info = enqueued[0]["file_info"]
    assert queued_file_info["forced_domain"] == "legal"


@pytest.mark.asyncio
async def test_reclassify_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclassify on a non-existent source raises NotFoundError."""
    from chaoscypher_core.exceptions import NotFoundError

    monkeypatch.setattr(
        "chaoscypher_core.llm_queue.get_provider_factory",
        lambda: MagicMock(get_chat_provider=MagicMock()),
    )

    engine_service = MagicMock()
    engine_service.get_source.return_value = None

    service = SourceService.__new__(SourceService)
    service.engine_service = engine_service
    service.database_name = "default"
    service.settings = MagicMock()
    service.storage_adapter = MagicMock()
    service.graph_repository = MagicMock()
    service.search_repository = None

    with pytest.raises(NotFoundError):
        await service.reclassify_source(source_id="no_such", domain="generic")
