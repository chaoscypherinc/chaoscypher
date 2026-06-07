# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: indexing_handler raises ValidationError for malformed inputs.

Covers:
- handle_index_document → ValidationError when file_info is missing
- handle_index_document → ValidationError when file_id is not a string
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.indexing_handler import (
    handle_index_document,
)


class TestIndexDocumentValidation:
    """ValidationError raised for missing or wrongly-typed inputs."""

    @pytest.mark.asyncio
    async def test_missing_file_info_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_index_document(
                data={"file_id": "src-1"},  # no file_info
                source_repository=MagicMock(),
                chunking_service=AsyncMock(),
            )

        assert exc_info.value.field == "file_info"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_non_string_file_id_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_index_document(
                data={"file_id": 12345, "file_info": {"filepath": "/tmp/f.pdf"}},
                source_repository=MagicMock(),
                chunking_service=AsyncMock(),
            )

        assert exc_info.value.field == "file_id"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_none_file_info_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_index_document(
                data={"file_id": "src-1", "file_info": None},
                source_repository=MagicMock(),
                chunking_service=AsyncMock(),
            )

        assert exc_info.value.field == "file_info"

    @pytest.mark.asyncio
    async def test_not_stdlib_value_error(self) -> None:
        """Confirm stdlib ValueError is no longer raised."""
        with pytest.raises(ValidationError):
            await handle_index_document(
                data={},
                source_repository=MagicMock(),
                chunking_service=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_not_stdlib_type_error(self) -> None:
        """Confirm stdlib TypeError is no longer raised for non-string file_id."""
        with pytest.raises(ValidationError):
            await handle_index_document(
                data={"file_id": [], "file_info": {}},
                source_repository=MagicMock(),
                chunking_service=AsyncMock(),
            )
