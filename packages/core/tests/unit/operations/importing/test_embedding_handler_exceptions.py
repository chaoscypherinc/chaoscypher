# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: embedding_handler raises ValidationError for malformed inputs.

Covers:
- handle_embed_chunks → ValidationError when file_info is missing
- handle_embed_chunks → ValidationError when source_id is not a string
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.embedding_handler import (
    handle_embed_chunks,
)


class TestEmbedChunksValidation:
    """ValidationError raised for missing or wrongly-typed inputs."""

    @pytest.mark.asyncio
    async def test_missing_file_info_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_embed_chunks(
                data={"source_id": "src-1"},  # no file_info
                source_repository=MagicMock(),
                indexing_service=AsyncMock(),
            )

        assert exc_info.value.field == "file_info"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_non_string_source_id_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_embed_chunks(
                data={"source_id": 999, "file_info": {"filename": "doc.pdf"}},
                source_repository=MagicMock(),
                indexing_service=AsyncMock(),
            )

        assert exc_info.value.field == "source_id"
        assert exc_info.value.code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_none_file_info_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            await handle_embed_chunks(
                data={"source_id": "src-1", "file_info": None},
                source_repository=MagicMock(),
                indexing_service=AsyncMock(),
            )

        assert exc_info.value.field == "file_info"

    @pytest.mark.asyncio
    async def test_not_stdlib_value_error(self) -> None:
        """Confirm stdlib ValueError is no longer raised."""
        with pytest.raises(ValidationError):
            await handle_embed_chunks(
                data={},
                source_repository=MagicMock(),
                indexing_service=AsyncMock(),
            )

    @pytest.mark.asyncio
    async def test_not_stdlib_type_error(self) -> None:
        """Confirm stdlib TypeError is no longer raised for non-string source_id."""
        with pytest.raises(ValidationError):
            await handle_embed_chunks(
                data={"source_id": None, "file_info": {}},
                source_repository=MagicMock(),
                indexing_service=AsyncMock(),
            )
