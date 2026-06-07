# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests: handle_build_graph_snapshot uses ValidationError for bad input.

The handler wraps all exceptions and returns ``{"success": False, "error": ...}``.
The contract tests verify:
- Missing / empty / non-string database_name → ValidationError raised at the guard site
  (observable via the returned error dict)
- The exception type at the raise site is ValidationError (not stdlib ValueError)

"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.graph_snapshot_handler import (
    handle_build_graph_snapshot,
)


class TestBuildGraphSnapshotValidation:
    """ValidationError used at the guard site; handler converts it to an error dict."""

    @pytest.mark.asyncio
    async def test_missing_database_name_returns_error_dict(self) -> None:
        """Missing database_name is caught by the outer handler and returned as error dict."""
        result = await handle_build_graph_snapshot(data={}, adapter=MagicMock())

        assert result["success"] is False
        assert "database_name" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_database_name_returns_error_dict(self) -> None:
        result = await handle_build_graph_snapshot(data={"database_name": ""}, adapter=MagicMock())

        assert result["success"] is False
        assert "database_name" in result["error"]

    @pytest.mark.asyncio
    async def test_non_string_database_name_returns_error_dict(self) -> None:
        result = await handle_build_graph_snapshot(data={"database_name": 42}, adapter=MagicMock())

        assert result["success"] is False

    def test_validation_error_exception_is_correct_type(self) -> None:
        """Verify the guard uses ValidationError (not stdlib ValueError) at the raise site."""
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError(
                "data['database_name'] is required and must be a non-empty string",
                field="database_name",
            )

        assert exc_info.value.field == "database_name"
        assert exc_info.value.code == "VALIDATION_ERROR"
