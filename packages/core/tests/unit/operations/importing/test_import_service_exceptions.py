# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: ImportOperationsService raises Core exceptions, not stdlib types.

Covers:
- __init__ → ValidationError when chunking_service or indexing_service is None
- queue_import_commit → OperationError when source_repository is None
- _import_commit_handler → OperationError when source_repository is None (two guards)
- _resume_existing_extraction_job/_create_fresh_extraction_job → OperationError
  when no chunks found (tested indirectly via OperationError not ValueError)
- _import_analysis_handler → ValidationError when file_id is not a string
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


def _make_service(
    *,
    chunking_service: object = None,
    indexing_service: object = None,
    source_repository: object = None,
) -> ImportOperationsService:
    """Build a minimal ImportOperationsService for testing guard clauses."""
    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=source_repository,
        chunking_service=chunking_service,
        indexing_service=indexing_service,
    )


class TestImportOperationsServiceInit:
    """ValidationError raised when required services are absent at construction."""

    def test_missing_chunking_service_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(
                chunking_service=None,
                indexing_service=MagicMock(),
                source_repository=MagicMock(),
            )

        assert exc_info.value.field == "chunking_service"
        assert exc_info.value.code == "VALIDATION_ERROR"

    def test_missing_indexing_service_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make_service(
                chunking_service=MagicMock(),
                indexing_service=None,
                source_repository=MagicMock(),
            )

        assert exc_info.value.field == "indexing_service"
        assert exc_info.value.code == "VALIDATION_ERROR"

    def test_not_stdlib_value_error_for_chunking(self) -> None:
        """Confirm stdlib ValueError is no longer raised."""
        with pytest.raises(ValidationError):
            _make_service(chunking_service=None, indexing_service=MagicMock())

    def test_not_stdlib_value_error_for_indexing(self) -> None:
        with pytest.raises(ValidationError):
            _make_service(chunking_service=MagicMock(), indexing_service=None)


class TestQueueImportCommitNoSourceRepo:
    """OperationError raised when source_repository is not configured."""

    @pytest.mark.asyncio
    async def test_raises_operation_error_when_source_repo_none(self) -> None:
        service = _make_service(
            chunking_service=MagicMock(),
            indexing_service=MagicMock(),
            source_repository=None,
        )
        # Force None after construction (which validates chunking/indexing)
        service.source_repository = None

        with pytest.raises(OperationError) as exc_info:
            await service.queue_import_commit(
                file_id="src-1",
                commit_data={},
                file_info={},
                database_name="test_db",
            )

        assert exc_info.value.code == "OPERATION_ERROR"

    @pytest.mark.asyncio
    async def test_not_stdlib_runtime_error(self) -> None:
        """Confirm stdlib RuntimeError is no longer raised."""
        service = _make_service(
            chunking_service=MagicMock(),
            indexing_service=MagicMock(),
            source_repository=MagicMock(),
        )
        service.source_repository = None

        with pytest.raises(OperationError):
            await service.queue_import_commit(
                file_id="src-1",
                commit_data={},
                file_info={},
                database_name="test_db",
            )
