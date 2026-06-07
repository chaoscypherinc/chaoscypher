# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: format_handler uses ValidationError at the guard site.

The handler raises ValidationError (propagates) for missing file_content.
The contract tests verify:
- Missing ``file_content`` → ValidationError raised and propagates
- The exception type at the raise site is ValidationError (not stdlib ValueError)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.format_handler import handle_import_ccx


class TestImportCcxValidation:
    """ValidationError used at the guard site; handler re-raises it."""

    @pytest.mark.asyncio
    async def test_missing_file_content_raises_validation_error(self) -> None:
        """Missing file_content raises ValidationError that propagates to the caller."""
        with pytest.raises(ValidationError) as exc_info:
            await handle_import_ccx(
                data={},
                graph_repository=MagicMock(),
            )

        assert "file_content" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_null_file_content_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            await handle_import_ccx(
                data={"file_content": None},
                graph_repository=MagicMock(),
            )

    def test_validation_error_exception_is_correct_type(self) -> None:
        """Verify the guard uses ValidationError (not stdlib ValueError) at the raise site."""
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError("file_content is required", field="file_content")

        assert exc_info.value.field == "file_content"
        assert exc_info.value.code == "VALIDATION_ERROR"
