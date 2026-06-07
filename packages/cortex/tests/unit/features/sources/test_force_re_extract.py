# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: force=True on COMMITTED resets graph + source state before dispatch."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.service import SourceService


@pytest.mark.asyncio
async def test_force_re_extract_committed_resets_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force=True + COMMITTED status: graph artifacts deleted, source reset, analysis queued."""
    enqueued: list[dict[str, Any]] = []

    async def fake_queue_import_analysis(**kwargs: Any) -> str:
        enqueued.append(kwargs)
        return "tsk_1"

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_analysis",
        fake_queue_import_analysis,
    )

    # LLM provider stub — trigger_extraction calls factory.get_chat_provider() to
    # ensure a provider exists. Patch the factory so we don't need real LLM creds.
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
        "id": "src_1",
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

    result = await service.trigger_extraction(
        source_id="src_1",
        analysis_depth="full",
        domain=None,
        force=True,
    )

    # delete_source_artifacts must receive the adapter's session so all three
    # SQL deletes share the adapter's transaction (real atomicity, not just
    # documented atomicity).
    graph_repository.delete_source_artifacts.assert_called_once_with(
        "src_1", session=storage_adapter.session
    )
    storage_adapter.reset_for_re_extraction.assert_called_once_with(
        source_id="src_1",
        database_name="default",
    )
    assert len(enqueued) == 1
    assert result["status"] == "extracting"


@pytest.mark.asyncio
async def test_force_indexed_does_not_call_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """force=True on INDEXED source skips the reset path (no commit state to clear)."""
    enqueued: list[dict[str, Any]] = []

    async def fake_queue_import_analysis(**kwargs: Any) -> str:
        enqueued.append(kwargs)
        return "tsk_1"

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
        "id": "src_1",
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

    await service.trigger_extraction(
        source_id="src_1",
        analysis_depth="full",
        domain=None,
        force=True,
    )

    graph_repository.delete_source_artifacts.assert_not_called()
    storage_adapter.reset_for_re_extraction.assert_not_called()
    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_force_false_on_committed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """force=False on COMMITTED source raises ValidationError (existing behavior)."""
    from chaoscypher_core.exceptions import ValidationError

    settings = MagicMock()
    settings.priorities.background = 50

    storage_adapter = MagicMock()
    graph_repository = MagicMock()
    engine_service = MagicMock()
    engine_service.get_source.return_value = {
        "id": "src_1",
        "status": "committed",
    }

    service = SourceService.__new__(SourceService)
    service.engine_service = engine_service
    service.database_name = "default"
    service.settings = settings
    service.storage_adapter = storage_adapter
    service.graph_repository = graph_repository
    service.search_repository = None

    with pytest.raises(ValidationError):
        await service.trigger_extraction(
            source_id="src_1",
            analysis_depth="full",
            domain=None,
            force=False,
        )
