# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for ExportOperationsService.

Target: chaoscypher_core.operations.export_operations_service

Exercises the queue-enqueue methods (metadata assembly, data payloads) and
the two async operation handlers (base64 encoding, event emission, repository
wiring). All heavy collaborators are MagicMock/AsyncMock; lazy imports inside
the handlers are patched at their source paths.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import ccx
import pytest

from chaoscypher_core.operations.export_operations_service import ExportOperationsService


def _real_ccx_bytes() -> bytes:
    """Build a minimal but valid CCX 3.0 package for contract assertions."""
    builder = ccx.PackageBuilder(
        name="chaoscypher/test",
        package_version="1.0.0",
        license="CC-BY-4.0",
        base_iri="urn:ccx:chaoscypher:",
    )
    builder.add_graph(
        "ccx",
        "knowledge",
        {"@graph": [{"@id": "urn:ccx:chaoscypher:node/n1", "@type": "Person", "name": "Alice"}]},
        role="default",
    )
    return builder.build()


def _make_service() -> ExportOperationsService:
    return ExportOperationsService(
        workflow_db=MagicMock(),
    )


class TestInitAndRegistration:
    """Constructor wiring and handler registration."""

    def test_handlers_present_and_retry_on_crash(self) -> None:
        service = _make_service()
        assert set(service.operation_handlers) == {"export_graph", "export_by_sources"}
        for spec in service.operation_handlers.values():
            assert spec.retry_on_crash is True

    def test_register_handlers_delegates_to_queue_client(self) -> None:
        service = _make_service()
        mock_client = MagicMock()
        with patch(
            "chaoscypher_core.operations.export_operations_service.queue_client",
            mock_client,
        ):
            service.register_handlers()
        mock_client.register_handlers.assert_called_once()
        # Second positional arg is the handler dict.
        args, _ = mock_client.register_handlers.call_args
        assert args[1] is service.operation_handlers


class TestQueueExport:
    """queue_export assembles metadata and the data payload."""

    @pytest.mark.asyncio
    async def test_queue_export_default_metadata_and_payload(self) -> None:
        service = _make_service()
        mock_client = MagicMock()
        mock_client.enqueue_task = AsyncMock(return_value="task-123")
        with patch(
            "chaoscypher_core.operations.export_operations_service.queue_client",
            mock_client,
        ):
            task_id = await service.queue_export(database_name="db1")

        assert task_id == "task-123"
        _, kwargs = mock_client.enqueue_task.call_args
        assert kwargs["operation"] == "export_graph"
        assert kwargs["priority"] == 50
        assert kwargs["metadata"]["database_name"] == "db1"
        assert kwargs["metadata"]["operation_type"] == "export_graph"
        data = kwargs["data"]
        assert data["include_templates"] is True
        assert data["include_embeddings"] is False
        assert data["lens_id"] is None

    @pytest.mark.asyncio
    async def test_queue_export_merges_extra_metadata(self) -> None:
        service = _make_service()
        mock_client = MagicMock()
        mock_client.enqueue_task = AsyncMock(return_value="t")
        with patch(
            "chaoscypher_core.operations.export_operations_service.queue_client",
            mock_client,
        ):
            await service.queue_export(
                database_name="db2",
                include_embeddings=True,
                lens_id="lens-9",
                title="My Snapshot",
                priority=80,
                extra_metadata={"source_app": "cli", "database_name": "ignored"},
            )

        _, kwargs = mock_client.enqueue_task.call_args
        meta = kwargs["metadata"]
        assert meta["source_app"] == "cli"
        # database_name/operation_type are forcibly re-set after the merge.
        assert meta["database_name"] == "db2"
        assert meta["operation_type"] == "export_graph"
        assert kwargs["priority"] == 80
        assert kwargs["data"]["include_embeddings"] is True
        assert kwargs["data"]["lens_id"] == "lens-9"
        assert kwargs["data"]["title"] == "My Snapshot"


class TestQueueExportBySources:
    """queue_export_by_sources assembles metadata with source_count."""

    @pytest.mark.asyncio
    async def test_payload_and_metadata(self) -> None:
        service = _make_service()
        mock_client = MagicMock()
        mock_client.enqueue_task = AsyncMock(return_value="t-src")
        with patch(
            "chaoscypher_core.operations.export_operations_service.queue_client",
            mock_client,
        ):
            task_id = await service.queue_export_by_sources(
                ["s1", "s2", "s3"],
                database_name="db3",
                include_templates=False,
                include_embeddings=True,
                title="Filtered",
                extra_metadata={"k": "v"},
            )

        assert task_id == "t-src"
        _, kwargs = mock_client.enqueue_task.call_args
        assert kwargs["operation"] == "export_by_sources"
        meta = kwargs["metadata"]
        assert meta["source_count"] == 3
        assert meta["database_name"] == "db3"
        assert meta["operation_type"] == "export_by_sources"
        assert meta["k"] == "v"
        data = kwargs["data"]
        assert data["source_ids"] == ["s1", "s2", "s3"]
        assert data["include_templates"] is False
        assert data["include_embeddings"] is True


def _patch_handler_deps(
    *,
    ccx_bytes: bytes,
    filename: str,
) -> Any:
    """Build the patch context for a handler's lazy imports.

    Returns a tuple of (context_managers list, mock_event_bus, mock_exporter).
    The mocked CcxExporter.export() returns ``ccx_bytes`` for both handlers.
    """
    settings = MagicMock()
    settings.current_database = "current-db"

    # The export handlers build the EngineSettings view at the operation
    # boundary and hand THAT (not the app singleton) to CcxExporter, so we
    # patch ``build_engine_settings`` to return a real EngineSettings carrying
    # the same current_database the adapter is scoped by.
    from chaoscypher_core.settings import EngineSettings

    engine_settings = EngineSettings(current_database="current-db")

    exporter = MagicMock()
    exporter.export.return_value = ccx_bytes
    exporter.get_export_filename.return_value = filename

    mock_get_settings = MagicMock(return_value=settings)
    mock_build_engine = MagicMock(return_value=engine_settings)
    mock_get_adapter = MagicMock(return_value=MagicMock())
    mock_exporter_cls = MagicMock(return_value=exporter)
    mock_event_bus = MagicMock()

    cms = [
        patch(
            "chaoscypher_core.app_config.get_settings",
            mock_get_settings,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            mock_build_engine,
        ),
        patch(
            "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
            mock_get_adapter,
        ),
        patch(
            "chaoscypher_core.repo_factories.get_graph_repository",
            MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.export.CcxExporter",
            mock_exporter_cls,
        ),
        patch(
            "chaoscypher_core.operations.export_operations_service.event_bus",
            mock_event_bus,
        ),
    ]
    return cms, mock_event_bus, exporter


class TestExportGraphHandler:
    """_export_graph_handler executes export, encodes, and emits."""

    @pytest.mark.asyncio
    async def test_returns_encoded_content_and_emits(self) -> None:
        service = _make_service()
        ccx_bytes = _real_ccx_bytes()
        cms, mock_event_bus, exporter = _patch_handler_deps(
            ccx_bytes=ccx_bytes,
            filename="graph_export.ccx",
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._export_graph_handler(
                data={"include_knowledge": True, "lens_id": "L1", "title": "T"},
                metadata=None,
                task_id="task-x",
            )

        assert result["filename"] == "graph_export.ccx"
        assert result["size_bytes"] == len(ccx_bytes)
        # The base64 payload decodes to bytes that open + validate as CCX 3.0.
        decoded = base64.b64decode(result["content"])
        assert decoded == ccx_bytes
        assert ccx.open_package(decoded).validate().ok

        # CcxExporter.export called with config from data.
        _, kwargs = exporter.export.call_args
        assert kwargs["include_knowledge"] is True
        assert kwargs["lens_id"] == "L1"
        assert kwargs["title"] == "T"

        # task_completed event emitted with worker source.
        mock_event_bus.emit.assert_called_once()
        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_completed"
        assert ev_kwargs["source"] == "worker"
        assert ev_kwargs["details"]["filename"] == "graph_export.ccx"

    @pytest.mark.asyncio
    async def test_defaults_when_data_empty(self) -> None:
        service = _make_service()
        cms, _bus, exporter = _patch_handler_deps(
            ccx_bytes=_real_ccx_bytes(),
            filename="f.ccx",
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            await service._export_graph_handler(data={})

        _, kwargs = exporter.export.call_args
        assert kwargs["include_templates"] is True
        assert kwargs["include_embeddings"] is False
        assert kwargs["lens_id"] is None


class TestExportBySourcesHandler:
    """_export_by_sources_handler executes source-filtered export."""

    @pytest.mark.asyncio
    async def test_returns_encoded_content_with_prefixed_filename(self) -> None:
        service = _make_service()
        ccx_bytes = _real_ccx_bytes()
        cms, mock_event_bus, exporter = _patch_handler_deps(
            ccx_bytes=ccx_bytes,
            filename="base.ccx",
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._export_by_sources_handler(
                data={"source_ids": ["a", "b"], "include_templates": False},
                metadata={"x": 1},
                task_id="t",
            )

        # Filename is prefixed with sources_export_<count>_.
        assert result["filename"] == "sources_export_2_base.ccx"
        assert result["size_bytes"] == len(ccx_bytes)
        decoded = base64.b64decode(result["content"])
        assert decoded == ccx_bytes
        assert ccx.open_package(decoded).validate().ok

        _, kwargs = exporter.export.call_args
        assert kwargs["source_ids"] == ["a", "b"]
        assert kwargs["include_templates"] is False

        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_completed"
        assert ev_kwargs["details"]["source_count"] == 2

    @pytest.mark.asyncio
    async def test_empty_source_ids_default(self) -> None:
        service = _make_service()
        cms, _bus, exporter = _patch_handler_deps(
            ccx_bytes=_real_ccx_bytes(),
            filename="b.ccx",
        )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await service._export_by_sources_handler(data={})

        assert result["filename"] == "sources_export_0_b.ccx"
        _, kwargs = exporter.export.call_args
        assert kwargs["source_ids"] == []
