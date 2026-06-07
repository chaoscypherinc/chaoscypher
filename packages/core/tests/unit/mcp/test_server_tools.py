# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP server tool handler functions.

Covers the module-level handler functions in ``chaoscypher_core.mcp.server``
that are not exercised by the processor or bridge test suites:

- ``_handle_remove_document``
- ``_handle_wait_for_document``
- ``_handle_document_status`` (lists sources with stage_progress)
- ``_handle_extraction_tool``
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.server import (
    _handle_document_status,
    _handle_extraction_tool,
    _handle_remove_document,
    _handle_wait_for_document,
)


# ------------------------------------------------------------------ #
#  TestHandleConfirmExtraction
# ------------------------------------------------------------------ #


class TestHandleConfirmExtraction:
    """Tests for _handle_confirm_extraction handler."""

    @pytest.mark.asyncio
    async def test_missing_file_id_returns_error(self, mock_engine):
        from chaoscypher_core.mcp.server import _handle_confirm_extraction

        result = _parse_result(await _handle_confirm_extraction(mock_engine, {}))

        assert result["success"] is False
        assert "file_id is required" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_confirm_returns_success(self, mock_engine):
        from chaoscypher_core.mcp.server import _handle_confirm_extraction

        with patch(
            "chaoscypher_core.mcp.server.confirm_extraction", return_value=True
        ) as mock_confirm:
            result = _parse_result(
                await _handle_confirm_extraction(
                    mock_engine,
                    {"file_id": "src_1", "domain": "medical"},
                )
            )

        assert result["success"] is True
        assert result["source_id"] == "src_1"
        assert result["domain"] == "medical"
        # adapter, file_id, chosen_domain, overrides
        args = mock_confirm.call_args.args
        assert args[0] is mock_engine.storage_adapter
        assert args[1] == "src_1"
        assert args[2] == "medical"

    @pytest.mark.asyncio
    async def test_overrides_forwarded(self, mock_engine):
        from chaoscypher_core.mcp.server import _handle_confirm_extraction

        with patch(
            "chaoscypher_core.mcp.server.confirm_extraction", return_value=True
        ) as mock_confirm:
            await _handle_confirm_extraction(
                mock_engine,
                {
                    "file_id": "src_1",
                    "analysis_depth": "quick",
                    "filtering_mode": "aggressive",
                    "protect_orphans": True,
                },
            )

        overrides = mock_confirm.call_args.args[3]
        assert overrides["analysis_depth"] == "quick"
        assert overrides["filtering_mode"] == "aggressive"
        assert overrides["protect_orphans"] is True
        # file_id and domain are NOT in the overrides dict
        assert "file_id" not in overrides
        assert "domain" not in overrides

    @pytest.mark.asyncio
    async def test_cas_loss_returns_conflict(self, mock_engine):
        from chaoscypher_core.mcp.server import _handle_confirm_extraction

        with patch("chaoscypher_core.mcp.server.confirm_extraction", return_value=False):
            result = _parse_result(
                await _handle_confirm_extraction(mock_engine, {"file_id": "src_1"})
            )

        assert result["success"] is False
        assert result["error_code"] == "NOT_AWAITING_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_no_domain_omits_chosen_domain(self, mock_engine):
        from chaoscypher_core.mcp.server import _handle_confirm_extraction

        with patch(
            "chaoscypher_core.mcp.server.confirm_extraction", return_value=True
        ) as mock_confirm:
            await _handle_confirm_extraction(mock_engine, {"file_id": "src_1"})

        # chosen_domain is None when the client accepts the recommendation.
        assert mock_confirm.call_args.args[2] is None


# ------------------------------------------------------------------ #
#  TestConfirmExtractionReadModeRejection
# ------------------------------------------------------------------ #


class TestConfirmExtractionReadModeRejection:
    """confirm_extraction is gated out of read mode by the write_only flag.

    The call_tool gate at server.py:119-128 builds ``write_only_tools``
    from ``{t.name for t in TOOL_DEFINITIONS if t.write_only}`` and returns
    ``NOT_AUTHORIZED`` before the handler is reached.  This class locks in
    that contract so any regression (e.g. removing ``write_only=True`` from
    the tool definition, or reordering the gate below the dispatch branch)
    will fail loudly.
    """

    @pytest.mark.asyncio
    async def test_read_mode_rejects_confirm_extraction(self):
        """call_tool returns NOT_AUTHORIZED and never invokes the handler."""
        import mcp.types

        from chaoscypher_core.mcp.server import create_mcp_server

        engine = MagicMock()
        engine.settings.mcp.mode = "read"
        engine.settings.mcp.auto_extract = False
        engine.settings.mcp.completed_history_limit = 20
        engine.settings.current_database = "default"
        engine.embedding_service = None

        server = create_mcp_server(engine)
        handler = server.request_handlers[mcp.types.CallToolRequest]

        with patch("chaoscypher_core.mcp.server._handle_confirm_extraction") as mock_handler:
            req = mcp.types.CallToolRequest(
                method="tools/call",
                params=mcp.types.CallToolRequestParams(
                    name="confirm_extraction",
                    arguments={"file_id": "src_1"},
                ),
            )
            server_result = await handler(req)

        # The handler must never run in read mode.
        mock_handler.assert_not_called()
        payload = json.loads(server_result.root.content[0].text)
        assert payload["success"] is False
        assert payload["error_code"] == "NOT_AUTHORIZED"

    @pytest.mark.asyncio
    async def test_write_mode_dispatches_to_handler(self):
        """In write mode the gate is skipped and the handler is reached.

        This is the positive-path guard: if the gate accidentally fires in
        write mode, this test fails immediately.
        """
        import mcp.types

        from chaoscypher_core.mcp.server import create_mcp_server

        engine = MagicMock()
        engine.settings.mcp.mode = "write"
        engine.settings.mcp.auto_extract = False
        engine.settings.mcp.completed_history_limit = 20
        engine.settings.current_database = "default"
        engine.embedding_service = None

        server = create_mcp_server(engine)
        handler = server.request_handlers[mcp.types.CallToolRequest]

        with patch(
            "chaoscypher_core.mcp.server._handle_confirm_extraction",
            return_value=[],
        ) as mock_handler:
            req = mcp.types.CallToolRequest(
                method="tools/call",
                params=mcp.types.CallToolRequestParams(
                    name="confirm_extraction",
                    arguments={"file_id": "src_1"},
                ),
            )
            await handler(req)

        # The handler must be called in write mode.
        mock_handler.assert_called_once()

    def test_confirm_extraction_is_write_only_in_tool_definitions(self):
        """Static invariant: write_only=True on the confirm_extraction definition."""
        from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS

        write_only_names = {t.name for t in TOOL_DEFINITIONS if t.write_only}
        assert "confirm_extraction" in write_only_names, (
            "confirm_extraction must have write_only=True in TOOL_DEFINITIONS "
            "so the call_tool gate rejects it in read mode"
        )


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #


def _parse_result(text_contents: list) -> dict:
    """Extract the JSON dict from a list[TextContent] response."""
    assert len(text_contents) == 1
    assert text_contents[0].type == "text"
    return json.loads(text_contents[0].text)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def mock_engine():
    """Create a mock Engine with storage adapter, graph, and search repos."""
    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.storage_adapter = MagicMock()
    engine.graph_repository = MagicMock()
    engine.search_repository = MagicMock()
    return engine


@pytest.fixture
def mock_doc_processor():
    """Create a mock DocumentProcessor with pending/completed helpers."""
    proc = MagicMock()
    proc.has_pending = MagicMock(return_value=False)
    proc.get_completed = MagicMock(return_value=None)
    proc.wait_for_completion = AsyncMock(return_value={"status": "committed", "file_id": "f1"})
    proc.get_status = MagicMock(
        return_value={
            "current": None,
            "queued": [],
            "completed": [],
        }
    )
    return proc


@pytest.fixture
def mock_index_only_processor():
    """Create a mock index-only DocumentProcessor."""
    proc = MagicMock()
    proc.has_pending = MagicMock(return_value=False)
    proc.get_completed = MagicMock(return_value=None)
    proc.wait_for_completion = AsyncMock(return_value={"status": "indexed", "file_id": "f2"})
    return proc


# ------------------------------------------------------------------ #
#  TestHandleRemoveDocument
# ------------------------------------------------------------------ #


class TestHandleRemoveDocument:
    """Tests for _handle_remove_document handler."""

    @pytest.mark.asyncio
    async def test_missing_source_id_returns_error(self, mock_engine):
        """Empty args yields a 'source_id is required' error."""
        result = _parse_result(await _handle_remove_document(mock_engine, {}))

        assert result["success"] is False
        assert "source_id is required" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_source_id_returns_error(self, mock_engine):
        """Explicitly empty source_id yields a 'source_id is required' error."""
        result = _parse_result(await _handle_remove_document(mock_engine, {"source_id": ""}))

        assert result["success"] is False
        assert "source_id is required" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_delete(self, mock_engine):
        """When SourceService.delete_source returns True, success is True."""
        with patch(
            "chaoscypher_core.services.graph.management.source.SourceService"
        ) as mock_sms_cls:
            mock_sms = MagicMock()
            mock_sms.delete_source.return_value = True
            mock_sms_cls.return_value = mock_sms

            result = _parse_result(
                await _handle_remove_document(mock_engine, {"source_id": "src_123"})
            )

        assert result["success"] is True
        assert result["source_id"] == "src_123"
        assert "error" not in result
        mock_sms.delete_source.assert_called_once_with(
            "src_123",
            graph_repo=mock_engine.graph_repository,
            search_repo=mock_engine.search_repository,
        )

    @pytest.mark.asyncio
    async def test_source_not_found(self, mock_engine):
        """When SourceService.delete_source returns False, error is 'Source not found'."""
        with patch(
            "chaoscypher_core.services.graph.management.source.SourceService"
        ) as mock_sms_cls:
            mock_sms = MagicMock()
            mock_sms.delete_source.return_value = False
            mock_sms_cls.return_value = mock_sms

            result = _parse_result(
                await _handle_remove_document(mock_engine, {"source_id": "nonexistent"})
            )

        assert result["success"] is False
        assert result["source_id"] == "nonexistent"
        assert result["error"] == "Source not found"

    @pytest.mark.asyncio
    async def test_exception_returns_operation_failed(self, mock_engine):
        """When SourceService raises, error is 'Operation failed'."""
        with patch(
            "chaoscypher_core.services.graph.management.source.SourceService"
        ) as mock_sms_cls:
            mock_sms_cls.side_effect = RuntimeError("db locked")

            result = _parse_result(
                await _handle_remove_document(mock_engine, {"source_id": "src_fail"})
            )

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_delete_source_exception_returns_operation_failed(self, mock_engine):
        """When delete_source itself raises, error is 'Operation failed'."""
        with patch(
            "chaoscypher_core.services.graph.management.source.SourceService"
        ) as mock_sms_cls:
            mock_sms = MagicMock()
            mock_sms.delete_source.side_effect = ValueError("cascade failure")
            mock_sms_cls.return_value = mock_sms

            result = _parse_result(
                await _handle_remove_document(mock_engine, {"source_id": "src_err"})
            )

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_service_constructed_with_engine_params(self, mock_engine):
        """SourceService is created with engine storage adapter and database name."""
        with patch(
            "chaoscypher_core.services.graph.management.source.SourceService"
        ) as mock_sms_cls:
            mock_sms = MagicMock()
            mock_sms.delete_source.return_value = True
            mock_sms_cls.return_value = mock_sms

            await _handle_remove_document(mock_engine, {"source_id": "src_1"})

        mock_sms_cls.assert_called_once_with(
            repository=mock_engine.storage_adapter,
            database_name="default",
        )


# ------------------------------------------------------------------ #
#  TestHandleWaitForDocument
# ------------------------------------------------------------------ #


class TestHandleWaitForDocument:
    """Tests for _handle_wait_for_document handler."""

    @pytest.mark.asyncio
    async def test_missing_file_id_returns_error(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """Empty args yields a 'file_id is required' error."""
        result = _parse_result(
            await _handle_wait_for_document(mock_doc_processor, mock_index_only_processor, {})
        )

        assert result["success"] is False
        assert "file_id is required" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_file_id_returns_error(self, mock_doc_processor, mock_index_only_processor):
        """Explicitly empty file_id yields a 'file_id is required' error."""
        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor, mock_index_only_processor, {"file_id": ""}
            )
        )

        assert result["success"] is False
        assert "file_id is required" in result["error"]

    @pytest.mark.asyncio
    async def test_not_found_in_any_processor(self, mock_doc_processor, mock_index_only_processor):
        """When no processor has the file, error contains 'not found'."""
        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor,
                mock_index_only_processor,
                {"file_id": "unknown_id"},
            )
        )

        assert result["success"] is False
        assert "unknown_id" in result["error"]
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_found_pending_in_index_only_processor(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """When the file is pending in index_only_processor, waits and returns result."""
        mock_index_only_processor.has_pending.return_value = True
        mock_index_only_processor.wait_for_completion.return_value = {
            "status": "indexed",
            "file_id": "f_idx",
        }

        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor,
                mock_index_only_processor,
                {"file_id": "f_idx"},
            )
        )

        assert result["success"] is True
        assert result["status"] == "indexed"
        assert result["file_id"] == "f_idx"
        mock_index_only_processor.wait_for_completion.assert_awaited_once_with("f_idx", 300)

    @pytest.mark.asyncio
    async def test_found_pending_in_doc_processor(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """When the file is pending in doc_processor, waits and returns result."""
        mock_doc_processor.has_pending.return_value = True
        mock_doc_processor.wait_for_completion.return_value = {
            "status": "committed",
            "file_id": "f_doc",
        }

        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor,
                mock_index_only_processor,
                {"file_id": "f_doc"},
            )
        )

        assert result["success"] is True
        assert result["status"] == "committed"
        assert result["file_id"] == "f_doc"
        mock_doc_processor.wait_for_completion.assert_awaited_once_with("f_doc", 300)

    @pytest.mark.asyncio
    async def test_found_completed_in_doc_processor(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """When the file is already completed in doc_processor, returns immediately."""
        mock_doc_processor.get_completed.return_value = {
            "status": "committed",
            "file_id": "f_done",
        }

        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor,
                mock_index_only_processor,
                {"file_id": "f_done"},
            )
        )

        assert result["success"] is True
        assert result["status"] == "committed"
        assert result["file_id"] == "f_done"

    @pytest.mark.asyncio
    async def test_found_completed_in_index_only_processor(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """When the file is already completed in index_only_processor, returns immediately."""
        mock_index_only_processor.get_completed.return_value = {
            "status": "indexed",
            "file_id": "f_indexed",
        }

        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor,
                mock_index_only_processor,
                {"file_id": "f_indexed"},
            )
        )

        assert result["success"] is True
        assert result["status"] == "indexed"
        assert result["file_id"] == "f_indexed"

    @pytest.mark.asyncio
    async def test_custom_timeout_passed_to_processor(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """Custom timeout from args is forwarded to wait_for_completion."""
        mock_doc_processor.has_pending.return_value = True

        await _handle_wait_for_document(
            mock_doc_processor,
            mock_index_only_processor,
            {"file_id": "f1", "timeout": 60},
        )

        mock_doc_processor.wait_for_completion.assert_awaited_once_with("f1", 60)

    @pytest.mark.asyncio
    async def test_both_processors_none(self):
        """When both processors are None, file is not found."""
        result = _parse_result(await _handle_wait_for_document(None, None, {"file_id": "orphan"}))

        assert result["success"] is False
        assert "orphan" in result["error"]
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_index_only_checked_before_doc_processor(
        self, mock_doc_processor, mock_index_only_processor
    ):
        """Index-only processor is checked first (iteration order)."""
        mock_index_only_processor.has_pending.return_value = True
        mock_index_only_processor.wait_for_completion.return_value = {
            "status": "indexed",
            "file_id": "f_both",
        }
        mock_doc_processor.has_pending.return_value = True

        result = _parse_result(
            await _handle_wait_for_document(
                mock_doc_processor,
                mock_index_only_processor,
                {"file_id": "f_both"},
            )
        )

        assert result["success"] is True
        assert result["status"] == "indexed"
        # doc_processor.wait_for_completion should NOT be called
        mock_doc_processor.wait_for_completion.assert_not_awaited()


# ------------------------------------------------------------------ #
#  TestHandleDocumentStatus
# ------------------------------------------------------------------ #


class TestHandleDocumentStatus:
    """Tests for _handle_document_status handler."""

    def test_returns_documents_with_stage_progress(self, mock_engine):
        """Returns list of sources with stage_progress from storage adapter."""
        source = {
            "id": "src_001",
            "filename": "report.pdf",
            "status": "committed",
            "stage_progress": {
                "mcp_extraction": {
                    "total": 5,
                    "processed": 5,
                    "avg_ms": None,
                    "started_at": "2026-05-10T12:00:00+00:00",
                    "last_activity": "2026-05-10T12:01:00+00:00",
                    "completed_at": "2026-05-10T12:01:00+00:00",
                    "extras": None,
                }
            },
        }
        # list_sources returns (sources_list, total_count)
        mock_engine.storage_adapter.list_sources.return_value = ([source], 1)
        mock_engine.storage_adapter.list_sources_by_statuses.return_value = []

        result = _parse_result(_handle_document_status(mock_engine))

        assert result["success"] is True
        assert len(result["documents"]) == 1
        doc = result["documents"][0]
        assert doc["id"] == "src_001"
        assert doc["filename"] == "report.pdf"
        assert doc["status"] == "committed"
        assert "mcp_extraction" in doc["stage_progress"]
        assert doc["stage_progress"]["mcp_extraction"]["total"] == 5

    def test_empty_database_returns_empty_list(self, mock_engine):
        """Returns empty documents list when no sources exist."""
        # list_sources returns (sources_list, total_count)
        mock_engine.storage_adapter.list_sources.return_value = ([], 0)
        mock_engine.storage_adapter.list_sources_by_statuses.return_value = []

        result = _parse_result(_handle_document_status(mock_engine))

        assert result["success"] is True
        assert result["documents"] == []

    def test_awaiting_sources_carry_detection_fields(self, mock_engine):
        """Awaiting sources include detected_domain, confidence, file_id from the proposal."""
        # Normal page-1 scan returns the committed source.
        committed = {
            "id": "src_done",
            "filename": "done.pdf",
            "status": "committed",
            "stage_progress": {},
        }
        mock_engine.storage_adapter.list_sources.return_value = ([committed], 1)
        # The targeted awaiting query returns the parked source with a proposal.
        awaiting = {
            "id": "src_park",
            "filename": "parked.pdf",
            "status": "awaiting_confirmation",
            "stage_progress": {},
            "confirmation_required": True,
            "detection_proposal": {
                "ranking": [{"domain": "legal", "score": 3.1}],
                "confidence": 3.1,
                "detected_domain": "legal",
                "low_confidence": False,
            },
        }
        mock_engine.storage_adapter.list_sources_by_statuses.return_value = [awaiting]

        result = _parse_result(_handle_document_status(mock_engine))

        mock_engine.storage_adapter.list_sources_by_statuses.assert_called_once_with(
            statuses=["awaiting_confirmation"],
            database_name="default",
        )
        docs = {d["id"]: d for d in result["documents"]}
        assert "src_done" in docs
        park = docs["src_park"]
        assert park["status"] == "awaiting_confirmation"
        assert park["file_id"] == "src_park"
        assert park["detected_domain"] == "legal"
        assert park["confidence"] == 3.1
        assert park["confirmation_required"] is True

    def test_awaiting_low_confidence_flag_surfaced(self, mock_engine):
        """Low-confidence awaiting sources surface the low_confidence flag and fallback domain."""
        mock_engine.storage_adapter.list_sources.return_value = ([], 0)
        awaiting = {
            "id": "src_lc",
            "filename": "ambiguous.pdf",
            "status": "awaiting_confirmation",
            "stage_progress": {},
            "confirmation_required": True,
            "detection_proposal": {
                "ranking": [],
                "confidence": 0.1,
                "detected_domain": "generic",
                "low_confidence": True,
            },
        }
        mock_engine.storage_adapter.list_sources_by_statuses.return_value = [awaiting]

        result = _parse_result(_handle_document_status(mock_engine))

        park = next(d for d in result["documents"] if d["id"] == "src_lc")
        assert park["low_confidence"] is True
        # Empty ranking falls back to the stored detected_domain.
        assert park["detected_domain"] == "generic"

    def test_no_awaiting_sources_unchanged(self, mock_engine):
        """Non-awaiting sources are returned unaffected when awaiting query is empty."""
        mock_engine.storage_adapter.list_sources.return_value = (
            [{"id": "s1", "filename": "x", "status": "indexed", "stage_progress": {}}],
            1,
        )
        mock_engine.storage_adapter.list_sources_by_statuses.return_value = []

        result = _parse_result(_handle_document_status(mock_engine))

        assert result["success"] is True
        assert [d["id"] for d in result["documents"]] == ["s1"]


# ------------------------------------------------------------------ #
#  TestHandleExtractionTool
# ------------------------------------------------------------------ #


class TestHandleExtractionTool:
    """Tests for _handle_extraction_tool handler."""

    @pytest.mark.asyncio
    async def test_none_orchestrator_returns_write_mode_error(self):
        """When orchestrator is None, returns 'requires mcp.mode: write' error."""
        result = _parse_result(await _handle_extraction_tool(None, None, {}))

        assert result["success"] is False
        assert "mcp.mode: write" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_method_call(self):
        """Successful method call returns JSON from the method."""
        orchestrator = MagicMock()
        method = AsyncMock(return_value={"source_id": "s1", "total_chunks": 5})

        result = _parse_result(
            await _handle_extraction_tool(orchestrator, method, {"source_id": "s1"})
        )

        assert result["source_id"] == "s1"
        assert result["total_chunks"] == 5
        method.assert_awaited_once_with(source_id="s1")

    @pytest.mark.asyncio
    async def test_value_error_returns_tool_execution_failed(self):
        """ValueError from method yields 'Tool execution failed'."""
        orchestrator = MagicMock()
        method = AsyncMock(side_effect=ValueError("bad source"))

        result = _parse_result(
            await _handle_extraction_tool(orchestrator, method, {"source_id": "bad"})
        )

        assert result["success"] is False
        assert result["error"] == "Tool execution failed"

    @pytest.mark.asyncio
    async def test_key_error_returns_tool_execution_failed(self):
        """KeyError from method yields 'Tool execution failed'."""
        orchestrator = MagicMock()
        method = AsyncMock(side_effect=KeyError("missing_field"))

        result = _parse_result(await _handle_extraction_tool(orchestrator, method, {}))

        assert result["success"] is False
        assert result["error"] == "Tool execution failed"

    @pytest.mark.asyncio
    async def test_method_receives_all_args(self):
        """All args from the tool call are unpacked into the method."""
        orchestrator = MagicMock()
        method = AsyncMock(return_value={"ok": True})

        await _handle_extraction_tool(
            orchestrator,
            method,
            {"source_id": "s1", "chunk_group_index": 0, "entities_text": "E|A|P||0.9|S1|desc"},
        )

        method.assert_awaited_once_with(
            source_id="s1",
            chunk_group_index=0,
            entities_text="E|A|P||0.9|S1|desc",
        )

    @pytest.mark.asyncio
    async def test_empty_args_calls_method_with_no_kwargs(self):
        """Empty args dict calls method with no keyword arguments."""
        orchestrator = MagicMock()
        method = AsyncMock(return_value={"status": "ok"})

        result = _parse_result(await _handle_extraction_tool(orchestrator, method, {}))

        assert result["status"] == "ok"
        method.assert_awaited_once_with()


# ------------------------------------------------------------------ #
#  TestIndexOnlyPipelineSandbox
# ------------------------------------------------------------------ #


class TestIndexOnlyPipelineSandbox:
    """The index-only pipeline callback refuses paths that escape the sandbox."""

    @pytest.mark.asyncio
    async def test_symlink_escape_rejected(self, tmp_path):
        from chaoscypher_core.mcp.server import _create_pipeline_callback

        engine = MagicMock()
        engine.settings.current_database = "default"
        engine.settings.paths.data_dir = tmp_path
        engine.storage_adapter = MagicMock()

        sandbox = tmp_path / "uploads"
        sandbox.mkdir()
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("top secret")
        link = sandbox / "evil"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):  # fmt: skip
            pytest.skip("symlinks not supported on this filesystem")

        pipeline = _create_pipeline_callback(engine, extract_entities=False)

        with pytest.raises(Exception) as exc_info:
            await pipeline(
                file_path=str(link),
                file_id="f1",
                extraction_depth="full",
            )
        assert "PATH_OUTSIDE_SANDBOX" in str(exc_info.value) or "outside sandbox" in str(
            exc_info.value
        )


# ------------------------------------------------------------------ #
#  TestHandleAddDocumentSandbox
# ------------------------------------------------------------------ #


class TestHandleAddDocumentSandbox:
    """_handle_add_document rejects paths that escape the sandbox."""

    @pytest.mark.asyncio
    async def test_absolute_path_outside_sandbox_rejected(self, tmp_path):
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()
        sandbox = tmp_path / "uploads"
        sandbox.mkdir()

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": "/etc/passwd"},
                pipeline=pipeline,
                sandbox_dir=sandbox,
            )
        )

        assert result["success"] is False
        assert result.get("error_code") == "PATH_OUTSIDE_SANDBOX"

    @pytest.mark.asyncio
    async def test_url_paths_are_not_sandbox_checked(self, tmp_path):
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": "https://example.com/x.pdf"},
                pipeline=pipeline,
                sandbox_dir=tmp_path,
            )
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_path_outside_uploads_dir_rejected(self, tmp_path):
        """File paths outside the mcp sandbox dir must return PATH_OUTSIDE_SANDBOX."""
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()
        uploads = tmp_path / "mcp"
        uploads.mkdir()
        # Use a non-hidden filename so the dotfile check does not fire first;
        # only the sandbox containment check should fire here.
        secret = tmp_path / "credentials.txt"
        secret.write_text("QUEUE_PASSWORD=hunter2\n")

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": str(secret)},
                pipeline=pipeline,
                sandbox_dir=uploads,
            )
        )

        assert result["success"] is False
        assert result.get("error_code") == "PATH_OUTSIDE_SANDBOX"

    @pytest.mark.asyncio
    async def test_dotfile_inside_uploads_rejected(self, tmp_path):
        """Dotfiles inside the sandbox return a structured DOTFILE_REJECTED response."""
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()
        uploads = tmp_path / "mcp"
        uploads.mkdir()
        secret = uploads / ".secret"
        secret.write_text("x")

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": str(secret)},
                pipeline=pipeline,
                sandbox_dir=uploads,
            )
        )

        assert result["success"] is False
        assert result.get("error_code") == "DOTFILE_REJECTED"
        assert "Hidden files" in result["error"]
        # The filename should appear in the message, but not the full absolute path
        assert ".secret" in result["error"]
        assert str(secret) not in result["error"]


# ------------------------------------------------------------------ #
#  TestHandleAddDocumentSandboxWaitFalse
# ------------------------------------------------------------------ #


class TestHandleAddDocumentSandboxWaitFalse:
    """The sandbox guard must also fire on the wait=False (queued) path.

    Regression: the dotfile + sandbox check used to live only inside the
    ``elif wait:`` branch. A client passing ``wait=False`` skipped it
    entirely — the raw client path reached ``processor.add_document`` and,
    via the full pipeline, ``engine.add_document`` → ``Loaders.load_text``,
    an arbitrary-file-read bypass.
    """

    @pytest.mark.asyncio
    async def test_wait_false_path_outside_sandbox_rejected(self, tmp_path):
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()
        processor.add_document = AsyncMock(
            return_value={"success": True, "source_id": "x", "status": "indexed"}
        )
        uploads = tmp_path / "mcp"
        uploads.mkdir()
        secret = tmp_path / "credentials.txt"
        secret.write_text("QUEUE_PASSWORD=hunter2\n")

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": str(secret)},
                wait=False,
                pipeline=pipeline,
                sandbox_dir=uploads,
            )
        )

        assert result["success"] is False
        assert result.get("error_code") == "PATH_OUTSIDE_SANDBOX"
        # The raw path must never reach the processor queue.
        processor.add_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wait_false_dotfile_rejected(self, tmp_path):
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()
        processor.add_document = AsyncMock(return_value={"success": True})
        uploads = tmp_path / "mcp"
        uploads.mkdir()
        secret = uploads / ".secret"
        secret.write_text("x")

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": str(secret)},
                wait=False,
                pipeline=pipeline,
                sandbox_dir=uploads,
            )
        )

        assert result["success"] is False
        assert result.get("error_code") == "DOTFILE_REJECTED"
        processor.add_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wait_false_valid_path_resolved_before_queue(self, tmp_path):
        """A valid in-sandbox path is resolved to an absolute path before queueing."""
        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()
        processor.add_document = AsyncMock(
            return_value={"success": True, "source_id": "x", "status": "indexed"}
        )
        uploads = tmp_path / "mcp"
        uploads.mkdir()
        doc = uploads / "real.txt"
        doc.write_text("content")

        async def pipeline(**kwargs):
            return {"success": True, "source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": "real.txt"},  # relative to the sandbox
                wait=False,
                pipeline=pipeline,
                sandbox_dir=uploads,
            )
        )

        assert result["success"] is True
        processor.add_document.assert_awaited_once()
        queued_path = processor.add_document.await_args.args[0]
        assert queued_path == str(doc.resolve())


# ------------------------------------------------------------------ #
#  TestAddDocumentLogSanitization
# ------------------------------------------------------------------ #


class TestAddDocumentLogSanitization:
    """Pipeline exceptions must not leak tracebacks in logs."""

    @pytest.mark.asyncio
    async def test_pipeline_exception_logs_error_not_exception(self, tmp_path):
        import structlog.testing

        from chaoscypher_core.mcp.server import _handle_add_document

        processor = MagicMock()

        async def pipeline(**kwargs):
            raise RuntimeError("internal secret: DB connection string foo")

        # Create a real file so the sandbox check passes.
        sandbox = tmp_path
        (sandbox / "real.txt").write_text("content")

        with structlog.testing.capture_logs() as captured:
            result = _parse_result(
                await _handle_add_document(
                    processor,
                    {"file_path": "real.txt"},
                    pipeline=pipeline,
                    sandbox_dir=sandbox,
                )
            )

        # Client gets generic error with error_id only.
        assert result["success"] is False
        assert result["error"] == "Document processing failed"
        assert "error_id" in result
        # No secrets in the client payload.
        assert "internal secret" not in json.dumps(result)
        assert "DB connection string" not in json.dumps(result)

        # ERROR-level record: event name only, error_id + error_type, no exception msg.
        error_records = [r for r in captured if r["log_level"] == "error"]
        assert any(r["event"] == "add_document_pipeline_failed" for r in error_records)
        for r in error_records:
            # No secret strings ever appear in ERROR-level records.
            assert "internal secret" not in json.dumps(r, default=str)
            assert "DB connection string" not in json.dumps(r, default=str)
            # No traceback at ERROR level (exc_info is only passed at DEBUG).
            assert r.get("exc_info") is None

        # DEBUG-level traceback record carries exc_info=True with the same error_id.
        debug_records = [
            r
            for r in captured
            if r["log_level"] == "debug"
            and r.get("event") == "add_document_pipeline_failed_traceback"
        ]
        assert len(debug_records) == 1
        assert debug_records[0]["error_id"] == result["error_id"]


# ------------------------------------------------------------------ #
#  TestHandleAddDocumentAwaitingGate
# ------------------------------------------------------------------ #


class TestHandleAddDocumentAwaitingGate:
    """wait=True server-extraction returns awaiting_confirmation promptly."""

    @pytest.mark.asyncio
    async def test_parked_source_returns_awaiting_payload(self, tmp_path):
        from chaoscypher_core.mcp.server import _handle_add_document

        engine = MagicMock()
        engine.settings.current_database = "default"
        engine.storage_adapter.get_source.return_value = {
            "id": "f1",
            "filename": "report.pdf",
            "status": "awaiting_confirmation",
            "confirmation_required": True,
            "detection_proposal": {
                "ranking": [{"domain": "medical", "score": 2.7}],
                "confidence": 2.7,
                "detected_domain": "medical",
                "low_confidence": False,
            },
        }

        processor = MagicMock()
        sandbox = tmp_path
        (sandbox / "real.txt").write_text("content")

        async def pipeline(**kwargs):
            # The inline full pipeline parked the source: it does not raise and
            # returns a benign result. The handler must consult the SourceRow.
            return {"source_id": kwargs["file_id"]}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": "real.txt"},
                wait=True,
                pipeline=pipeline,
                sandbox_dir=sandbox,
                engine=engine,
            )
        )

        assert result["status"] == "awaiting_confirmation"
        assert result["detected_domain"] == "medical"
        assert result["confidence"] == 2.7
        assert result["file_id"] == result["source_id"]
        assert "next_steps" in result
        assert "confirm_extraction" in result["next_steps"]

    @pytest.mark.asyncio
    async def test_auto_confirm_bypasses_park(self, tmp_path):
        from chaoscypher_core.mcp.server import _handle_add_document

        engine = MagicMock()
        engine.settings.current_database = "default"
        # auto_confirm proceeded: the source committed, not parked.
        engine.storage_adapter.get_source.return_value = {
            "id": "f1",
            "status": "committed",
        }

        processor = MagicMock()
        sandbox = tmp_path
        (sandbox / "real.txt").write_text("content")

        async def pipeline(**kwargs):
            assert kwargs["auto_confirm"] is True
            return {"source_id": kwargs["file_id"], "nodes": [], "edges": []}

        result = _parse_result(
            await _handle_add_document(
                processor,
                {"file_path": "real.txt"},
                wait=True,
                auto_confirm=True,
                pipeline=pipeline,
                sandbox_dir=sandbox,
                engine=engine,
            )
        )

        assert result.get("status") != "awaiting_confirmation"
        assert result["success"] is True
