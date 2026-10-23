# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: force=True on COMMITTED resets graph + source state before dispatch."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ExternalServiceError
from chaoscypher_core.models import ReleasedRows
from chaoscypher_cortex.features.sources.service import SourceService


@pytest.fixture(autouse=True)
def graph(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """The repository force_re_extract builds on the adapter's own session."""
    repo = MagicMock()
    repo.release_source.return_value = ReleasedRows(deleted_node_ids=frozenset({"n1"}))
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.management.re_extraction."
        "get_transaction_graph_repository",
        lambda adapter, _database_name: repo,
    )
    return repo


@pytest.mark.asyncio
async def test_force_re_extract_committed_resets_state(
    monkeypatch: pytest.MonkeyPatch,
    graph: MagicMock,
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

    graph.release_source.assert_called_once_with("src_1")
    storage_adapter.reset_for_re_extraction.assert_called_once_with(
        source_id="src_1",
        database_name="default",
    )
    assert len(enqueued) == 1
    assert result["status"] == "extracting"


@pytest.mark.asyncio
async def test_force_indexed_does_not_call_reset(
    monkeypatch: pytest.MonkeyPatch, graph: MagicMock
) -> None:
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

    graph.release_source.assert_not_called()
    storage_adapter.reset_for_re_extraction.assert_not_called()
    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_force_false_on_committed_raises(
    monkeypatch: pytest.MonkeyPatch, graph: MagicMock
) -> None:
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


@pytest.mark.asyncio
async def test_provider_outage_does_not_destroy_the_committed_graph(
    monkeypatch: pytest.MonkeyPatch,
    graph: MagicMock,
) -> None:
    """An unreachable LLM provider must leave the committed graph intact.

    Regression for a P1 (hunt queue, 2026-08-04). In ``trigger_extraction`` the
    LLM-provider preflight used to run *after* ``force_re_extract``.
    ``force_re_extract`` releases the source's nodes, edges and templates and
    commits -- its atomicity guarantee covers only its own two writes, not the
    caller's later failure points. So ``force=True`` on a COMMITTED source with
    no reachable provider destroyed the subgraph, then raised
    ``ExternalServiceError`` with nothing queued: the graph was gone and the
    response said extraction could not start.

    The load-bearing assertion is ``release_source`` never being
    called. Asserting only that the call raises passes with the bug present --
    it raised either way.
    """
    enqueued: list[dict[str, Any]] = []

    async def fake_queue_import_analysis(**kwargs: Any) -> str:
        enqueued.append(kwargs)
        return "tsk_1"

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_analysis",
        fake_queue_import_analysis,
    )

    # The outage: resolving a chat provider raises.
    def _broken_factory() -> MagicMock:
        factory = MagicMock()
        factory.get_chat_provider.side_effect = RuntimeError("no provider configured")
        return factory

    monkeypatch.setattr("chaoscypher_core.llm_queue.get_provider_factory", _broken_factory)

    settings = MagicMock()
    settings.priorities.background = 50

    storage_adapter = MagicMock()
    graph_repository = MagicMock()
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

    with pytest.raises(ExternalServiceError):
        await service.trigger_extraction(
            source_id="src_1",
            analysis_depth="full",
            domain=None,
            force=True,
        )

    graph.release_source.assert_not_called()
    storage_adapter.reset_for_re_extraction.assert_not_called()
    assert enqueued == []
