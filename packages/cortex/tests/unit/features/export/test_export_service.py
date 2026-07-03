# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ExportService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import ccx
import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphNode, GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings
from chaoscypher_core.exceptions import ExternalServiceError, ValidationError
from chaoscypher_core.services.export import CcxExporter
from chaoscypher_cortex.features.export.models import ExportResponse, ImportResponse
from chaoscypher_cortex.features.export.service import ExportService


pytestmark = pytest.mark.asyncio


def _make_settings(background_priority: int = 50, current_database: str = "default") -> Mock:
    """Build a minimal mock settings object for ExportService."""
    settings = Mock()
    settings.priorities = Mock()
    settings.priorities.background = background_priority
    settings.current_database = current_database
    return settings


@pytest.mark.unit
class TestExportService:
    """Tests for ExportService covering queue_export, queue_import, and queue_export_by_sources."""

    @pytest.mark.asyncio
    async def test_queue_export_returns_export_response(self):
        export_ops = AsyncMock()
        export_ops.queue_export = AsyncMock(return_value="task-abc-123")
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        result = await svc.queue_export()

        assert isinstance(result, ExportResponse)
        assert result.task_id == "task-abc-123"
        assert result.status == "queued"
        assert "task_id" in result.message

    @pytest.mark.asyncio
    async def test_queue_export_passes_default_options(self):
        export_ops = AsyncMock()
        export_ops.queue_export = AsyncMock(return_value="task-1")
        settings = _make_settings(background_priority=42, current_database="my-db")

        svc = ExportService(export_operations=export_ops, settings=settings)
        await svc.queue_export()

        export_ops.queue_export.assert_awaited_once_with(
            database_name="my-db",
            include_templates=True,
            include_knowledge=True,
            include_lenses=False,
            include_workflows=True,
            include_sources=True,
            include_embeddings=False,
            priority=42,
        )

    @pytest.mark.asyncio
    async def test_queue_export_with_custom_options(self):
        export_ops = AsyncMock()
        export_ops.queue_export = AsyncMock(return_value="task-custom")
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        result = await svc.queue_export(
            include_templates=False,
            include_knowledge=False,
            include_workflows=False,
            include_sources=False,
            include_embeddings=True,
        )

        assert result.task_id == "task-custom"

        call_kwargs = export_ops.queue_export.call_args.kwargs
        assert call_kwargs["include_templates"] is False
        assert call_kwargs["include_knowledge"] is False
        assert call_kwargs["include_lenses"] is False
        assert call_kwargs["include_workflows"] is False
        assert call_kwargs["include_sources"] is False
        assert call_kwargs["include_embeddings"] is True

    @pytest.mark.asyncio
    async def test_queue_export_with_embeddings_flag(self):
        export_ops = AsyncMock()
        export_ops.queue_export = AsyncMock(return_value="task-embed")
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        result = await svc.queue_export(include_embeddings=True)

        assert result.task_id == "task-embed"
        assert export_ops.queue_export.call_args.kwargs["include_embeddings"] is True

    @pytest.mark.asyncio
    async def test_queue_export_raises_when_operations_unavailable(self):
        settings = _make_settings()

        svc = ExportService(export_operations=None, settings=settings)

        with pytest.raises(ExternalServiceError):
            await svc.queue_export()

    @pytest.mark.asyncio
    async def test_queue_export_uses_background_priority(self):
        export_ops = AsyncMock()
        export_ops.queue_export = AsyncMock(return_value="task-pri")
        settings = _make_settings(background_priority=75)

        svc = ExportService(export_operations=export_ops, settings=settings)
        await svc.queue_export()

        assert export_ops.queue_export.call_args.kwargs["priority"] == 75

    @pytest.mark.asyncio
    async def test_queue_import_returns_import_response(self):
        export_ops = AsyncMock()
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "chaoscypher_cortex.features.export.service.queue_import_ccx",
                AsyncMock(return_value="import-task-456"),
            )
            result = await svc.queue_import(
                file_content=b"fake-ccx-content",
                filename="knowledge.ccx",
            )

        assert isinstance(result, ImportResponse)
        assert result.task_id == "import-task-456"
        assert result.status == "queued"
        assert "knowledge.ccx" in result.message

    @pytest.mark.asyncio
    async def test_queue_import_passes_merge_flag(self):
        export_ops = AsyncMock()
        settings = _make_settings(background_priority=30, current_database="merge-db")

        svc = ExportService(export_operations=export_ops, settings=settings)
        mock_queue_fn = AsyncMock(return_value="import-merge")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "chaoscypher_cortex.features.export.service.queue_import_ccx",
                mock_queue_fn,
            )
            result = await svc.queue_import(
                file_content=b"content",
                filename="data.ccx",
                merge=True,
            )

        assert result.task_id == "import-merge"
        mock_queue_fn.assert_awaited_once_with(
            file_content=b"content",
            database_name="merge-db",
            merge=True,
            priority=30,
            extra_metadata={"filename": "data.ccx"},
        )

    @pytest.mark.asyncio
    async def test_queue_import_default_merge_is_false(self):
        export_ops = AsyncMock()
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        mock_queue_fn = AsyncMock(return_value="import-no-merge")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "chaoscypher_cortex.features.export.service.queue_import_ccx",
                mock_queue_fn,
            )
            await svc.queue_import(file_content=b"data", filename="test.ccx")

        assert mock_queue_fn.call_args.kwargs["merge"] is False

    @pytest.mark.asyncio
    async def test_queue_import_includes_filename_in_metadata(self):
        export_ops = AsyncMock()
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        mock_queue_fn = AsyncMock(return_value="import-meta")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "chaoscypher_cortex.features.export.service.queue_import_ccx",
                mock_queue_fn,
            )
            await svc.queue_import(
                file_content=b"data",
                filename="my_graph.ccx",
            )

        extra_metadata = mock_queue_fn.call_args.kwargs["extra_metadata"]
        assert extra_metadata["filename"] == "my_graph.ccx"

    @pytest.mark.asyncio
    async def test_queue_import_rejects_legacy_cxl(self):
        """The .cxl bundle format has been replaced by .ccx (raises ValidationError)."""
        export_ops = AsyncMock()
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)

        with pytest.raises(ValidationError):
            await svc.queue_import(file_content=b"old", filename="legacy.cxl")

    @pytest.mark.asyncio
    async def test_queue_export_by_sources_returns_export_response(self):
        export_ops = AsyncMock()
        export_ops.queue_export_by_sources = AsyncMock(return_value="src-task-789")
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        result = await svc.queue_export_by_sources(
            source_ids=["src-1", "src-2"],
        )

        assert isinstance(result, ExportResponse)
        assert result.task_id == "src-task-789"
        assert result.status == "queued"
        assert "2 sources" in result.message

    @pytest.mark.asyncio
    async def test_queue_export_by_sources_passes_source_ids(self):
        export_ops = AsyncMock()
        export_ops.queue_export_by_sources = AsyncMock(return_value="src-task")
        settings = _make_settings(background_priority=60, current_database="src-db")

        svc = ExportService(export_operations=export_ops, settings=settings)
        await svc.queue_export_by_sources(
            source_ids=["a", "b", "c"],
            include_templates=False,
            include_embeddings=True,
        )

        export_ops.queue_export_by_sources.assert_awaited_once_with(
            source_ids=["a", "b", "c"],
            database_name="src-db",
            include_templates=False,
            include_embeddings=True,
            priority=60,
        )

    @pytest.mark.asyncio
    async def test_queue_export_by_sources_raises_when_operations_unavailable(self):
        settings = _make_settings()

        svc = ExportService(export_operations=None, settings=settings)

        with pytest.raises(ExternalServiceError):
            await svc.queue_export_by_sources(source_ids=["src-1"])

    @pytest.mark.asyncio
    async def test_queue_export_by_sources_defaults(self):
        export_ops = AsyncMock()
        export_ops.queue_export_by_sources = AsyncMock(return_value="src-def")
        settings = _make_settings()

        svc = ExportService(export_operations=export_ops, settings=settings)
        await svc.queue_export_by_sources(source_ids=["only-one"])

        call_kwargs = export_ops.queue_export_by_sources.call_args.kwargs
        assert call_kwargs["include_templates"] is True
        assert call_kwargs["include_embeddings"] is False


def _build_real_ccx_package(db_path: Path) -> bytes:
    """Build a genuine CCX 3.0 package via CcxExporter from a seeded graph.

    Used to prove the cortex import path consumes *real* CCX 3.0 bytes (not
    just an opaque blob): a tiny graph is seeded through the real SQLite
    adapter and exported with ``CcxExporter(...).export()``.
    """
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        assert adapter.session is not None
        session = adapter.session
        session.add(
            GraphTemplate(
                id="tpl_person",
                database_name="default",
                name="Person",
                template_type="node",
                color="#00ff00",
            )
        )
        session.flush()
        session.add(
            GraphNode(
                id="node_ada",
                database_name="default",
                graph_name="knowledge",
                template_id="tpl_person",
                label="Ada",
            )
        )
        session.commit()

        graph_repo = GraphRepository(session, "default")
        settings = build_engine_settings(get_settings())
        settings.current_database = "default"
        exporter = CcxExporter(
            graph_repository=graph_repo,
            sources_repository=None,
            settings=settings,
            workflow_db=None,
        )
        return exporter.export(
            include_sources=False,
            include_workflows=False,
            include_embeddings=False,
        )
    finally:
        adapter.disconnect()


@pytest.mark.unit
class TestExportServiceCcx30:
    """The cortex import path consumes real CCX 3.0 package bytes."""

    @pytest.mark.asyncio
    async def test_queue_import_accepts_real_ccx_30_package(self, tmp_path: Path):
        """A package built by CcxExporter validates as 3.0 and is accepted for import."""
        data = _build_real_ccx_package(tmp_path / "export.db")

        # Sanity: the bytes really are a conformant CCX 3.0 package.
        report = ccx.open_package(data).validate()
        assert report.ok, report.errors
        assert "core" in report.classes

        export_ops = AsyncMock()
        settings = Mock()
        settings.priorities = Mock()
        settings.priorities.background = 50
        settings.current_database = "default"
        svc = ExportService(export_operations=export_ops, settings=settings)

        with pytest.MonkeyPatch.context() as mp:
            queue_fn = AsyncMock(return_value="import-ccx30")
            mp.setattr(
                "chaoscypher_cortex.features.export.service.queue_import_ccx",
                queue_fn,
            )
            result = await svc.queue_import(file_content=data, filename="export.ccx")

        assert isinstance(result, ImportResponse)
        assert result.task_id == "import-ccx30"
        # The genuine 3.0 bytes are forwarded verbatim to the queue.
        assert queue_fn.call_args.kwargs["file_content"] == data
